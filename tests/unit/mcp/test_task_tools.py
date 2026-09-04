"""Les agents lisent, réclament et clôturent leurs tâches par MCP (#138).

La preuve passe par un vrai client du SDK, relié au serveur par des flux en
mémoire : ce que Claude Desktop ou VS Code verraient, à la sérialisation près.
Le projet est enrôlé en profil gouverné par le vrai `setup_standard_profile`,
donc avec les vrais gates du kit, pas un fichier de test.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("mcp", reason="extra optionnel grimoire-kit[mcp] non installé")

import anyio
from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.evidence import EvidenceItem, EvidenceKind, EvidenceProfile, EvidenceService
from grimoire.hosts.decisions import HookInput, decide_activation, decide_task_context
from grimoire.hosts.surface import HookEvent
from grimoire.mcp import server as server_module
from grimoire.mcp.server import mcp, task_claim, task_context, task_list_ready, task_show, task_update
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import DEFAULT_LEDGER_RELPATH

TASK_TOOLS = {"task_list_ready", "task_show", "task_claim", "task_update", "task_context"}
ACCEPTATION = "un client MCP liste, reclame et clot une tache reelle"
EVIDENCE = Path("_grimoire-runtime-output/evidence")
BOARD = Path("_grimoire/standard/task-board.yaml")


# ── un client MCP, pas un appel de fonction ──────────────────────────────────

def _lowlevel() -> Any:
    # `_lowlevel_server` en mcp 2.x, `_mcp_server` en 1.x : même objet, même `run`.
    return getattr(mcp, "_lowlevel_server", None) or mcp._mcp_server


async def _with_client(scenario: Any) -> Any:
    async with create_client_server_memory_streams() as (client_streams, server_streams), anyio.create_task_group() as tg:
        low = _lowlevel()
        tg.start_soon(low.run, server_streams[0], server_streams[1], low.create_initialization_options())
        async with ClientSession(client_streams[0], client_streams[1]) as session:
            await session.initialize()
            result = await scenario(session)
        tg.cancel_scope.cancel()
    return result


def via_client(scenario: Any) -> Any:
    return anyio.run(_with_client, scenario)


async def call(session: ClientSession, tool: str, **args: Any) -> dict[str, Any]:
    result = await session.call_tool(tool, args)
    text = "".join(getattr(block, "text", "") for block in result.content)
    return dict(json.loads(text))


# ── le projet : gouverné pour de vrai ────────────────────────────────────────

@pytest.fixture
def projet(tmp_path: Path) -> Path:
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    registry = tmp_path / "_grimoire/standard/llm-provider-registry.yaml"
    registry.write_text(registry.read_text(encoding="utf-8").replace("enabled: false", "enabled: true", 1), encoding="utf-8")
    return tmp_path


def _ledger(projet: Path) -> MissionLedger:
    return MissionLedger(projet / DEFAULT_LEDGER_RELPATH)


def ouvre(projet: Path) -> str:
    ledger = _ledger(projet)
    mission = ledger.create_mission(title="Travaux", origin="test")
    task = ledger.create_task(mission.id, "Exposer les taches par MCP", acceptance=(ACCEPTATION,), owner="amelia")
    ledger.transition_task(task.id, TaskState.READY, actor_id="amelia")
    return task.id


def prouve(projet: Path, task_id: str) -> None:
    svc = EvidenceService(projet / EVIDENCE)
    pack = svc.create_pack(
        task_id=task_id, profile=EvidenceProfile.STANDARD,
        items=[
            EvidenceItem(id="e1", kind=EvidenceKind.TEST, uri="pytest://", digest="d1", summary=ACCEPTATION),
            EvidenceItem(id="e2", kind=EvidenceKind.LOG, uri="log://", digest="d2", summary=ACCEPTATION),
        ],
        acceptance=(ACCEPTATION,),
    )
    svc.verify(pack, acceptance=(ACCEPTATION,))
    trace = projet / "_grimoire-output/decisions" / task_id / "decision-trace.yaml"
    trace.parent.mkdir(parents=True, exist_ok=True)
    trace.write_text("decisions: []\n", encoding="utf-8")


def board_status(projet: Path, task_id: str) -> str:
    from ruamel.yaml import YAML

    data = YAML(typ="safe").load(projet / BOARD)
    return next(str(t["status"]) for t in data["tasks"] if t["task_id"] == task_id)


def hook_nomme(projet: Path) -> str:
    """La tâche que le hook SessionStart injecte — via la directive, pas le détail."""
    decision = decide_activation(HookInput(event=HookEvent.SESSION_START, project_root=projet))
    ligne = next(line for line in decision.context.splitlines() if "task-envelope.md" in line)
    return ligne.split("evidence/")[1].split("/")[0]


# ── la surface existe pour un client ─────────────────────────────────────────

def test_un_client_voit_les_cinq_outils() -> None:
    async def scenario(session: ClientSession) -> set[str]:
        return {tool.name for tool in (await session.list_tools()).tools}

    assert via_client(scenario) >= TASK_TOOLS


# ── le scénario de l'issue : lister, réclamer, déplacer, clore ───────────────

def test_un_client_mcp_liste_reclame_deplace_et_clot_une_tache_reelle(projet: Path) -> None:
    tid = ouvre(projet)
    assert hook_nomme(projet) == "bootstrap", "avant tout claim, la session est sur bootstrap"

    async def scenario(session: ClientSession) -> None:
        ready = await call(session, "task_list_ready", project_path=str(projet))
        assert [t["id"] for t in ready["tasks"]] == [tid]

        vue = await call(session, "task_show", task_id=tid, project_path=str(projet))
        assert vue["board"] == "ready"
        assert vue["next_moves_require"]["in_progress"] == ["context_bundle", "provider_policy"]

        # Le gate refuse d'abord : pas de context bundle. Le refus nomme le chemin attendu.
        refus = await call(session, "task_claim", task_id=tid, actor="claude", project_path=str(projet))
        assert refus["blocked"] is True
        assert refus["refusals"][0]["evidence"] == "context_bundle"
        assert f"_grimoire-output/context/{tid}/context-bundle.yaml" in refus["refusals"][0]["remedy"]

        # `task_context` produit le bundle — c'est le remède.
        ctx = await call(session, "task_context", task_id=tid, project_path=str(projet))
        assert ctx["task"]["id"] == tid and (projet / ctx["context_bundle_path"]).is_file()

        pris = await call(session, "task_claim", task_id=tid, actor="claude", project_path=str(projet))
        assert pris["transition"] == "ready → claimed" and pris["claim"]["actor_id"] == "claude"

        # Sans argument, l'outil dit sur quelle tâche la session est, et d'où il le sait.
        courante = await call(session, "task_context", project_path=str(projet))
        assert (courante["task_id"], courante["resolved_from"]) == (tid, "ledger_claim")

        assert (await call(session, "task_list_ready", project_path=str(projet)))["count"] == 0

        en_cours = await call(session, "task_update", task_id=tid, action="move", to="running", project_path=str(projet))
        assert en_cours["status"] == "running"

        revue = await call(session, "task_update", task_id=tid, action="move", to="needs_verification", project_path=str(projet))
        assert revue["blocked"] is True
        assert {r["evidence"] for r in revue["refusals"]} == {"evidence_pack", "decision_trace"}

    via_client(scenario)

    # Le board projeté et le hook reflètent le mouvement sans export manuel.
    assert board_status(projet, tid) == "in_progress"
    assert hook_nomme(projet) == tid
    prompt = decide_task_context(HookInput(event=HookEvent.USER_PROMPT_SUBMIT, project_root=projet))
    assert f"Tâche courante : {tid}" in prompt.context

    prouve(projet, tid)

    async def cloture(session: ClientSession) -> None:
        revue = await call(session, "task_update", task_id=tid, action="move", to="needs_verification", project_path=str(projet))
        assert revue["board"] == "review"
        clos = await call(session, "task_update", task_id=tid, action="close", project_path=str(projet))
        assert clos["transition"] == "needs_verification → closed"

    via_client(cloture)
    assert board_status(projet, tid) == "accepted"
    assert _ledger(projet).get_task(tid).status is TaskState.CLOSED  # type: ignore[union-attr]
    assert hook_nomme(projet) == "bootstrap", "une tâche close n'est plus la tâche courante"


def test_bloquer_exige_un_motif_et_le_board_le_montre(projet: Path) -> None:
    tid = ouvre(projet)
    task_context(task_id=tid, project_path=str(projet))
    task_claim(tid, actor="claude", project_path=str(projet))
    task_update(tid, "move", to="running", project_path=str(projet))
    assert "reason" in json.loads(task_update(tid, "block", project_path=str(projet)))["error"]
    bloque = json.loads(task_update(tid, "block", reason="service tiers en panne", project_path=str(projet)))
    assert bloque["status"] == "blocked"
    assert board_status(projet, tid) == "blocked"


# ── contrôle négatif : l'outil MCP ne contourne pas le gate ──────────────────

def test_l_outil_mcp_passe_par_le_gate_et_un_gate_rouge_ne_change_rien(
    projet: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Si `task_update` écrivait au ledger sans consulter `check_transition`,
    l'espion ne serait pas appelé et l'événement serait appendé : ce test
    échouerait sur l'une ou l'autre assertion."""
    from grimoire.missions import service as service_module
    from grimoire.missions.gates import GateRefusal, GateVerdict

    tid = ouvre(projet)
    ledger = _ledger(projet)
    consulte: list[tuple[str, str]] = []

    def gate_rouge(root: Path, task: object, from_board: str, to_board: str) -> GateVerdict:
        consulte.append((from_board, to_board))
        return GateVerdict("espion", "hard_fail", (GateRefusal("preuve-x", "absente", "la produire"),))

    monkeypatch.setattr(service_module, "check_transition", gate_rouge)
    avant = len(ledger.list_events())

    refus = json.loads(task_update(tid, "move", to="cancelled", project_path=str(projet)))
    assert refus["blocked"] is True and refus["transition"] == "espion"
    assert consulte == [("ready", "archived")]
    assert len(MissionLedger(projet / DEFAULT_LEDGER_RELPATH).list_events()) == avant
    assert MissionLedger(projet / DEFAULT_LEDGER_RELPATH).get_task(tid).status is TaskState.READY  # type: ignore[union-attr]


def test_le_refus_du_cli_et_celui_de_mcp_sont_le_meme(projet: Path) -> None:
    """Deux surfaces, un service : la même transition refusée pour la même raison."""
    from typer.testing import CliRunner

    from grimoire.cli.app import app

    tid = ouvre(projet)
    cli = CliRunner().invoke(app, ["--output", "json", "task", "claim", tid, "--project-root", str(projet)])
    par_cli = json.loads(cli.output)
    par_mcp = json.loads(task_claim(tid, project_path=str(projet)))
    assert cli.exit_code == 1 and par_cli["blocked"] is True
    assert par_cli["refusals"] == par_mcp["refusals"]


# ── erreurs lisibles ─────────────────────────────────────────────────────────

def test_sans_ledger_la_liste_est_vide_et_le_dit(tmp_path: Path) -> None:
    out = json.loads(task_list_ready(project_path=str(tmp_path)))
    assert out["count"] == 0 and "task add" in out["note"]
    assert not (tmp_path / DEFAULT_LEDGER_RELPATH).exists()


def test_une_tache_inconnue_et_un_etat_inconnu_sont_nommes(projet: Path) -> None:
    ouvre(projet)
    assert "inconnue" in json.loads(task_show("GAO-nulle-001", project_path=str(projet)))["error"]
    inconnu = json.loads(task_update("x", "move", to="fini", project_path=str(projet)))
    assert "fini" in inconnu["error"] and "running" in inconnu["states"]
    assert "actions" in json.loads(task_update("x", "teleport", project_path=str(projet)))


def test_le_serveur_expose_les_outils_de_tache_sous_leur_nom() -> None:
    assert {name for name in dir(server_module) if name.startswith("task_")} >= TASK_TOOLS
