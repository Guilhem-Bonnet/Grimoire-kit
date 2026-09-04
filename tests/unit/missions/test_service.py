"""Le service des tâches : ce que le CLI et le serveur MCP partagent (#138).

Le critère d'acceptation est une action observable : après un claim, le board
projeté et le hook SessionStart nomment la tâche, sans qu'un humain relance
`task board export`. Et un gate rouge laisse le ledger intact.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ruamel.yaml import YAML

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.core.standard_state import resolve_active_task
from grimoire.evidence import EvidenceItem, EvidenceKind, EvidenceProfile, EvidenceService
from grimoire.missions import service as service_module
from grimoire.missions.gates import GATES_FILE, GateRefusal, GateVerdict
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskRefusedError, TaskService

STANDARD = Path("_grimoire/standard")
BOARD = STANDARD / "task-board.yaml"
EVIDENCE = Path("_grimoire-runtime-output/evidence")
ACCEPTATION = "le client MCP clot une tache reelle"

GATES = """\
$schema: "grimoire-agentic-standard-evidence-gates/v1"
transitions:
  - id: proposed_to_ready
    from: proposed
    to: ready
    required_evidence: ["acceptance_criteria", "owner_or_agent_role"]
  - id: in_progress_to_review
    from: in_progress
    to: review
    required_evidence: ["evidence_pack"]
  - id: review_to_accepted
    from: review
    to: accepted
    required_evidence: ["review_gate"]
profile_strictness:
  governed: hard_fail
  starter: advisory
"""


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    (tmp_path / STANDARD).mkdir(parents=True)
    (tmp_path / GATES_FILE).write_text(GATES, encoding="utf-8")
    (tmp_path / STANDARD / "standard-profile.yaml").write_text("profile: governed\n", encoding="utf-8")
    return tmp_path


def ouvre(projet: Path, titre: str = "Exposer les taches", owner: str = "amelia") -> tuple[TaskService, str]:
    service = TaskService(projet)
    mission = service.ledger.create_mission(title="Travaux", origin="test")
    task = service.ledger.create_task(mission.id, titre, acceptance=(ACCEPTATION,), owner=owner)
    return service, task.id


def prouve(projet: Path, task_id: str, *, verifie: bool = True) -> None:
    svc = EvidenceService(projet / EVIDENCE)
    pack = svc.create_pack(
        task_id=task_id, profile=EvidenceProfile.STANDARD,
        items=[
            EvidenceItem(id="e1", kind=EvidenceKind.TEST, uri="pytest://", digest="d1", summary=ACCEPTATION),
            EvidenceItem(id="e2", kind=EvidenceKind.LOG, uri="log://", digest="d2", summary=ACCEPTATION),
        ],
        acceptance=(ACCEPTATION,),
    )
    if verifie:
        svc.verify(pack, acceptance=(ACCEPTATION,))


def board_status(projet: Path, task_id: str) -> str:
    data = YAML(typ="safe").load(projet / BOARD)
    return next(str(t["status"]) for t in data["tasks"] if t["task_id"] == task_id)


def nb_evenements(service: TaskService) -> int:
    return len(service.ledger.list_events())


# ── le mouvement se voit sans export manuel ──────────────────────────────────

def test_chaque_ecriture_reprojette_le_board_et_le_hook_suit(projet: Path) -> None:
    service, tid = ouvre(projet)
    assert not (projet / BOARD).exists()

    move = service.transition(tid, TaskState.READY, "amelia")
    assert move.board_path == projet / BOARD
    assert board_status(projet, tid) == "ready"
    assert resolve_active_task(projet).task_id == "bootstrap", "une tâche prête n'est pas encore la tâche courante"

    service.claim(tid, "claude-session", "claude")
    assert board_status(projet, tid) == "in_progress"
    active = resolve_active_task(projet)
    assert (active.task_id, active.source) == (tid, "ledger_claim")


def test_le_board_suit_meme_quand_il_a_ete_ecrit_a_la_main(projet: Path) -> None:
    (projet / BOARD).write_text("tasks:\n  - task_id: fantome\n    status: in_progress\n", encoding="utf-8")
    service, tid = ouvre(projet)
    service.transition(tid, TaskState.READY, "amelia")
    data = YAML(typ="safe").load(projet / BOARD)
    assert [t["task_id"] for t in data["tasks"]] == [tid], "la carte fantôme ne survit pas à la projection"


def test_un_projet_non_enrole_ne_recoit_pas_de_board(tmp_path: Path) -> None:
    service, tid = ouvre(tmp_path)
    move = service.transition(tid, TaskState.READY, "amelia")
    assert move.board_path is None
    assert not (tmp_path / BOARD).exists()


# ── le scénario complet ──────────────────────────────────────────────────────

def test_list_ready_claim_move_close_de_bout_en_bout(projet: Path) -> None:
    service, tid = ouvre(projet)
    assert service.list_ready() == []
    service.transition(tid, TaskState.READY, "amelia")
    assert [t.id for t in service.list_ready()] == [tid]

    claimed = service.claim(tid, "agent-mcp", "mcp")
    assert claimed.task.claim is not None and claimed.task.claim.actor_id == "agent-mcp"
    assert service.list_ready() == [], "une tâche réclamée n'est plus à prendre"

    service.transition(tid, TaskState.RUNNING, "agent-mcp")
    with pytest.raises(TaskRefusedError) as refus:
        service.transition(tid, TaskState.NEEDS_VERIFICATION, "agent-mcp")
    assert [r.evidence for r in refus.value.verdict.refusals] == ["evidence_pack"]
    assert service.require(tid).status is TaskState.RUNNING

    prouve(projet, tid)
    service.transition(tid, TaskState.NEEDS_VERIFICATION, "agent-mcp")
    ferme = service.transition(tid, TaskState.CLOSED, "agent-mcp")
    assert ferme.to_dict()["transition"] == "needs_verification → closed"
    assert board_status(projet, tid) == "accepted"


def test_sans_verdict_accepte_la_fermeture_est_refusee(projet: Path) -> None:
    service, tid = ouvre(projet)
    service.transition(tid, TaskState.READY, "amelia")
    service.claim(tid, "a", "h")
    service.transition(tid, TaskState.RUNNING, "a")
    prouve(projet, tid, verifie=False)
    service.transition(tid, TaskState.NEEDS_VERIFICATION, "a")
    with pytest.raises(TaskRefusedError) as refus:
        service.transition(tid, TaskState.CLOSED, "a")
    assert refus.value.to_dict()["refusals"][0]["evidence"] == "review_gate"


# ── contrôle négatif : le gate précède l'écriture, toujours ──────────────────

def test_un_gate_rouge_ne_laisse_aucune_trace_au_ledger(projet: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Si une surface contournait `check_transition`, ce test échouerait : le
    gate espion n'aurait pas été consulté, ou l'événement aurait été appendé."""
    service, tid = ouvre(projet)
    service.transition(tid, TaskState.READY, "amelia")
    consulte: list[tuple[str, str]] = []

    def gate_rouge(root: Path, task: object, from_board: str, to_board: str) -> GateVerdict:
        consulte.append((from_board, to_board))
        return GateVerdict("espion", "hard_fail", (GateRefusal("preuve-x", "absente", "la produire"),))

    monkeypatch.setattr(service_module, "check_transition", gate_rouge)
    avant = nb_evenements(service)
    with pytest.raises(TaskRefusedError):
        service.claim(tid, "a", "h")
    assert consulte == [("ready", "in_progress")]
    assert nb_evenements(service) == avant
    assert service.require(tid).status is TaskState.READY


def test_un_gate_qui_plante_ne_laisse_aucune_trace_non_plus(projet: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, tid = ouvre(projet)
    service.transition(tid, TaskState.READY, "amelia")

    def gate_casse(*_: object) -> GateVerdict:
        raise OSError("gates.yaml illisible")

    monkeypatch.setattr(service_module, "check_transition", gate_casse)
    avant = nb_evenements(service)
    with pytest.raises(OSError, match="illisible"):
        service.claim(tid, "a", "h")
    assert nb_evenements(service) == avant


def test_la_machine_a_etats_refuse_avant_le_gate(projet: Path) -> None:
    service, tid = ouvre(projet)
    with pytest.raises(GrimoireMissionError, match="Invalid task transition"):
        service.transition(tid, TaskState.CLOSED, "a")


def test_une_tache_inconnue_est_refusee(projet: Path) -> None:
    service, _ = ouvre(projet)
    with pytest.raises(GrimoireMissionError, match="Tâche inconnue"):
        service.transition("GAO-nulle-part-001", TaskState.READY, "a")


def test_un_profil_permissif_signale_sans_bloquer(projet: Path) -> None:
    (projet / STANDARD / "standard-profile.yaml").write_text("profile: starter\n", encoding="utf-8")
    service, tid = ouvre(projet, owner="")
    move = service.transition(tid, TaskState.READY, "a")
    assert move.advisories and "owner_or_agent_role" in move.advisories[0]
    assert move.task.status is TaskState.READY
