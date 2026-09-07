"""``grimoire task record-model-call`` / ``task handoff`` (#246, cinq DOIT).

AG-LLM-004 (« les appels modèle DOIVENT être journalisés : modèle, rôle,
coût, latence, erreur ») et AG-ORC-005 (« les communications inter-agents
DOIVENT être tracées si elles influencent une décision ») n'avaient ni l'un
ni l'autre de mécanisme réel avant cette passe : `TraceRecord.model` et
`.token_usage` existaient dans le schéma sans qu'aucun appelant ne les
peuple, et `tools.handoff.build_handoff` n'avait aucun appelant du tout.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.cli.cmd_task import task_app

runner = CliRunner()

TRACES = Path("_grimoire-output/traces")
LEDGER = Path("_grimoire-runtime-output/ledger")


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    return tmp_path


def test_record_model_call_writes_model_role_cost_latency_error(projet: Path) -> None:
    res = runner.invoke(
        task_app,
        [
            "record-model-call", "GAO-demo",
            "--model", "claude-sonnet-4.6",
            "--tokens-in", "2000",
            "--tokens-out", "800",
            "--agent-id", "dev",
            "--latency-ms", "1234.5",
            "--project-root", str(projet),
        ],
    )
    assert res.exit_code == 0, res.output

    from grimoire.traces.ledger import TraceLedger

    ledger = TraceLedger(projet / TRACES)
    traces = ledger.list_traces(task_id="GAO-demo")
    assert len(traces) == 1
    trace = traces[0]
    assert trace.model == "claude-sonnet-4.6"
    assert trace.agent_id == "dev"
    assert trace.token_usage.prompt_tokens == 2000
    assert trace.token_usage.completion_tokens == 800
    assert trace.token_usage.estimated_cost_usd > 0
    assert trace.latency_ms == 1234.5
    assert trace.error_count == 0


def test_record_model_call_flags_error(projet: Path) -> None:
    res = runner.invoke(
        task_app,
        ["record-model-call", "GAO-demo", "--model", "opus", "--error", "--project-root", str(projet)],
    )
    assert res.exit_code == 0, res.output

    from grimoire.traces.ledger import TraceLedger
    from grimoire.traces.schemas import TraceOutcome

    trace = TraceLedger(projet / TRACES).list_traces(task_id="GAO-demo")[0]
    assert trace.error_count == 1
    assert trace.outcome is TraceOutcome.FAILURE


def test_record_model_call_unknown_model_costs_nothing_invented(projet: Path) -> None:
    res = runner.invoke(
        task_app,
        ["record-model-call", "GAO-demo", "--model", "totally-unknown-model", "--tokens-in", "500", "--project-root", str(projet)],
    )
    assert res.exit_code == 0, res.output
    from grimoire.traces.ledger import TraceLedger

    trace = TraceLedger(projet / TRACES).list_traces(task_id="GAO-demo")[0]
    assert trace.token_usage.estimated_cost_usd == 0.0


def _capsule(projet: Path, *, event: str = "SubagentStop") -> Path:
    path = projet / "capsule.json"
    path.write_text(
        json.dumps({
            "event": event,
            "agent": "amelia",
            "task": "brancher les accesseurs",
            "outputPreview": "onze retirés, trois branchés",
            "explicitFailure": False,
            "timestamp": "2026-09-07T12:00:00+00:00",
        }),
        encoding="utf-8",
    )
    return path


def test_handoff_derives_packet_and_traces_it_to_the_ledger(projet: Path) -> None:
    capsule = _capsule(projet)
    res = runner.invoke(task_app, ["handoff", "GAO-demo", str(capsule), "--project-root", str(projet)])
    assert res.exit_code == 0, res.output
    assert "amelia" in res.output

    # Le Mission Ledger est append-only JSONL : lire le fichier brut suffit à
    # prouver l'écriture sans dépendre d'un accesseur de lecture.
    raw = (projet / LEDGER / "events.jsonl").read_text(encoding="utf-8").strip().splitlines()
    payloads = [json.loads(line) for line in raw]
    handoffs = [p for p in payloads if p["event_type"] == "handoff" and p["entity_id"] == "GAO-demo"]
    assert len(handoffs) == 1
    assert handoffs[0]["payload"]["pattern"] == "ORC-03"
    assert handoffs[0]["actor_id"] == "amelia"


def test_handoff_json_output_is_the_raw_packet(projet: Path) -> None:
    capsule = _capsule(projet)
    res = runner.invoke(app, ["--output", "json", "task", "handoff", "GAO-demo", str(capsule), "--project-root", str(projet)])
    assert res.exit_code == 0, res.output
    packet = json.loads(res.output)
    assert packet["contract"] == "handoff-packet"
    assert packet["pattern"] == "ORC-03"


def test_handoff_refuses_a_non_subagent_stop_capsule(projet: Path) -> None:
    capsule = _capsule(projet, event="SomethingElse")
    res = runner.invoke(task_app, ["handoff", "GAO-demo", str(capsule), "--project-root", str(projet)])
    assert res.exit_code == 1
    assert "SubagentStop" in res.output


def test_handoff_refuses_an_unreadable_capsule(projet: Path) -> None:
    res = runner.invoke(task_app, ["handoff", "GAO-demo", str(projet / "does-not-exist.json"), "--project-root", str(projet)])
    assert res.exit_code == 1
