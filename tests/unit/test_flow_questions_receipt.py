"""Lot 4.6 (issues #564, #554) — le reçu d'un run dit combien de questions il a posées, et en combien d'interruptions."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from grimoire.flows.engine import FlowEngine
from grimoire.flows.executor import InteractiveNodeExecutor
from grimoire.missions.questions import QuestionSpec
from grimoire.missions.schemas import TaskClaim, TaskState
from grimoire.missions.service import TaskService

S = TaskState


@pytest.fixture(autouse=True)
def _no_host_session(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "CLAUDE_CODE_SESSION_ID", "GRIMOIRE_UNATTENDED"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GRIMOIRE_SESSION_ID", "S1")


def _engine(root: Path) -> FlowEngine:
    return FlowEngine(
        kernel_root=root / "_grimoire-runtime-output" / "runtime",
        flows_root=root / "_grimoire-runtime-output" / "flows",
        project_root=root,
    )


def _blueprint(root: Path) -> Path:
    gate = {"id": "gate", "kind": "checkpoint", "ref": "",
            "pins": [{"id": "out", "direction": "out", "contract": "c1"}]}
    after = {"id": "after", "kind": "pattern", "ref": "ORC-01", "pins": [{"id": "in", "direction": "in", "contract": "c1"}]}
    path = root / "q.blueprint.json"
    path.write_text(json.dumps({
        "blueprintVersion": 1, "id": "q-gate", "nodes": [gate, after],
        "edges": [{"from": "gate.out", "to": "after.in", "contract": "c1"}],
    }), encoding="utf-8")
    return path


def _run(root: Path, *, linked: bool = True) -> tuple[FlowEngine, str, TaskService]:
    service = TaskService(root)
    if linked:
        ledger = service.ledger
        mission = ledger.create_mission("Flows", origin="test")
        task = ledger.create_task(mission.id, "Flow", acceptance=("a",), task_id="FLOW-q-gate")
        ledger.transition_task(task.id, S.READY)
        ledger.transition_task(task.id, S.CLAIMED, claim=TaskClaim.new("flow", "host-claude-code-cli"))
        ledger.transition_task(task.id, S.RUNNING, actor_id="flow")
    engine = _engine(root)
    wfi, _ = engine.run(_blueprint(root), executor=InteractiveNodeExecutor(stream=io.StringIO()))
    return engine, wfi.id, service


def test_receipt_counts_three_blockers_grouped_in_one_interruption(tmp_path: Path) -> None:
    engine, run_id, service = _run(tmp_path)
    assert engine.status(run_id).to_dict()["questions_asked"] == 0

    blockers = [
        QuestionSpec(f"Blocage {i} : arbitrage requis ?", options=("oui", "non"), why=f"blocage {i}")
        for i in range(1, 4)
    ]
    service.ask_batch("FLOW-q-gate", blockers, "flow")

    receipt = engine.status(run_id).to_dict()
    assert receipt["questions_asked"] == 3
    assert receipt["question_interruptions"] == 1


def test_receipt_is_zero_without_a_linked_task(tmp_path: Path) -> None:
    engine, run_id, _ = _run(tmp_path, linked=False)
    receipt = engine.status(run_id).to_dict()
    assert receipt["questions_asked"] == 0 and receipt["question_interruptions"] == 0
