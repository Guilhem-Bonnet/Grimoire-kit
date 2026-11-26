"""Épique #1280, lot C3 : le pas de repli et ``task usage`` lisent le journal d'une session Copilot.

Journal composé depuis les fixtures ``tests/fixtures/hosts/copilot/copilot-harness/`` ; non vérifié sur un
vrai Copilot. Sans emplacement configuré, rien n'est lu et le pas le dit.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from grimoire.cli.cmd_task_usage import _usage_of
from grimoire.missions.pause import CONTROL_HOME_ENV
from grimoire.missions.schemas import TaskClaim, TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.usage_fold import fold_pending
from grimoire.traces.copilot_usage import LOGS_ENV, UNVERIFIED_NOTE
from grimoire.traces.ledger import TraceLedger

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "hosts" / "copilot" / "copilot-harness"
SESSION = "00000000-0000-4000-8000-0000000000c1"


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CONTROL_HOME_ENV, str(tmp_path / "control-home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.delenv(LOGS_ENV, raising=False)


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    root = tmp_path / "projet"
    (root / "_grimoire/standard").mkdir(parents=True)
    return root


@pytest.fixture
def service(projet: Path) -> TaskService:
    return TaskService(projet)


def _task(service: TaskService) -> str:
    mission = service.ledger.create_mission(title="Usage", origin="test")
    task = service.ledger.create_task(mission.id, "Session Copilot", acceptance=("l'usage est lu",), owner="amelia")
    service.ledger.transition_task(task.id, TaskState.READY)
    service.ledger.transition_task(task.id, TaskState.CLAIMED, actor_id="winston", claim=TaskClaim("winston", "local"))
    service.ledger.attach_session(task.id, SESSION, session_host="github-copilot", actor_id="hook")
    service.ledger.transition_task(task.id, TaskState.RUNNING, actor_id="winston")
    return task.id


def _journal(logs: Path, *, shutdown: bool) -> Path:
    events: list[dict[str, Any]] = [{"type": "session.start", "data": {"sessionId": SESSION}}]
    if shutdown:
        events.append(json.loads((FIXTURES / "session-shutdown-usage.json").read_text(encoding="utf-8"))["events"][0])
    else:
        events.append({"type": "assistant.message", "data": {"messageId": "m1", "model": "claude-sonnet-4.6", "outputTokens": 40}})
    path = logs / SESSION / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return path


def test_sans_emplacement_rien_n_est_lu_et_le_pas_le_dit(projet: Path, service: TaskService) -> None:
    task_id = _task(service)
    report = fold_pending(service)
    (outcome,) = report.outcomes
    assert outcome.status == "missing" and "journaux Copilot : emplacement non configuré" in outcome.detail
    assert report.logs == 0
    # Aucun journal lisible : rien d'inventé, comme avant (« none » ici : le claim ne nomme pas l'hôte).
    assert _usage_of(service.require(task_id), projet)["usage_source"] in ("none", "unavailable")


def test_le_pas_replie_une_fois_puis_task_usage_lit_le_repli(
    tmp_path: Path, projet: Path, service: TaskService, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_id = _task(service)
    logs = tmp_path / "logs"
    _journal(logs, shutdown=True)
    monkeypatch.setenv(LOGS_ENV, str(logs))
    # Avant tout repli : lu directement, plus « unavailable ».
    direct = _usage_of(service.require(task_id), projet)
    assert direct["usage_source"] == "copilot-session" and direct["input_tokens"] == 1000 and direct["verified"] is False
    first = fold_pending(service)
    assert [(o.status, o.new_calls) for o in first.outcomes] == [("folded", 1)]
    second = fold_pending(service)
    assert [(o.status, o.new_calls, o.already_folded) for o in second.outcomes] == [("folded", 0, 1)]
    traces = TraceLedger(projet / "_grimoire-output" / "traces").list_traces(task_id=task_id)
    assert len(traces) == 1 and traces[0].host_id == "host-github-copilot"
    folded = _usage_of(service.require(task_id), projet)
    assert folded["usage_source"] == "copilot-session" and folded["calls"] == 1
    assert folded["cost_usd"] is None and folded["note"] == UNVERIFIED_NOTE


def test_session_en_cours_est_reportee_et_lue_sans_entree(
    tmp_path: Path, projet: Path, service: TaskService, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_id = _task(service)
    logs = tmp_path / "logs"
    _journal(logs, shutdown=False)
    monkeypatch.setenv(LOGS_ENV, str(logs))
    (outcome,) = fold_pending(service).outcomes
    assert outcome.status == "deferred" and "not_ended" in outcome.detail
    usage = _usage_of(service.require(task_id), projet)
    assert usage["usage_source"] == "copilot-session" and usage["input_tokens"] is None and usage["output_tokens"] == 40


def test_ligne_de_task_usage_dit_inconnu_et_non_verifie() -> None:
    from grimoire.cli.cmd_task_usage import _usage_line

    line = _usage_line(
        {
            "usage_source": "copilot-session", "calls": 1, "input_tokens": None, "output_tokens": 40,
            "cache_read_tokens": None, "cache_creation_tokens": None, "cost_usd": None,
            "cost_status": "unknown", "unpriced_calls": 1, "discrepancy": None, "verified": False, "note": UNVERIFIED_NOTE,
        }
    )
    assert "inconnu in / 40 out" in line and UNVERIFIED_NOTE in line
