"""Issue #692 — Stop : plusieurs claims sans session nomment les candidates, jamais « bootstrap ».

Suite de #680/#686. Rejeu du 2026-10-02 (projet jetable, ``session_id=sess-Z``,
deux tâches réclamées sans session) : ``UserPromptSubmit`` nommait les
candidates et ``grimoire task attach <id>``, mais ``Stop`` retombait sur le
message de la tâche fantôme ``bootstrap`` (« encore en état proposed »). Une
session qui n'a pas écrit n'est pas bloquée, mais le message doit dire vrai.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts.decisions import Outcome
from grimoire.hosts.events import HookEvent
from grimoire.hosts.runtime import run_hook
from grimoire.missions.service import TaskService

SESSION = "sess-Z"
ACCEPTATION = "le hook Stop nomme les candidates"


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


def _claimed(root: Path, title: str, *, session: str = "") -> str:
    service = TaskService(root)
    tid = service.add(title, (ACCEPTATION,), owner="guilhem", actor="claude", ready=True).task.id
    service.context(tid)
    service.claim(tid, "claude", host="local")
    if session:
        service.attach_session(tid, session, session_host="claude-code-cli", actor="claude")
    return tid


def _stop(root: Path, session: str = SESSION) -> tuple[dict[str, Any], Any]:
    payload = {"session_id": session, "hook_event_name": "Stop", "cwd": str(root), "stop_hook_active": False}
    rendered, decision, _ = run_hook(payload, host_id=HostId.CLAUDE_CODE_CLI, event=HookEvent.STOP, project_root=root)
    return rendered, decision


def test_stop_sans_ecriture_nomme_les_candidates_au_lieu_de_juger_bootstrap(governed: Path) -> None:
    tid_1 = _claimed(governed, "Premiere")
    tid_2 = _claimed(governed, "Seconde")

    rendered, decision = _stop(governed)

    message = decision.context + decision.reason
    assert tid_1 in message and tid_2 in message
    assert "grimoire task attach" in message
    assert "bootstrap" not in message
    assert "encore en état" not in message
    assert decision.detail["task_source"] == "ambiguous"
    # Rien n'a été écrit : la session n'est pas bloquée, et l'hôte voit le message.
    assert decision.outcome is Outcome.ALLOW
    assert tid_1 in rendered["systemMessage"]


def test_stop_avec_ecriture_nomme_toujours_les_candidates_et_bloque(governed: Path) -> None:
    tid_1 = _claimed(governed, "Premiere")
    tid_2 = _claimed(governed, "Seconde")
    payload = {
        "session_id": SESSION,
        "hook_event_name": "PostToolUse",
        "cwd": str(governed),
        "tool_name": "Edit",
        "tool_input": {"file_path": str(governed / "src/app.py"), "old_string": "a", "new_string": "b"},
    }
    run_hook(payload, host_id=HostId.CLAUDE_CODE_CLI, event=HookEvent.POST_TOOL_USE, project_root=governed)

    _, decision = _stop(governed)

    assert decision.outcome is Outcome.BLOCK
    assert tid_1 in decision.reason and tid_2 in decision.reason
    assert "grimoire task attach" in decision.reason


def test_stop_juge_la_tache_rattachee_a_la_session(governed: Path) -> None:
    tid_a = _claimed(governed, "Rattachee", session=SESSION)
    _claimed(governed, "Autre")

    _, decision = _stop(governed)

    assert decision.detail.get("task_id") == tid_a
    assert "grimoire task attach" not in decision.context + decision.reason
