"""K6 (issue #771) — l'attente nommée vue des hooks, des flows et du cockpit.

- ``Stop`` : une session qui a garé sa tâche (``task ask`` / ``task wait``) peut s'arrêter. Sur la base,
  son travail passait pour une écriture hors tâche et un profil gouverné refusait la clôture ;
- flows (W2-01) : un checkpoint destructif sans décision attestée pose sa question sur la tâche liée, et
  une décision attestée y répond ;
- cockpit : la réponse passe par le jeton de session (W1-10), et l'origine humaine est posée par le canal,
  jamais lue du corps de la requête.
"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path
from typing import Any

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.core.exceptions import GrimoireMissionError
from grimoire.flows.engine import FlowEngine
from grimoire.flows.executor import InteractiveNodeExecutor
from grimoire.hosts.decisions import HookInput, Outcome, run_decision
from grimoire.hosts.events import HookEvent
from grimoire.hosts.runtime import run_hook
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import TaskClaim, TaskState
from grimoire.missions.service import TaskService
from grimoire.tools import request_guard

from .test_mutation_guard import JSON, Host, cockpit  # noqa: F401

SESSION = "sess-K6"
S = TaskState


@pytest.fixture(autouse=True)
def _no_host_session(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "CLAUDE_CODE_SESSION_ID", "GRIMOIRE_UNATTENDED"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GRIMOIRE_SESSION_ID", "S1")


# ── Stop : une session qui se gare peut s'arrêter ───────────────────────────


@pytest.fixture
def governed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("GRIMOIRE_SESSION_ID", raising=False)
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    registry = tmp_path / "_grimoire/standard/llm-provider-registry.yaml"
    registry.write_text(registry.read_text(encoding="utf-8").replace("enabled: false", "enabled: true", 1), encoding="utf-8")
    return tmp_path


def _hook(root: Path, event: HookEvent, **payload: Any) -> Any:
    body = {"session_id": SESSION, "hook_event_name": event.value, "cwd": str(root), **payload}
    rendered, decision, _ = run_hook(body, host_id=HostId.CLAUDE_CODE_CLI, event=event, project_root=root)
    return rendered, decision


def _working_session(root: Path) -> tuple[TaskService, str]:
    """Une session qui tient une tâche, a écrit un fichier, et vient de s'enregistrer au hook."""
    service = TaskService(root)
    tid = service.add("Travail", ("fait",), owner="guilhem", actor="claude", ready=True).task.id
    service.context(tid)
    service.claim(tid, "claude", host="host-claude-code-cli")
    service.transition(tid, S.RUNNING, "claude")
    service.attach_session(tid, SESSION, session_host="claude-code-cli", actor="claude")
    _hook(root, HookEvent.POST_TOOL_USE, tool_name="Edit",
          tool_input={"file_path": str(root / "src/app.py"), "old_string": "a", "new_string": "b"})
    return service, tid


def test_stop_refuses_a_session_that_still_holds_its_task(governed: Path) -> None:
    _, tid = _working_session(governed)
    _, decision = _hook(governed, HookEvent.STOP, stop_hook_active=False)
    assert decision.outcome is Outcome.BLOCK  # témoin : tenue et non garée, la clôture reste refusée en profil gouverné
    assert decision.detail["task_id"] == tid


@pytest.mark.parametrize("parking", ["ask", "wait"])
def test_stop_lets_a_parked_session_stop(governed: Path, parking: str) -> None:
    service, tid = _working_session(governed)
    if parking == "ask":
        service.ask(tid, "Fusionner la PR ?", "claude", kind="merge")
    else:
        service.wait(tid, "ci", "claude")
    assert service.require(tid).status is S.WAITING

    rendered, decision = _hook(governed, HookEvent.STOP, stop_hook_active=False)

    assert decision.outcome is Outcome.ALLOW, decision.reason  # sur la base : « clôture hors tâche », refusée
    assert decision.detail["parked"] is True and decision.detail["task_id"] == tid
    assert "garée en attente" in decision.context and tid in rendered["systemMessage"]


def test_another_session_is_not_let_through_by_a_parked_task(governed: Path) -> None:
    service, tid = _working_session(governed)
    service.ask(tid, "Fusionner ?", "claude")
    # Une autre session a écrit sans tâche : sa clôture reste refusée (la tâche garée n'est pas la sienne).
    other = {"session_id": "autre", "hook_event_name": "PostToolUse", "cwd": str(governed), "tool_name": "Edit",
             "tool_input": {"file_path": str(governed / "x.py"), "old_string": "a", "new_string": "b"}}
    run_hook(other, host_id=HostId.CLAUDE_CODE_CLI, event=HookEvent.POST_TOOL_USE, project_root=governed)
    stop = {"session_id": "autre", "hook_event_name": "Stop", "cwd": str(governed), "stop_hook_active": False}
    _, decision, _ = run_hook(stop, host_id=HostId.CLAUDE_CODE_CLI, event=HookEvent.STOP, project_root=governed)
    assert decision.outcome is Outcome.BLOCK and decision.detail["blocked_on"] == "no_active_task"


def test_session_start_tells_the_next_session_that_a_human_is_owed_an_answer(governed: Path) -> None:
    service, tid = _working_session(governed)
    service.ask(tid, "Fusionner la PR ?", "claude", kind="merge")
    _, decision = _hook(governed, HookEvent.SESSION_START, source="startup")
    assert "1 décision en attente, plus ancienne" in decision.context and tid in decision.context
    assert "grimoire task decisions" in decision.context
    service.answer(tid, "oui", "guilhem", origin="human")
    _, after = _hook(governed, HookEvent.SESSION_START, source="startup")
    assert "décision en attente" not in after.context and "décisions en attente" not in after.context


# ── flows : la question d'un checkpoint destructif vit sur la tâche liée ────

_GATE_OUT = {"pins": {"out": {"contract": "c1"}}}


def _engine(root: Path) -> FlowEngine:
    return FlowEngine(
        kernel_root=root / "_grimoire-runtime-output" / "runtime",
        flows_root=root / "_grimoire-runtime-output" / "flows",
        project_root=root,
    )


def _quiet() -> InteractiveNodeExecutor:
    return InteractiveNodeExecutor(stream=io.StringIO())


def _blueprint(root: Path) -> Path:
    gate = {"id": "gate", "kind": "checkpoint", "ref": "", "config": {"checkpoint": {"destructive": True}},
            "pins": [{"id": "out", "direction": "out", "contract": "c1"}]}
    after = {"id": "after", "kind": "pattern", "ref": "ORC-01", "pins": [{"id": "in", "direction": "in", "contract": "c1"}]}
    path = root / "k6.blueprint.json"
    path.write_text(json.dumps({
        "blueprintVersion": 1, "id": "k6-gate", "nodes": [gate, after],
        "edges": [{"from": "gate.out", "to": "after.in", "contract": "c1"}],
    }), encoding="utf-8")
    return path


def _linked_task(root: Path, *, running: bool = True) -> MissionLedger:
    """La tâche de ledger que le run lie par défaut : ``FLOW-<id du blueprint>``."""
    ledger = TaskService(root).ledger
    mission = ledger.create_mission("Flows", origin="test")
    task = ledger.create_task(mission.id, "Flow", acceptance=("a",), task_id="FLOW-k6-gate")
    ledger.transition_task(task.id, S.READY)
    if running:
        ledger.transition_task(task.id, S.CLAIMED, claim=TaskClaim.new("flow", "host-claude-code-cli"))
        ledger.transition_task(task.id, S.RUNNING, actor_id="flow")
    return ledger


def _approve(engine: FlowEngine, run_id: str) -> Any:
    return engine.resume(run_id, output={**_GATE_OUT, "checkpoint_decision": "approve"}, executor=_quiet())


def _human_turn(root: Path) -> None:
    time.sleep(0.02)
    run_decision("grimoire.task-context", HookInput(
        event=HookEvent.USER_PROMPT_SUBMIT, project_root=root, prompt="ok", session_id="S1", host="host-claude-code-cli"))
    time.sleep(0.02)


def test_a_destructive_checkpoint_asks_the_linked_task_and_an_attested_decision_answers(tmp_path: Path) -> None:
    _linked_task(tmp_path)
    engine = _engine(tmp_path)
    wfi, _ = engine.run(_blueprint(tmp_path), executor=_quiet())

    refused = _approve(engine, wfi.id)  # aucun tour humain : awaiting_human
    assert refused.ok is False and "awaiting_human" in " ".join(refused.faults)

    task = MissionLedger(tmp_path / "_grimoire-runtime-output" / "ledger").get_task("FLOW-k6-gate")
    assert task is not None and task.status is S.WAITING and task.waiting is not None
    question = task.waiting.question
    assert question is not None and question.id == f"q-flow-{wfi.id}-gate"
    assert (question.kind, question.origin, question.options) == ("permission", "flow", ("approve", "reject", "amend"))
    assert "« gate »" in question.text and wfi.id in question.text and "journal des tours" in question.text

    # Un refus répété ne repose pas la question.
    _approve(engine, wfi.id)
    ledger = MissionLedger(tmp_path / "_grimoire-runtime-output" / "ledger")
    assert len([e for e in ledger.list_events("FLOW-k6-gate") if e.event_type == "task.question_asked"]) == 1

    _human_turn(tmp_path)
    accepted = _approve(engine, wfi.id)
    assert accepted.ok is True and accepted.attestation["status"] == "attested"

    ledger = MissionLedger(tmp_path / "_grimoire-runtime-output" / "ledger")
    woken = ledger.get_task("FLOW-k6-gate")
    assert woken is not None and woken.status is S.READY and woken.waiting is None
    [answer] = [e for e in ledger.list_events("FLOW-k6-gate") if e.event_type == "task.human_answered"]
    assert answer.payload["question_id"] == question.id and answer.payload["origin"] == "human"


def test_a_flow_question_is_not_answered_from_the_cockpit_or_the_cli(tmp_path: Path) -> None:
    """Répondre à la place du flux réveillerait la tâche sans décider le checkpoint : refusé, le flux atteste."""
    _linked_task(tmp_path)
    engine = _engine(tmp_path)
    wfi, _ = engine.run(_blueprint(tmp_path), executor=_quiet())
    _approve(engine, wfi.id)
    service = TaskService(tmp_path)
    with pytest.raises(GrimoireMissionError, match="s'atteste dans le flux"):
        service.answer("FLOW-k6-gate", "approve", "guilhem", origin="human")
    assert service.require("FLOW-k6-gate").status is S.WAITING


def test_an_unheld_linked_task_only_records_the_question_event(tmp_path: Path) -> None:
    _linked_task(tmp_path, running=False)
    engine = _engine(tmp_path)
    wfi, _ = engine.run(_blueprint(tmp_path), executor=_quiet())
    _approve(engine, wfi.id)
    _approve(engine, wfi.id)
    ledger = MissionLedger(tmp_path / "_grimoire-runtime-output" / "ledger")
    task = ledger.get_task("FLOW-k6-gate")
    assert task is not None and task.status is S.READY  # rien à garer : elle n'est pas tenue
    asked = [e for e in ledger.list_events("FLOW-k6-gate") if e.event_type == "task.question_asked"]
    assert len(asked) == 1 and asked[0].payload["question"]["origin"] == "flow"


def test_a_run_without_a_linked_task_creates_nothing(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    wfi, _ = engine.run(_blueprint(tmp_path), executor=_quiet())
    assert _approve(engine, wfi.id).ok is False
    assert not (tmp_path / "_grimoire-runtime-output" / "ledger" / "events.jsonl").exists()


# ── cockpit : répondre passe par le jeton de session et le canal humain ─────


def _waiting_task(root: Path) -> tuple[str, str]:
    service = TaskService(root)
    tid = service.add("Décision", ("a",), owner="guilhem", actor="t", ready=True).task.id
    service.claim(tid, "agent-a", host="host-claude-code-cli")
    service.transition(tid, S.RUNNING, "agent-a")
    service.ask(tid, "Fusionner la PR ?", "agent-a", kind="merge", options=("fusionner", "attendre"))
    return tid, service.require(tid).waiting.question.id  # type: ignore[union-attr]


def _post(host: Host, path: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", host.port, timeout=10)
    try:
        conn.request("POST", f"{path}?project={host.slug}", json.dumps(body).encode(), headers)
        resp = conn.getresponse()
        raw = resp.read()
        return resp.status, (json.loads(raw) if raw else {})
    finally:
        conn.close()


@pytest.mark.no_session_token
def test_answering_without_the_session_token_is_refused_and_writes_nothing(cockpit: Host) -> None:  # noqa: F811
    tid, qid = _waiting_task(cockpit.root)
    before = (cockpit.root / "_grimoire-runtime-output/ledger/events.jsonl").read_text(encoding="utf-8")
    for headers in ({**JSON}, {**JSON, "Origin": cockpit.origin}, {**JSON, request_guard.SESSION_HEADER: "pas-le-bon"}):
        status, _ = _post(cockpit, f"/api/workspace/tasks/{tid}/answer", {"text": "fusionner", "question_id": qid}, headers)
        assert status == 403
    assert (cockpit.root / "_grimoire-runtime-output/ledger/events.jsonl").read_text(encoding="utf-8") == before
    assert TaskService(cockpit.root).require(tid).status is S.WAITING


def test_answering_with_the_token_is_a_human_answer_whatever_the_body_claims(cockpit: Host) -> None:  # noqa: F811
    tid, qid = _waiting_task(cockpit.root)
    status, body = _post(
        cockpit, f"/api/workspace/tasks/{tid}/answer",
        {"text": "fusionner", "question_id": qid, "origin": "agent", "actor": "agent-a"}, {**JSON},  # le jeton est posé par conftest
    )
    assert status == 200, body
    assert body["status"] == "ready"
    ledger = MissionLedger(cockpit.root / "_grimoire-runtime-output" / "ledger")
    [answer] = [e for e in ledger.list_events(tid) if e.event_type == "task.human_answered"]
    assert answer.payload["origin"] == "human" and answer.payload["answer"] == "fusionner"
    # Une seconde réponse n'a plus de question à trancher.
    status, _ = _post(cockpit, f"/api/workspace/tasks/{tid}/answer", {"text": "encore"}, {**JSON})
    assert status == 404  # GrimoireMissionError → FileNotFoundError côté transport


def test_the_workspace_task_view_carries_the_wait_and_its_question(cockpit: Host) -> None:  # noqa: F811
    from grimoire.tools import workspace_api

    tid, qid = _waiting_task(cockpit.root)
    view = workspace_api.tasks_view(cockpit.root)
    card = next(t for t in view["tasks"] if t["id"] == tid)
    assert card["status"] == "waiting" and card["board"] == "blocked"
    assert card["waiting"]["reason"] == "human" and card["waiting"]["question"]["id"] == qid
    assert view["tasks"][0]["id"] == tid and "waiting" in view["states"]
