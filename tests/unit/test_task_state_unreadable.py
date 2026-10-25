"""Un board ou un ledger illisible n'est pas « aucune tâche » (tri R38/R39).

Avant, ``_held_claims`` et ``_load_mapping`` avalaient la corruption : la
résolution rendait ``bootstrap``, SessionStart disait « Aucune tâche du Mission
Ledger n'est en cours » (et invitait à en ouvrir une dans un ledger qu'on ne
sait pas lire), Stop jugeait la tâche fantôme ``bootstrap``. Maintenant la
source vaut ``unreadable``, chaque message dit « illisible » et nomme le fichier ;
une source absente par conception reste ``bootstrap``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.core.standard_state import LEDGER_RELPATH, TASK_BOARD_RELPATH, resolve_active_task
from grimoire.hosts.decisions import Outcome
from grimoire.hosts.events import HookEvent
from grimoire.hosts.runtime import run_hook
from grimoire.missions.service import TaskService

SESSION = "aaaaaaaa-0000-4000-8000-00000000000a"


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


def _corrupt_board(root: Path) -> None:
    (root / TASK_BOARD_RELPATH).write_text("tasks: [\n  - task_id: x\n", encoding="utf-8")


def _ledger_with_corrupt_line(root: Path) -> None:
    TaskService(root).add("Une tâche", ("ok",), owner="guilhem", actor="claude", ready=True)
    events = root / LEDGER_RELPATH / "events.jsonl"
    events.write_text(events.read_text(encoding="utf-8") + "{ceci n'est pas du json\n", encoding="utf-8")


def _hook(root: Path, event: HookEvent, **extra: Any) -> tuple[dict[str, Any], Any]:
    payload = {"session_id": SESSION, "hook_event_name": event.value, "cwd": str(root), **extra}
    rendered, decision, _ = run_hook(payload, host_id=HostId.CLAUDE_CODE_CLI, event=event, project_root=root)
    return rendered, decision


def _write(root: Path) -> None:
    _hook(
        root,
        HookEvent.POST_TOOL_USE,
        tool_name="Edit",
        tool_input={"file_path": str(root / "src/app.py"), "old_string": "a", "new_string": "b"},
        tool_response={"filePath": str(root / "src/app.py"), "success": True},
    )


# ── résolution ────────────────────────────────────────────────────────────────


def test_board_corrompu_n_est_pas_bootstrap(governed: Path) -> None:
    _corrupt_board(governed)
    active = resolve_active_task(governed, env={}, session_id=SESSION)
    assert active.source == "unreadable"
    assert active.is_fallback
    assert any("task-board.yaml" in p for p in active.unreadable)
    # Cache chaud : la corruption ne devient pas « vide » au deuxième appel.
    assert resolve_active_task(governed, env={}, session_id=SESSION).source == "unreadable"


def test_ledger_corrompu_n_est_pas_bootstrap(governed: Path) -> None:
    _ledger_with_corrupt_line(governed)
    active = resolve_active_task(governed, env={}, session_id=SESSION)
    assert active.source == "unreadable"
    assert any("events.jsonl" in p for p in active.unreadable)


def test_ledger_entierement_illisible_n_est_pas_bootstrap(governed: Path) -> None:
    path = governed / LEDGER_RELPATH / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xfe\x00 pas du json\n")
    assert resolve_active_task(governed, env={}, session_id=SESSION).source == "unreadable"


def test_sources_absentes_par_conception_restent_bootstrap(governed: Path) -> None:
    (governed / TASK_BOARD_RELPATH).unlink(missing_ok=True)
    active = resolve_active_task(governed, env={}, session_id=SESSION)
    assert (active.source, active.unreadable) == ("bootstrap", ())


def test_grimoire_task_id_prime_sur_une_source_illisible(governed: Path) -> None:
    _corrupt_board(governed)
    active = resolve_active_task(governed, env={"GRIMOIRE_TASK_ID": "t-1"}, session_id=SESSION)
    assert (active.task_id, active.source) == ("t-1", "env")


# ── SessionStart / UserPromptSubmit ──────────────────────────────────────────


@pytest.mark.parametrize("corrupt", [_corrupt_board, _ledger_with_corrupt_line])
def test_session_start_dit_illisible(governed: Path, corrupt: Any) -> None:
    corrupt(governed)
    rendered, _ = _hook(governed, HookEvent.SESSION_START)
    context = rendered["hookSpecificOutput"]["additionalContext"]
    assert "illisible" in context
    assert "Aucune tâche du Mission Ledger n'est en cours" not in context


def test_prompt_dit_illisible(governed: Path) -> None:
    _corrupt_board(governed)
    rendered, _ = _hook(governed, HookEvent.USER_PROMPT_SUBMIT, prompt="salut")
    context = rendered["hookSpecificOutput"]["additionalContext"]
    assert "illisible" in context
    assert "Aucune tâche du Mission Ledger n'est en cours" not in context


# ── Stop ──────────────────────────────────────────────────────────────────────


def test_stop_ne_retombe_pas_en_silence_sur_bootstrap(governed: Path) -> None:
    _corrupt_board(governed)
    _, decision = _hook(governed, HookEvent.STOP)
    assert decision.outcome is Outcome.BLOCK  # profil governed : refus une fois, nommé
    assert decision.detail.get("task_source") == "unreadable"
    assert "illisible" in decision.reason
    assert "bootstrap" not in decision.reason.replace("Tâche bootstrap", "")
    # ...et le second Stop passe : une source cassée coûte un tour, jamais la session.
    _, again = _hook(governed, HookEvent.STOP, stop_hook_active=True)
    assert again.outcome is Outcome.ALLOW


def test_stop_apres_ecriture_refuse_en_nommant_la_source_illisible(governed: Path) -> None:
    _corrupt_board(governed)
    _write(governed)
    _, decision = _hook(governed, HookEvent.STOP)
    assert decision.outcome is Outcome.BLOCK
    assert "illisible" in decision.reason
    assert "introuvable" in decision.reason
