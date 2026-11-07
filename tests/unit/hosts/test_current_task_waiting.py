"""Issue #1153 — une tâche ``waiting`` non réclamée n'est ni courante ni jugée par ``Stop``.

Constat (2026-11-07) : la Forge injectait à chaque tour « Tâche courante :
GAO-refonte-ui-u-001 » et le gate ``Stop`` (shadow, ``_with_done_gate``) redemandait
les tests de cette tâche, alors qu'elle était ``waiting`` et que la session ne
l'avait pas réclamée. Chaque avertissement coûtait un tour.

Deux cas, symétriques :

- une tâche ``waiting`` dont le claim n'est pas celui de cette session ne désigne
  plus la tâche courante au prompt, et ``Stop`` ne la juge pas ;
- une tâche réclamée par la session garde le comportement actuel.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.core.standard_state import resolve_active_task
from grimoire.hosts.decisions import Outcome
from grimoire.hosts.events import HookEvent
from grimoire.hosts.runtime import run_hook
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService

SESSION = "dddddddd-0000-4000-8000-00000000000d"
OTHER_SESSION = "eeeeeeee-0000-4000-8000-00000000000e"
ACCEPTATION = "une tache waiting non reclamee n'est pas la tache courante"


@pytest.fixture
def governed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    registry = tmp_path / "_grimoire/standard/llm-provider-registry.yaml"
    registry.write_text(
        registry.read_text(encoding="utf-8").replace("enabled: false", "enabled: true", 1), encoding="utf-8"
    )
    return tmp_path


def _hook(root: Path, event: HookEvent, payload: dict[str, Any]) -> tuple[dict[str, Any], Any]:
    rendered, decision, _ = run_hook(payload, host_id=HostId.CLAUDE_CODE_CLI, event=event, project_root=root)
    return rendered, decision


def _payload(root: Path, event: str, session: str, **extra: Any) -> dict[str, Any]:
    return {"session_id": session, "hook_event_name": event, "cwd": str(root), **extra}


def _prompt(root: Path, session: str) -> tuple[dict[str, Any], Any]:
    payload = _payload(root, "UserPromptSubmit", session, prompt="go", transcript_path=str(root / "t.jsonl"))
    (root / "t.jsonl").write_text("", encoding="utf-8")
    return _hook(root, HookEvent.USER_PROMPT_SUBMIT, payload)


def _stop(root: Path, session: str) -> tuple[dict[str, Any], Any]:
    return _hook(root, HookEvent.STOP, _payload(root, "Stop", session, stop_hook_active=False))


def _waiting_task(root: Path, *, session: str) -> str:
    """Une tâche garée en ``waiting(ci)``, réclamée (ou non) par *session*."""
    service = TaskService(root)
    tid = service.add("Travail", (ACCEPTATION,), owner="guilhem", actor="claude", ready=True).task.id
    service.context(tid)
    service.claim(tid, "claude", host="claude-code-cli")
    service.transition(tid, TaskState.RUNNING, "claude")
    if session:
        service.attach_session(tid, session, session_host="claude-code-cli", actor="claude")
    service.wait(tid, "ci", "claude")
    assert service.require(tid).status is TaskState.WAITING
    return tid


def _green_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le socle des gates est vert : ce qui reste testé est la résolution de tâche."""
    monkeypatch.setattr(
        "grimoire.hosts.decisions.evidence_gate._gate_summary",
        lambda _root, task_id: (True, "", {"task_id": task_id, "state": "in_progress", "ok": True, "missing": []}),
    )


def _set_board_status(root: Path, task_id: str, status: str) -> None:
    """Rend la carte du board périmée : projection interrompue, board édité à la main."""
    import yaml

    path = root / "_grimoire/standard/task-board.yaml"
    board = yaml.safe_load(path.read_text(encoding="utf-8"))
    for card in board["tasks"]:
        if card.get("task_id") == task_id:
            card["status"] = status
    path.write_text(yaml.safe_dump(board, sort_keys=False), encoding="utf-8")


def test_a_waiting_task_of_another_session_is_not_the_current_task(
    governed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le prompt ne dit pas « Tâche courante » pour une tâche que la session ne tient pas."""
    tid = _waiting_task(governed, session=OTHER_SESSION)
    monkeypatch.setenv("GRIMOIRE_TASK_ID", tid)  # la Forge lance la session en nommant la tâche

    rendered, decision = _prompt(governed, SESSION)

    context = rendered.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert f"Tâche courante : {tid}" not in context
    assert decision.detail.get("task_id") != tid


def test_a_waiting_task_of_another_session_is_not_judged_by_stop(
    governed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le ``Stop`` ne juge pas les gates d'une tâche garée que la session ne détient pas."""
    _green_gates(monkeypatch)
    tid = _waiting_task(governed, session=OTHER_SESSION)
    monkeypatch.setenv("GRIMOIRE_TASK_ID", tid)

    rendered, decision = _stop(governed, SESSION)

    assert decision.outcome is Outcome.ALLOW
    assert decision.detail.get("task_id") != tid
    assert tid not in (decision.reason or "")
    assert tid not in rendered.get("systemMessage", "")


def test_a_stale_board_card_for_a_waiting_task_is_not_the_current_task(governed: Path) -> None:
    """Une carte ``in_progress`` que le ledger sait ``waiting`` est périmée : elle ne désigne rien."""
    tid = _waiting_task(governed, session=OTHER_SESSION)
    _set_board_status(governed, tid, "in_progress")

    rendered, decision = _prompt(governed, SESSION)

    context = rendered.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert f"Tâche courante : {tid}" not in context
    assert decision.detail.get("task_id") != tid


def test_a_waiting_task_claimed_by_the_session_keeps_current_behaviour(
    governed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Réclamée par la session, la tâche nommée reste courante et reste jugée : le gate n'est pas affaibli."""
    _green_gates(monkeypatch)
    tid = _waiting_task(governed, session=SESSION)
    monkeypatch.setenv("GRIMOIRE_TASK_ID", tid)

    rendered, _ = _prompt(governed, SESSION)
    context = rendered.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert f"Tâche courante : {tid}" in context

    _, decision = _stop(governed, SESSION)
    assert decision.detail.get("task_id") == tid


def test_resolution_ignores_a_waiting_task_that_another_session_holds(
    governed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La règle est dans ``resolve_active_task`` : tous les appelants en héritent."""
    tid = _waiting_task(governed, session=OTHER_SESSION)
    monkeypatch.setenv("GRIMOIRE_TASK_ID", tid)

    active = resolve_active_task(governed, session_id=SESSION)

    assert active.task_id != tid and active.is_fallback
