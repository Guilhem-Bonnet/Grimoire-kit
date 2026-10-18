"""W4-04 : ``grimoire mission report`` (coût, acceptance, palier, preuves, rejeu).

Avant, aucune commande ne rassemblait une mission : le coût vivait dans les
tentatives du ledger, le résumé de cascade dans le journal de traces, l'usage
replié du journal de session ailleurs, les preuves dans l'EvidenceService. Ces
tests posent le contrat : chaque tentative porte ``cost.status``, l'issue
d'acceptance et ``trace_id`` ; aucun coût connu, aucun total numérique ; deux
exécutions donnent les mêmes octets ; lecture seule.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.standard_generation import TRACES_DIR
from grimoire.evidence import EvidenceItem, EvidenceKind, EvidenceProfile, EvidenceService
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.report import DEFAULT_LEDGER_RELPATH, build_mission_report, render_json, render_markdown
from grimoire.traces.ledger import DISPATCH_OUTCOME_TAG, TraceLedger
from grimoire.traces.schemas import TraceOutcome

runner = CliRunner()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _attempt_payload(n: int, verdict: str, *, cost: float | None, acceptance_status: Any = "executed", **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "attempt": n,
        "tier": "cheap",
        "provider": "local",
        "model": "m1",
        "exit_code": 0,
        "duration_s": 0.1,
        "checks": [{"cmd": "true", "ok": verdict == "green"}],
        "verdict": verdict,
        "cost_usd": cost,
        "task_type": "implementation",
        "verifiability": "V0",
        "start_tier": "cheap",
        "start_tier_reason": "plancher",
        "acceptance_status": acceptance_status,
    }
    payload.update(extra)
    return payload


def _outcome_trace(root: Path, task_id: str, *, resolved: bool, replay: str, cost: float | None = None) -> str:
    trace = TraceLedger(root / TRACES_DIR).record(
        run_id="dispatch-x",
        workflow_instance_id="",
        mission_id="",
        task_id=task_id,
        recipe_id="grimoire.dispatch",
        outcome=TraceOutcome.SUCCESS if resolved else TraceOutcome.FAILURE,
        started_at=_now(),
        token_usage={"estimated_cost_usd": cost, "unpriced_calls": 0 if cost is not None else 1},
        tags=[DISPATCH_OUTCOME_TAG, "class:V0", "tier:cheap", f"resolved:{'true' if resolved else 'false'}", f"replay:{replay}"],
    )
    return trace.id


def _mission(root: Path, titles: tuple[str, ...] = ("Tâche A",)) -> tuple[MissionLedger, str, list[str]]:
    ledger = MissionLedger(root / DEFAULT_LEDGER_RELPATH)
    mission = ledger.create_mission("Mission de test", origin="user")
    ids = [ledger.create_task(mission.id, title, acceptance=("la suite passe",)).id for title in titles]
    return ledger, mission.id, ids


def _dispatch(ledger: MissionLedger, task_id: str, payload: dict[str, Any]) -> str:
    return ledger.append_event("task.dispatched", task_id, "task", "cli", payload).id


def _report(root: Path, mission_id: str) -> dict[str, Any]:
    report = build_mission_report(root, mission_id)
    assert report is not None
    return report


# ── Contrat : coût, acceptance, trace_id par tentative ─────────────────────────


def test_each_attempt_carries_cost_status_acceptance_and_trace_id(tmp_path: Path) -> None:
    ledger, mission_id, (task_id,) = _mission(tmp_path)
    event_id = _dispatch(ledger, task_id, _attempt_payload(1, "green", cost=0.25))
    trace_id = _outcome_trace(tmp_path, task_id, resolved=True, replay=task_id, cost=0.25)

    [attempt] = _report(tmp_path, mission_id)["tasks"][0]["attempts"]

    assert attempt["cost"] == {"usd": 0.25, "status": "exact", "unpriced_calls": 0}
    assert attempt["acceptance"]["status"] == "executed"
    assert attempt["acceptance"]["outcome"] == "passed"
    assert attempt["trace_id"] == trace_id
    assert attempt["trace_link"] == "matched"
    assert attempt["source"] == f"ledger:events.jsonl#{event_id}"
    assert attempt["tier"] == "cheap" and attempt["provider"] == "local" and attempt["start_tier"] == "cheap"


def test_unknown_attempt_cost_is_never_zero(tmp_path: Path) -> None:
    ledger, mission_id, (task_id,) = _mission(tmp_path)
    _dispatch(ledger, task_id, _attempt_payload(1, "rate_limit", cost=None, acceptance_status=None, checks=[]))

    [attempt] = _report(tmp_path, mission_id)["tasks"][0]["attempts"]

    assert attempt["cost"]["usd"] is None
    assert attempt["cost"]["status"] == "unknown"
    assert attempt["acceptance"] == {"status": "not_run", "outcome": "not_run", "checks_total": 0, "checks_passed": 0}


def test_event_older_than_acceptance_status_is_unrecorded_not_completed(tmp_path: Path) -> None:
    ledger, mission_id, (task_id,) = _mission(tmp_path)
    payload = _attempt_payload(1, "red", cost=None)
    del payload["acceptance_status"]
    _dispatch(ledger, task_id, payload)

    [attempt] = _report(tmp_path, mission_id)["tasks"][0]["attempts"]

    assert attempt["acceptance"]["status"] == "unrecorded"
    assert attempt["acceptance"]["outcome"] == "failed"


# ── Aucun coût connu : aucun total numérique ──────────────────────────────────


def test_no_known_cost_gives_no_numeric_total_and_says_unknown(tmp_path: Path) -> None:
    ledger, mission_id, (task_id,) = _mission(tmp_path)
    _dispatch(ledger, task_id, _attempt_payload(1, "green", cost=None))
    report = _report(tmp_path, mission_id)

    assert report["cost"]["usd"] is None
    assert report["cost"]["status"] == "unknown"
    assert report["tasks"][0]["cost"]["usd"] is None
    markdown = render_markdown(report)
    assert "coût inconnu" in markdown
    assert "0.0000" not in markdown


def test_mission_without_any_data_is_unknown_not_zero(tmp_path: Path) -> None:
    _, mission_id, _ = _mission(tmp_path)
    report = _report(tmp_path, mission_id)

    assert report["cost"]["usd"] is None and report["cost"]["status"] == "unknown"
    assert "coût inconnu" in render_markdown(report)


def test_partial_cost_is_a_lower_bound_and_names_the_task_without_data(tmp_path: Path) -> None:
    ledger, mission_id, (priced, silent) = _mission(tmp_path, ("Tâche chiffrée", "Tâche muette"))
    _dispatch(ledger, priced, _attempt_payload(1, "green", cost=0.4))

    report = _report(tmp_path, mission_id)

    assert report["cost"]["status"] == "lower_bound"
    assert report["cost"]["usd"] == pytest.approx(0.4)
    assert report["cost"]["tasks_without_data"] == 1
    assert ">= 0.4000 USD" in render_markdown(report)
    by_id = {t["id"]: t for t in report["tasks"]}
    assert by_id[silent]["cost"]["source"] == "none" and by_id[silent]["cost"]["usd"] is None


def test_transcript_view_and_dispatch_view_are_never_added(tmp_path: Path) -> None:
    from grimoire.traces.schemas import TokenUsage, TraceRecord

    ledger, mission_id, (task_id,) = _mission(tmp_path)
    _dispatch(ledger, task_id, _attempt_payload(1, "green", cost=10.0))
    TraceLedger(tmp_path / TRACES_DIR).append_new(
        [
            TraceRecord(
                id="TRC-usage-1",
                run_id="usage-1",
                workflow_instance_id="",
                mission_id="",
                task_id=task_id,
                recipe_id="grimoire.model-call.transcript",
                outcome=TraceOutcome.SUCCESS,
                started_at=_now(),
                completed_at=_now(),
                token_usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15, estimated_cost_usd=0.5, unpriced_calls=0),
                tags=("usage.source:claude-code-transcript", "usage.cache_read:0", "usage.cache_creation:0"),
            )
        ]
    )

    task = _report(tmp_path, mission_id)["tasks"][0]

    assert task["cost"]["source"] == "transcript"
    assert task["cost"]["usd"] == pytest.approx(0.5)
    assert task["cost"]["views"]["dispatch"]["usd"] == pytest.approx(10.0)
    assert task["usage"]["calls"] == 1


# ── Lien tentative -> trace : exact ou absent ─────────────────────────────────


def test_two_cascades_each_link_to_their_own_trace(tmp_path: Path) -> None:
    ledger, mission_id, (task_id,) = _mission(tmp_path)
    _dispatch(ledger, task_id, _attempt_payload(1, "red", cost=0.1))
    first = _outcome_trace(tmp_path, task_id, resolved=False, replay="k")
    _dispatch(ledger, task_id, _attempt_payload(1, "red", cost=0.1))
    _dispatch(ledger, task_id, _attempt_payload(2, "green", cost=0.2, tier="mid"))
    second = _outcome_trace(tmp_path, task_id, resolved=True, replay="k")

    attempts = _report(tmp_path, mission_id)["tasks"][0]["attempts"]

    assert [(a["cascade"], a["attempt"], a["trace_id"]) for a in attempts] == [(1, 1, first), (2, 1, second), (2, 2, second)]


def test_a_cascade_whose_trace_was_never_written_is_unlinked_not_guessed(tmp_path: Path) -> None:
    ledger, mission_id, (task_id,) = _mission(tmp_path)
    _dispatch(ledger, task_id, _attempt_payload(1, "red", cost=0.1))  # trace jamais écrite (journal indisponible)
    _dispatch(ledger, task_id, _attempt_payload(1, "green", cost=0.1))
    only = _outcome_trace(tmp_path, task_id, resolved=True, replay="k")

    report = _report(tmp_path, mission_id)
    attempts = report["tasks"][0]["attempts"]

    assert attempts[0]["trace_id"] is None and attempts[0]["trace_link"] == "none"
    assert attempts[1]["trace_id"] == only
    assert report["summary"]["cascades_without_trace"] == 1


# ── Rejeu ─────────────────────────────────────────────────────────────────────


def test_replay_groups_runs_by_key_across_tasks(tmp_path: Path) -> None:
    ledger, mission_id, (a, b) = _mission(tmp_path, ("Run 1", "Run 2"))
    _dispatch(ledger, a, _attempt_payload(1, "green", cost=0.1))
    _outcome_trace(tmp_path, a, resolved=True, replay="bp:node")
    _dispatch(ledger, b, _attempt_payload(1, "red", cost=0.1))
    _outcome_trace(tmp_path, b, resolved=False, replay="bp:node")

    assert _report(tmp_path, mission_id)["replay"] == [{"key": "bp:node", "runs": 2, "resolved": 1, "all_resolved": False}]


# ── Preuves ───────────────────────────────────────────────────────────────────


def test_evidence_packs_and_verdicts_are_listed_with_their_source(tmp_path: Path) -> None:
    _, mission_id, (task_id,) = _mission(tmp_path)
    service = EvidenceService(tmp_path / "_grimoire-runtime-output" / "evidence")
    item = EvidenceItem(id="ev-1", kind=EvidenceKind.TEST, uri="tests/test_x.py", digest="sha256:abc", summary="la suite passe")
    pack = service.create_pack(task_id, EvidenceProfile.LIGHT, [item], acceptance=("la suite passe",))
    verdict = service.verify(pack, acceptance=("la suite passe",))

    evidence = _report(tmp_path, mission_id)["tasks"][0]["evidence"]

    assert [p["id"] for p in evidence["packs"]] == [pack.id]
    assert evidence["packs"][0]["source"] == f"evidence:packs.jsonl#{pack.id}"
    assert evidence["verdicts"][0]["id"] == verdict.id
    assert evidence["verdicts"][0]["verdict"] == verdict.verdict.value


# ── Déterminisme, lecture seule, encodage ──────────────────────────────────────


def test_two_runs_are_identical_byte_for_byte(tmp_path: Path) -> None:
    ledger, mission_id, (a, b) = _mission(tmp_path, ("Tâche A", "Tâche B"))
    _dispatch(ledger, a, _attempt_payload(1, "red", cost=0.1))
    _dispatch(ledger, a, _attempt_payload(2, "green", cost=None, tier="mid"))
    _outcome_trace(tmp_path, a, resolved=True, replay=a)
    _dispatch(ledger, b, _attempt_payload(1, "timeout", cost=None, acceptance_status=None, checks=[]))

    first, second = _report(tmp_path, mission_id), _report(tmp_path, mission_id)

    assert render_json(first) == render_json(second)
    assert render_markdown(first) == render_markdown(second)
    cli = [runner.invoke(app, ["mission", "report", mission_id, "--project-root", str(tmp_path)]) for _ in range(2)]
    assert cli[0].exit_code == 0 and cli[0].stdout == cli[1].stdout == render_markdown(first)


def test_report_creates_nothing_on_disk(tmp_path: Path) -> None:
    ledger, mission_id, _ = _mission(tmp_path)
    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))

    report = _report(tmp_path, mission_id)

    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*")) == before
    assert report["sources"]["traces"] is None and report["sources"]["evidence"] is None
    assert "absentes" in render_markdown(report)
    del ledger


def test_lone_surrogate_in_a_title_does_not_break_the_output(tmp_path: Path) -> None:
    ledger = MissionLedger(tmp_path / DEFAULT_LEDGER_RELPATH)
    mission = ledger.create_mission("Titre MARQUE cassé", origin="user")
    events = tmp_path / DEFAULT_LEDGER_RELPATH / "events.jsonl"
    # Un journal édité à la main ou écrit par un autre outil : l'échappement JSON d'un substitut isolé,
    # que json.loads accepte et que ni stdout ni la frontière PyO3 ne savent encoder.
    events.write_text(events.read_text(encoding="utf-8").replace("MARQUE", "\\" + "ud800"), encoding="utf-8")
    report = _report(tmp_path, mission.id)
    assert chr(0xD800) in report["mission"]["title"]

    for text in (render_json(report), render_markdown(report)):
        text.encode("utf-8")  # lève UnicodeEncodeError si un substitut isolé survit


def test_pipe_in_a_title_does_not_split_a_table_cell(tmp_path: Path) -> None:
    ledger, mission_id, (task_id,) = _mission(tmp_path, ("a | b",))
    _dispatch(ledger, task_id, _attempt_payload(1, "green", cost=0.1, provider="x|y"))

    assert "x\\|y" in render_markdown(_report(tmp_path, mission_id))


# ── CLI ───────────────────────────────────────────────────────────────────────


def test_cli_json_matches_the_builder_and_unknown_mission_exits_1(tmp_path: Path) -> None:
    ledger, mission_id, (task_id,) = _mission(tmp_path)
    _dispatch(ledger, task_id, _attempt_payload(1, "green", cost=0.3))

    ok = runner.invoke(app, ["mission", "report", mission_id, "--json", "--project-root", str(tmp_path)])
    assert ok.exit_code == 0, ok.output
    assert json.loads(ok.stdout) == json.loads(render_json(_report(tmp_path, mission_id)))

    unknown = runner.invoke(app, ["mission", "report", "MIS-fantome-001", "--project-root", str(tmp_path)])
    assert unknown.exit_code == 1

    empty = tmp_path / "vide"
    empty.mkdir()
    absent = runner.invoke(app, ["mission", "report", mission_id, "--project-root", str(empty)])
    assert absent.exit_code == 1
    assert not (empty / "_grimoire-runtime-output").exists()


# ── De bout en bout : une vraie cascade de dispatch ──────────────────────────


def _registry(root: Path, invocation: str) -> None:
    path = root / "_grimoire" / "standard" / "llm-provider-registry.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        dedent(
            f"""\
            $schema: "grimoire-llm-provider-registry/v1"
            metadata: {{project: "demo", owner: "", policy: "x"}}
            providers:
              - id: "local"
                enabled: true
                provider_type: "hosted"
                allowed_capabilities: ["chat", "code"]
                default_models: ["local-model"]
                currency: "api"
                invocation: "{invocation}"
                models:
                  - id: "local-model"
                    tier: "cheap"
                fallback_order: []
            routing:
              default_provider: ""
              default_fallback_chain: []
              require_capability_match: true
              require_data_policy_match: true
            """
        ),
        encoding="utf-8",
    )


def test_real_dispatch_cascades_are_reported_with_their_real_trace(tmp_path: Path) -> None:
    script = tmp_path / "noop.py"
    script.write_text("pass\n", encoding="utf-8")
    _registry(tmp_path, f"{Path(sys.executable).as_posix()} {script.as_posix()} {{prompt}} --model {{model}}")
    added = runner.invoke(app, ["task", "add", "Tâche déléguée", "-a", "la suite de tests passe", "--owner", "amelia", "--project-root", str(tmp_path)])
    assert added.exit_code == 0, added.output
    task_id = next(w for w in (added.stdout + added.stderr).split() if w.startswith("GAO-"))

    def dispatch() -> int:
        argv = ["task", "dispatch", task_id, "--check", "test -f marker.txt", "--max-tier", "cheap", "--project-root", str(tmp_path)]
        return runner.invoke(app, argv).exit_code

    assert dispatch() == 1  # rouge : marker.txt n'existe pas
    (tmp_path / "marker.txt").write_text("ok", encoding="utf-8")
    assert dispatch() == 0  # vert

    task = MissionLedger(tmp_path / DEFAULT_LEDGER_RELPATH).get_task(task_id)
    assert task is not None
    report = _report(tmp_path, task.mission_id)
    attempts = report["tasks"][0]["attempts"]
    traces = {t.id for t in TraceLedger(tmp_path / TRACES_DIR).list_traces() if DISPATCH_OUTCOME_TAG in t.tags}

    assert [(a["cascade"], a["verdict"], a["acceptance"]["outcome"]) for a in attempts] == [(1, "red", "failed"), (2, "green", "passed")]
    assert all(a["trace_id"] in traces for a in attempts)
    assert attempts[0]["trace_id"] != attempts[1]["trace_id"]
    assert all(a["cost"]["status"] == "unknown" for a in attempts)  # le fournisseur factice ne rend aucun coût
    assert report["cost"]["usd"] is None
    assert report["replay"] == [{"key": task_id, "runs": 2, "resolved": 1, "all_resolved": False}]
