"""Lot A de l'issue #638 — les chantiers des sessions entrent dans le Mission Ledger.

Trois faits vérifiés avec les vrais payloads d'hôte (forme Claude Code), sur
un projet enrôlé par le vrai ``setup_standard_profile`` en profil ``governed`` :

a. sans tâche, le contexte injecté au ``SessionStart`` et au ``UserPromptSubmit``
   dit qu'aucune tâche n'est en cours et donne le remède copiable ;
b. après une action d'écriture journalisée dans la session, ``Stop`` refuse la
   clôture sous ``bootstrap`` — et une session qui n'a fait que lire n'est
   jamais bloquée ;
c. après ``task_add`` + ``claim``, le claim porte ``session_id``, le journal de
   session porte ``task_id``, la carte de ``tasks_view`` les expose, et ``Stop``
   ne bloque plus pour ce motif.

Chaque comportement fail-closed a d'abord été écrit ici en rouge (commit
« rouge-avant »), puis l'implémentation l'a fait passer au vert.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.core.standard_state import resolve_active_task
from grimoire.hosts.decisions import Outcome
from grimoire.hosts.events import HookEvent
from grimoire.hosts.runtime import main as hook_main
from grimoire.hosts.runtime import run_hook
from grimoire.missions.service import DEFAULT_LEDGER_RELPATH, TaskService
from grimoire.policies.session_state import load_session_state

SESSION = "efd17015-0000-4000-8000-000000000001"
ACCEPTATION = "le hook Stop refuse une cloture hors tache apres une ecriture"
REMEDE = "grimoire task add"


# ── projet gouverné pour de vrai ─────────────────────────────────────────────


@pytest.fixture
def governed(tmp_path: Path) -> Path:
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    # Le gate ``ready_to_in_progress`` exige un fournisseur activé : le
    # registre scaffoldé n'en active aucun par défaut.
    registry = tmp_path / "_grimoire/standard/llm-provider-registry.yaml"
    registry.write_text(
        registry.read_text(encoding="utf-8").replace("enabled: false", "enabled: true", 1), encoding="utf-8"
    )
    return tmp_path


def _payload(event: str, root: Path, **extra: Any) -> dict[str, Any]:
    """Un payload dans la forme exacte que Claude Code envoie sur stdin."""
    return {"session_id": SESSION, "hook_event_name": event, "cwd": str(root), **extra}


def _hook(root: Path, event: HookEvent, payload: dict[str, Any]) -> tuple[dict[str, Any], Any]:
    rendered, decision, _ = run_hook(payload, host_id=HostId.CLAUDE_CODE_CLI, event=event, project_root=root)
    return rendered, decision


def _write(root: Path, path: str = "src/app.py") -> None:
    _hook(
        root,
        HookEvent.POST_TOOL_USE,
        _payload(
            "PostToolUse",
            root,
            tool_name="Edit",
            tool_input={"file_path": str(root / path), "old_string": "a", "new_string": "b"},
            tool_response={"filePath": str(root / path), "success": True},
        ),
    )


def _read_only(root: Path) -> None:
    _hook(
        root,
        HookEvent.POST_TOOL_USE,
        _payload("PostToolUse", root, tool_name="Read", tool_input={"file_path": str(root / "README.md")}),
    )
    _hook(
        root,
        HookEvent.POST_TOOL_USE,
        _payload(
            "PostToolUse",
            root,
            tool_name="Bash",
            tool_input={"command": "git status"},
            tool_response={"stdout": "", "exit_code": 0},
        ),
    )


def _stop(root: Path, *, active: bool = False) -> tuple[dict[str, Any], Any]:
    return _hook(root, HookEvent.STOP, _payload("Stop", root, stop_hook_active=active))


def _prompt(root: Path) -> tuple[dict[str, Any], Any]:
    return _hook(root, HookEvent.USER_PROMPT_SUBMIT, _payload("UserPromptSubmit", root, prompt="corrige le bug"))


# ── (a) sans tâche : le contexte nomme l'absence et le remède ────────────────


def test_le_prompt_dit_qu_aucune_tache_n_est_en_cours_et_donne_le_remede(governed: Path) -> None:
    rendered, decision = _prompt(governed)
    context = rendered["hookSpecificOutput"]["additionalContext"]
    assert "aucune tâche" in context.lower()
    assert REMEDE in context and "grimoire task claim" in context
    assert "task_add" in context, "les outils MCP font partie du remède"
    assert "GRIMOIRE_TASK_ID" in context
    assert decision.detail["task_source"] == "bootstrap"


def test_le_session_start_governed_nomme_le_remede(governed: Path) -> None:
    rendered, _ = _hook(governed, HookEvent.SESSION_START, _payload("SessionStart", governed, source="startup"))
    context = rendered["hookSpecificOutput"]["additionalContext"]
    assert "aucune tâche" in context.lower()
    assert REMEDE in context


# ── (b) Stop refuse la clôture hors tâche après une écriture ─────────────────


def test_stop_refuse_la_cloture_sous_bootstrap_apres_une_ecriture(governed: Path) -> None:
    _write(governed)
    rendered, decision = _stop(governed)
    assert decision.outcome is Outcome.BLOCK
    assert rendered == {"decision": "block", "reason": decision.reason}
    assert REMEDE in decision.reason and "grimoire task claim" in decision.reason
    assert decision.detail["blocked_on"] == "no_active_task"
    assert decision.detail["mutations"] >= 1


def test_stop_refuse_aussi_apres_un_bash_mutant(governed: Path) -> None:
    _hook(
        governed,
        HookEvent.POST_TOOL_USE,
        _payload(
            "PostToolUse",
            governed,
            tool_name="Bash",
            tool_input={"command": "git commit -m x"},
            tool_response={"exit_code": 0},
        ),
    )
    _, decision = _stop(governed)
    assert decision.outcome is Outcome.BLOCK
    assert decision.detail["blocked_on"] == "no_active_task"


def test_une_session_qui_n_a_fait_que_lire_n_est_jamais_bloquee(governed: Path) -> None:
    _read_only(governed)
    rendered, decision = _stop(governed)
    assert decision.outcome is Outcome.ALLOW
    assert "decision" not in rendered
    assert "blocked_on" not in decision.detail


def test_une_session_sans_aucun_outil_n_est_jamais_bloquee(governed: Path) -> None:
    _, decision = _stop(governed)
    assert decision.outcome is Outcome.ALLOW


def test_stop_hook_active_ne_reboucle_pas(governed: Path) -> None:
    _write(governed)
    _, decision = _stop(governed, active=True)
    assert decision.outcome is Outcome.ALLOW
    assert decision.detail["skipped"] == "stop_hook_already_active"


def test_hors_governed_l_ecriture_hors_tache_avertit_sans_bloquer(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="orchestrated", task_id="bootstrap")
    _write(tmp_path)
    rendered, decision = _stop(tmp_path)
    assert decision.outcome is Outcome.ALLOW
    assert "decision" not in rendered
    assert REMEDE in rendered["systemMessage"]
    assert decision.detail["blocked_on"] == "no_active_task"


# ── (c) après task_add + claim : le lien session ↔ tâche ─────────────────────


def _ouvre_et_reclame(root: Path, *, actor: str = "claude") -> str:
    service = TaskService(root)
    added = service.add("Refuser la cloture hors tache", (ACCEPTATION,), owner=actor, actor=actor, ready=True)
    service.context(added.task.id)  # le bundle que le gate ready_to_in_progress exige
    service.claim(added.task.id, actor, host="local")
    return added.task.id


def test_le_service_add_ouvre_la_mission_et_peut_rendre_la_tache_prete(governed: Path, tmp_path: Path) -> None:
    # Projet enrôlé : `setup_standard_profile` a déjà ouvert une mission, la tâche s'y range.
    service = TaskService(governed)
    added = service.add("Une tache", ("un critere",), owner="amelia", actor="amelia", ready=True)
    assert added.mission_created is False
    assert added.task.mission_id == service.ledger.list_missions()[0].id
    assert added.task.status.value == "ready"
    assert added.board_path is not None and added.board_path.is_file()
    again = service.add("Une autre", ("un critere",), actor="amelia")
    assert again.mission_created is False and again.task.mission_id == added.task.mission_id
    assert again.task.status.value == "proposed"
    # Projet sans ledger : la mission « Travaux courants » est ouverte à l'occasion.
    bare = tmp_path / "bare"
    bare.mkdir()
    first = TaskService(bare).add("Premiere", ("un critere",), actor="amelia")
    assert first.mission_created is True
    assert TaskService(bare).ledger.get_mission(first.mission_id).title == "Travaux courants"  # type: ignore[union-attr]
    assert first.board_path is None, "pas de board sans _grimoire/standard/"


def test_le_service_add_exige_un_critere_comme_le_ledger(governed: Path) -> None:
    from grimoire.core.exceptions import GrimoireMissionError

    with pytest.raises(GrimoireMissionError, match="acceptance"):
        TaskService(governed).add("Sans critere", (), actor="amelia")
    with pytest.raises(GrimoireMissionError, match="acceptance"):
        TaskService(governed).add("Critere vide", ("   ",), actor="amelia")


def test_le_premier_prompt_attache_la_session_au_claim_actif(governed: Path) -> None:
    tid = _ouvre_et_reclame(governed)
    assert resolve_active_task(governed).source == "ledger_claim"

    rendered, decision = _prompt(governed)
    assert decision.detail["task_id"] == tid
    assert decision.detail["session_attached"] is True
    context = rendered["hookSpecificOutput"]["additionalContext"]
    assert f"Tâche courante : {tid}" in context and REMEDE not in context

    service = TaskService(governed)
    task = service.require(tid)
    assert task.claim is not None
    assert task.claim.session_id == SESSION
    assert task.claim.session_host == HostId.CLAUDE_CODE_CLI.value
    kinds = [e.event_type for e in service.ledger.list_events(tid)]
    assert kinds.count("task.session_attached") == 1, "un événement, jamais une réécriture"

    journal = load_session_state(governed, SESSION, now_iso="ignored")
    assert journal.task_id == tid

    # Le second prompt ne ré-attache rien : l'historique reste ce qu'il est.
    _prompt(governed)
    kinds = [e.event_type for e in service.ledger.list_events(tid)]
    assert kinds.count("task.session_attached") == 1


def test_une_session_differente_ne_vole_pas_le_claim(governed: Path) -> None:
    tid = _ouvre_et_reclame(governed)
    _prompt(governed)
    autre = {**_payload("UserPromptSubmit", governed, prompt="salut"), "session_id": "autre-session"}
    _, decision = _hook(governed, HookEvent.USER_PROMPT_SUBMIT, autre)
    assert decision.detail["session_attached"] is False
    assert TaskService(governed).require(tid).claim.session_id == SESSION  # type: ignore[union-attr]


def test_apres_le_claim_stop_ne_bloque_plus_pour_ce_motif(governed: Path) -> None:
    _ouvre_et_reclame(governed)
    _prompt(governed)
    _write(governed)
    _, decision = _stop(governed)
    # Les gates de preuve de la tâche réclamée peuvent être rouges (c'est
    # leur rôle) ; ce test ne juge que le motif « hors tâche ».
    assert decision.detail.get("blocked_on") != "no_active_task"
    assert REMEDE not in decision.reason


def test_la_carte_expose_session_host_et_commande_de_reprise(governed: Path) -> None:
    from grimoire.tools.workspace_api import task_view, tasks_view

    tid = _ouvre_et_reclame(governed)
    _prompt(governed)
    card = next(t for t in tasks_view(governed)["tasks"] if t["id"] == tid)
    assert card["session_id"] == SESSION
    assert card["host"] == HostId.CLAUDE_CODE_CLI.value
    assert card["resume_command"] == f"claude --resume {SESSION}"
    assert task_view(governed, tid)["resume_command"] == card["resume_command"]


def test_la_carte_n_invente_pas_de_commande_pour_un_autre_hote(governed: Path) -> None:
    from grimoire.tools.workspace_api import tasks_view

    tid = _ouvre_et_reclame(governed)
    payload = _payload("UserPromptSubmit", governed, prompt="salut")
    run_hook(payload, host_id=HostId.GITHUB_COPILOT, event=HookEvent.USER_PROMPT_SUBMIT, project_root=governed)
    card = next(t for t in tasks_view(governed)["tasks"] if t["id"] == tid)
    assert card["session_id"] == SESSION
    assert card["host"] == HostId.GITHUB_COPILOT.value
    assert card["resume_command"] == ""


def test_une_tache_sans_claim_n_a_ni_session_ni_hote(governed: Path) -> None:
    from grimoire.tools.workspace_api import tasks_view

    added = TaskService(governed).add("Proposee", ("c",), actor="amelia")
    card = next(t for t in tasks_view(governed)["tasks"] if t["id"] == added.task.id)
    assert (card["session_id"], card["host"], card["resume_command"]) == ("", "", "")


# ── le test de fait : la commande grimoire-hook, stdin → stdout ──────────────


def _grimoire_hook(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], root: Path, event: str, payload: dict[str, Any]
) -> dict[str, Any]:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert hook_main(["--host", "claude", "--event", event, "--project-root", str(root)]) == 0
    return dict(json.loads(capsys.readouterr().out))


def test_de_fait_rejoue_par_la_commande_grimoire_hook(
    governed: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # (a) sans tâche, le contexte contient le remède
    out = _grimoire_hook(
        monkeypatch, capsys, governed, "UserPromptSubmit", _payload("UserPromptSubmit", governed, prompt="go")
    )
    assert REMEDE in out["hookSpecificOutput"]["additionalContext"]

    # (b) après une écriture journalisée, Stop est bloqué avec le remède
    _grimoire_hook(
        monkeypatch,
        capsys,
        governed,
        "PostToolUse",
        _payload(
            "PostToolUse",
            governed,
            tool_name="Write",
            tool_input={"file_path": str(governed / "notes.md"), "content": "x"},
            tool_response={},
        ),
    )
    out = _grimoire_hook(monkeypatch, capsys, governed, "Stop", _payload("Stop", governed, stop_hook_active=False))
    assert out["decision"] == "block" and REMEDE in out["reason"]

    # (c) après task_add + claim, la carte porte session_id, le journal task_id, Stop ne bloque plus pour ce motif
    tid = _ouvre_et_reclame(governed)
    out = _grimoire_hook(
        monkeypatch, capsys, governed, "UserPromptSubmit", _payload("UserPromptSubmit", governed, prompt="suite")
    )
    assert f"Tâche courante : {tid}" in out["hookSpecificOutput"]["additionalContext"]
    from grimoire.tools.workspace_api import tasks_view

    card = next(t for t in tasks_view(governed)["tasks"] if t["id"] == tid)
    assert card["session_id"] == SESSION
    journal = json.loads((governed / "_grimoire-output/.runs" / f"session-{SESSION}.json").read_text(encoding="utf-8"))
    assert journal["task_id"] == tid
    out = _grimoire_hook(monkeypatch, capsys, governed, "Stop", _payload("Stop", governed, stop_hook_active=False))
    assert REMEDE not in out.get("reason", "")


# ── task_add par MCP : le même service, la même validation ───────────────────


def test_task_add_mcp_passe_par_le_meme_service(governed: Path) -> None:
    pytest.importorskip("mcp", reason="extra optionnel grimoire-kit[mcp] non installé")
    from grimoire.mcp.server import task_add

    created = json.loads(
        task_add(
            title="Ouvrir une tache par MCP",
            acceptance=["un critere"],
            owner="claude",
            expected_evidence=["pytest vert"],
            actor="claude",
            ready=True,
            project_path=str(governed),
        )
    )
    assert created["status"] == "ready" and created["board"] == "ready"
    assert created["mission_created"] is False, "setup_standard_profile a déjà ouvert la mission"
    assert created["mission_id"] == TaskService(governed).ledger.list_missions()[0].id
    assert created["expected_evidence"] == ["pytest vert"]
    assert TaskService(governed).require(created["id"]).owner == "claude"

    def body(result: Any) -> dict[str, Any]:
        # Un refus franc porte ``isError`` : un ``CallToolResult`` dont le contenu est le JSON.
        if isinstance(result, str):
            return dict(json.loads(result))
        return dict(json.loads("".join(getattr(block, "text", "") for block in result.content)))

    refused = body(task_add(title="Sans critere", acceptance=[], project_path=str(governed)))
    assert "acceptance" in refused["error"]
    blank = body(task_add(title="Critere blanc", acceptance=["  "], project_path=str(governed)))
    assert "acceptance" in blank["error"]


def test_le_cli_task_add_ready_et_le_mcp_rendent_la_meme_forme(governed: Path) -> None:
    from typer.testing import CliRunner

    from grimoire.cli.app import app

    result = CliRunner().invoke(
        app,
        [
            "-o",
            "json",
            "task",
            "add",
            "Par le CLI",
            "-a",
            "un critere",
            "--owner",
            "amelia",
            "--ready",
            "--project-root",
            str(governed),
            "--ledger-root",
            str(governed / DEFAULT_LEDGER_RELPATH),
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["status"] == "ready" and data["board"] == "ready"
    assert data["mission_created"] is False
    assert set(data) >= {"id", "mission_id", "claim", "board", "mission_created", "board_path"} - {"claim"}
