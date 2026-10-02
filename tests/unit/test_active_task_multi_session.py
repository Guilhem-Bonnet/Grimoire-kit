"""Issue #680 — plusieurs sessions sur un projet : la tâche active suit le session_id.

Constat du 2026-10-02 (homelab, kit 3.61.1) : cinq sessions Claude Code, deux
claims — toutes retombaient sur ``bootstrap`` (« Clôture hors tâche »), parce
que ``resolve_active_task`` sautait le niveau « claim unique » dès que deux
claims coexistaient et ne lisait pas ``task.session_attached``. Et un
``grimoire task claim`` lancé depuis la session ne la rattachait pas.

Ordre attendu : ``GRIMOIRE_TASK_ID`` → claim rattaché à ce ``session_id`` →
claim unique → board → ``bootstrap`` ; plusieurs claims sans rattachement
nomment leurs candidates au lieu de se replier en silence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.bridges.schemas import HostId
from grimoire.cli.cmd_task import task_app
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.core.standard_state import resolve_active_task
from grimoire.hosts.decisions import Outcome
from grimoire.hosts.events import HookEvent
from grimoire.hosts.runtime import run_hook
from grimoire.missions.service import TaskService

SESSION_A = "aaaaaaaa-0000-4000-8000-00000000000a"
SESSION_B = "bbbbbbbb-0000-4000-8000-00000000000b"
SESSION_C = "cccccccc-0000-4000-8000-00000000000c"
ACCEPTATION = "le hook resout la tache de chaque session"

runner = CliRunner()


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


def _ready(root: Path, title: str) -> str:
    service = TaskService(root)
    added = service.add(title, (ACCEPTATION,), owner="guilhem", actor="claude", ready=True)
    service.context(added.task.id)
    return added.task.id


def _claimed(root: Path, title: str, *, actor: str = "claude", session: str = "") -> str:
    tid = _ready(root, title)
    service = TaskService(root)
    service.claim(tid, actor, host="local")
    if session:
        service.attach_session(tid, session, session_host="claude-code-cli", actor=actor)
    return tid


def _hook(root: Path, event: HookEvent, session: str, **extra: Any) -> tuple[dict[str, Any], Any]:
    payload = {"session_id": session, "hook_event_name": event.value, "cwd": str(root), **extra}
    rendered, decision, _ = run_hook(payload, host_id=HostId.CLAUDE_CODE_CLI, event=event, project_root=root)
    return rendered, decision


def _write(root: Path, session: str) -> None:
    _hook(
        root,
        HookEvent.POST_TOOL_USE,
        session,
        tool_name="Edit",
        tool_input={"file_path": str(root / "src/app.py"), "old_string": "a", "new_string": "b"},
        tool_response={"filePath": str(root / "src/app.py"), "success": True},
    )


# ── résolution : chaque session retrouve sa tâche ────────────────────────────


def test_deux_sessions_deux_claims_chacune_resout_la_sienne(governed: Path) -> None:
    tid_a = _claimed(governed, "Tache de A", session=SESSION_A)
    tid_b = _claimed(governed, "Tache de B", session=SESSION_B)

    a = resolve_active_task(governed, env={}, session_id=SESSION_A)
    b = resolve_active_task(governed, env={}, session_id=SESSION_B)

    assert (a.task_id, b.task_id) == (tid_a, tid_b)
    assert a.source == b.source == "session_claim"


def test_le_claim_de_la_session_l_emporte_sur_un_claim_unique_filtre_par_acteur(governed: Path) -> None:
    tid_a = _claimed(governed, "Tache de A", actor="claude", session=SESSION_A)
    _claimed(governed, "Tache de B", actor="copilot", session=SESSION_B)
    assert resolve_active_task(governed, env={"GRIMOIRE_ACTOR": "copilot"}, session_id=SESSION_A).task_id == tid_a


def test_grimoire_task_id_reste_prioritaire(governed: Path) -> None:
    _claimed(governed, "Tache de A", session=SESSION_A)
    active = resolve_active_task(governed, env={"GRIMOIRE_TASK_ID": "sprint-9"}, session_id=SESSION_A)
    assert (active.task_id, active.source) == ("sprint-9", "env")


def test_la_session_se_lit_aussi_dans_l_environnement_de_l_hote(governed: Path) -> None:
    tid_a = _claimed(governed, "Tache de A", session=SESSION_A)
    _claimed(governed, "Tache de B", session=SESSION_B)
    active = resolve_active_task(governed, env={"CLAUDE_CODE_SESSION_ID": SESSION_A})
    assert active.task_id == tid_a
    active = resolve_active_task(governed, env={"GRIMOIRE_SESSION_ID": SESSION_A, "CLAUDE_CODE_SESSION_ID": SESSION_B})
    assert active.task_id == tid_a, "GRIMOIRE_SESSION_ID, posé explicitement, prime"


# ── un seul claim : inchangé ─────────────────────────────────────────────────


def test_un_seul_claim_reste_la_tache_active_avec_ou_sans_session(governed: Path) -> None:
    tid = _claimed(governed, "Seule tache")
    for session in ("", SESSION_A):
        active = resolve_active_task(governed, env={}, session_id=session)
        assert (active.task_id, active.source) == (tid, "ledger_claim")
    assert active.candidates == ()


# ── plusieurs claims sans rattachement : jamais de repli silencieux ──────────


def test_deux_claims_sans_rattachement_ne_replient_pas_en_silence_sur_bootstrap(governed: Path) -> None:
    tid_1 = _claimed(governed, "Premiere")
    tid_2 = _claimed(governed, "Seconde")

    active = resolve_active_task(governed, env={}, session_id=SESSION_C)

    assert active.task_id == "bootstrap"
    assert active.source == "ambiguous"
    assert set(active.candidates) == {tid_1, tid_2}


def test_session_sans_claim_parmi_des_claims_rattaches_ailleurs_est_ambigue_et_nomme_les_candidates(
    governed: Path,
) -> None:
    tid_a = _claimed(governed, "Tache de A", session=SESSION_A)
    tid_b = _claimed(governed, "Tache de B", session=SESSION_B)
    active = resolve_active_task(governed, env={}, session_id=SESSION_C)
    assert active.source == "ambiguous" and set(active.candidates) == {tid_a, tid_b}


def test_le_prompt_nomme_les_candidates_et_la_commande_de_rattachement(governed: Path) -> None:
    tid_1 = _claimed(governed, "Premiere")
    tid_2 = _claimed(governed, "Seconde")
    rendered, decision = _hook(governed, HookEvent.USER_PROMPT_SUBMIT, SESSION_C, prompt="corrige")
    context = rendered["hookSpecificOutput"]["additionalContext"]
    assert tid_1 in context and tid_2 in context
    assert "grimoire task attach" in context
    assert decision.detail["task_source"] == "ambiguous"
    # Rien n'est rattaché au hasard.
    service = TaskService(governed)
    assert service.require(tid_1).claim.session_id == ""  # type: ignore[union-attr]
    assert service.require(tid_2).claim.session_id == ""  # type: ignore[union-attr]


def test_stop_nomme_les_candidates_au_lieu_de_aucune_tache(governed: Path) -> None:
    tid_1 = _claimed(governed, "Premiere")
    tid_2 = _claimed(governed, "Seconde")
    _write(governed, SESSION_C)
    _, decision = _hook(governed, HookEvent.STOP, SESSION_C, stop_hook_active=False)
    assert decision.outcome is Outcome.BLOCK
    assert tid_1 in decision.reason and tid_2 in decision.reason
    assert "grimoire task attach" in decision.reason
    assert "aucune tâche du Mission Ledger n'est en cours" not in decision.reason


# ── hooks : chaque session voit sa tâche, Stop ne dit pas « hors tâche » ─────


def test_les_hooks_de_deux_sessions_resolvent_chacun_sa_tache(governed: Path) -> None:
    tid_a = _claimed(governed, "Tache de A", session=SESSION_A)
    tid_b = _claimed(governed, "Tache de B", session=SESSION_B)

    for session, tid in ((SESSION_A, tid_a), (SESSION_B, tid_b)):
        rendered, decision = _hook(governed, HookEvent.USER_PROMPT_SUBMIT, session, prompt="salut")
        assert decision.detail["task_id"] == tid
        assert f"Tâche courante : {tid}" in rendered["hookSpecificOutput"]["additionalContext"]
        _write(governed, session)
        _, stop = _hook(governed, HookEvent.STOP, session, stop_hook_active=False)
        assert stop.detail.get("blocked_on") != "no_active_task"
        assert stop.detail["task_id"] == tid


# ── claim depuis la session : le session_id est posé sur la carte ────────────


def _claim_cli(root: Path, tid: str, env: dict[str, str]) -> Any:
    return runner.invoke(
        task_app,
        ["claim", tid, "--actor", "claude", "--project-root", str(root)],
        obj={"output": "text"},
        env=env,
    )


def test_le_claim_cli_depuis_une_session_rattache_son_session_id(governed: Path) -> None:
    from grimoire.tools.workspace_api import tasks_view

    tid = _ready(governed, "Reclamee en CLI")
    res = _claim_cli(governed, tid, {"CLAUDE_CODE_SESSION_ID": SESSION_A})
    assert res.exit_code == 0, res.output

    service = TaskService(governed)
    kinds = [e.event_type for e in service.ledger.list_events(tid)]
    assert kinds.count("task.session_attached") == 1
    card = next(t for t in tasks_view(governed)["tasks"] if t["id"] == tid)
    assert card["session_id"] == SESSION_A
    assert resolve_active_task(governed, env={}, session_id=SESSION_A).task_id == tid


def test_le_claim_cli_sans_session_identifiable_ne_rattache_rien(governed: Path) -> None:
    tid = _ready(governed, "Reclamee hors session")
    res = _claim_cli(governed, tid, {})
    assert res.exit_code == 0, res.output
    kinds = [e.event_type for e in TaskService(governed).ledger.list_events(tid)]
    assert "task.session_attached" not in kinds


def test_deux_claims_cli_de_deux_sessions_se_resolvent_chacun(governed: Path) -> None:
    tid_a = _ready(governed, "Tache de A")
    tid_b = _ready(governed, "Tache de B")
    assert _claim_cli(governed, tid_a, {"CLAUDE_CODE_SESSION_ID": SESSION_A}).exit_code == 0
    assert _claim_cli(governed, tid_b, {"CLAUDE_CODE_SESSION_ID": SESSION_B}).exit_code == 0
    assert resolve_active_task(governed, env={}, session_id=SESSION_A).task_id == tid_a
    assert resolve_active_task(governed, env={}, session_id=SESSION_B).task_id == tid_b


# ── la commande de rattachement annoncée existe ──────────────────────────────


def test_task_attach_rattache_la_session_de_l_environnement(governed: Path) -> None:
    tid_1 = _claimed(governed, "Premiere")
    _claimed(governed, "Seconde")
    res = runner.invoke(
        task_app,
        ["attach", tid_1, "--project-root", str(governed)],
        obj={"output": "json"},
        env={"CLAUDE_CODE_SESSION_ID": SESSION_C},
    )
    assert res.exit_code == 0, res.output
    assert json.loads(res.stdout)["session_id"] == SESSION_C
    assert resolve_active_task(governed, env={}, session_id=SESSION_C).task_id == tid_1


def test_task_attach_sans_session_identifiable_echoue_clairement(governed: Path) -> None:
    tid = _claimed(governed, "Seule")
    res = runner.invoke(task_app, ["attach", tid, "--project-root", str(governed)], obj={"output": "text"}, env={})
    assert res.exit_code != 0
    assert "session" in res.output.lower()
