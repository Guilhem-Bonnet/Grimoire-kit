"""Pilotage humain des tâches — lot B de l'issue #638.

L'orchestrateur humain prioritise, commente, dirige et annule depuis le
cockpit ; tout passe par :class:`TaskService` (ADR-007 : la webview n'est
jamais une autorité causale). La consigne posée sur la carte est relue par la
session au tour suivant (``UserPromptSubmit``), marquée livrée, et l'agent peut
en accuser réception. Annuler exige une raison ; annuler une tâche qu'une
autre session tient exige ``force``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from grimoire.hosts.decisions import HookInput, decide_activation, decide_evidence_gate, decide_task_context
from grimoire.hosts.events import HookEvent
from grimoire.missions.board import PRIORITIES, priority_of
from grimoire.missions.gates import GATES_FILE
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskRefusedError, TaskService

STANDARD = Path("_grimoire/standard")
BOARD = STANDARD / "task-board.yaml"
ACCEPTATION = "la consigne du cockpit arrive dans la session"

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
profile_strictness:
  governed: hard_fail
"""


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    (tmp_path / STANDARD).mkdir(parents=True)
    (tmp_path / GATES_FILE).write_text(GATES, encoding="utf-8")
    (tmp_path / STANDARD / "standard-profile.yaml").write_text("profile: governed\n", encoding="utf-8")
    return tmp_path


def ouvre(projet: Path, titre: str = "Exposer les taches", owner: str = "amelia") -> tuple[TaskService, str]:
    """Une tâche prête ; la tâche `bootstrap` existe au ledger comme après `standard init` (ADR-007)."""
    service = TaskService(projet)
    missions = service.ledger.list_missions()
    if missions:
        mission = missions[0]
    else:
        mission = service.ledger.create_mission(title="Travaux", origin="test")
        service.ledger.create_task(
            mission.id, "Bootstrap", acceptance=("le standard est en place",), task_id="bootstrap"
        )
    task = service.ledger.create_task(mission.id, titre, acceptance=(ACCEPTATION,), owner=owner)
    service.transition(task.id, TaskState.READY, owner)
    return service, task.id


def reclame(projet: Path, actor: str = "claude-session") -> tuple[TaskService, str]:
    service, tid = ouvre(projet)
    service.claim(tid, actor, "claude")
    return service, tid


def board_tasks(projet: Path) -> list[dict]:
    data = YAML(typ="safe").load(projet / BOARD)
    return list(data["tasks"])


def prompt(projet: Path, session_id: str = "s-1") -> str:
    hook = HookInput(event=HookEvent.USER_PROMPT_SUBMIT, project_root=projet, session_id=session_id)
    return decide_task_context(hook).context


# ── priorité ─────────────────────────────────────────────────────────────────


def test_prioriser_change_la_priorite_garde_l_historique_et_reordonne_le_board(projet: Path) -> None:
    _, a = ouvre(projet, "Tache A")
    _, b = ouvre(projet, "Tache B")
    service = TaskService(projet)  # une instance fraîche : le cache du ledger est par instance
    assert priority_of(service.require(a)) == "medium", "dérivée du risk_profile standard"

    moved = service.prioritize(b, "high", "guilhem", reason="bloque la release")
    assert moved.task.priority == "high"
    assert priority_of(service.require(b)) == "high"
    assert [t["task_id"] for t in board_tasks(projet) if t["status"] == "ready"] == [b, a], (
        "tri par priorité dans la colonne"
    )

    service.prioritize(b, "low", "guilhem")
    events = [e for e in service.ledger.list_events(b) if e.event_type == "task.prioritized"]
    assert [(e.payload["from_priority"], e.payload["to_priority"]) for e in events] == [("", "high"), ("high", "low")]
    assert events[0].payload["reason"] == "bloque la release"
    assert [t["task_id"] for t in board_tasks(projet) if t["status"] == "ready"] == [a, b]


def test_une_priorite_inconnue_est_refusee_sans_rien_ecrire(projet: Path) -> None:
    service, tid = ouvre(projet)
    avant = len(service.ledger.list_events())
    with pytest.raises(ValueError, match="urgent"):
        service.prioritize(tid, "urgent", "guilhem")
    assert len(service.ledger.list_events()) == avant
    assert "low" in PRIORITIES and "high" in PRIORITIES


# ── commentaires et consignes ────────────────────────────────────────────────


def test_commenter_ajoute_au_journal_append_only_et_ne_touche_pas_a_l_etat(projet: Path) -> None:
    service, tid = reclame(projet)
    note = service.comment(tid, "regarde d'abord le test de fait", "guilhem", kind="directive")
    directive = note.directive
    assert directive is not None
    assert directive.author == "guilhem"
    assert directive.kind == "directive"
    assert directive.delivered_at == "" and directive.acknowledged_at == ""

    task = service.require(tid)
    assert task.status is TaskState.CLAIMED
    assert [d.text for d in task.directives] == ["regarde d'abord le test de fait"]
    raw = (projet / "_grimoire-runtime-output/ledger/events.jsonl").read_text(encoding="utf-8").splitlines()
    assert sum(1 for line in raw if json.loads(line)["event_type"] == "task.directive_added") == 1


def test_un_commentaire_vide_ou_d_une_nature_inconnue_est_refuse(projet: Path) -> None:
    service, tid = reclame(projet)
    with pytest.raises(ValueError):
        service.comment(tid, "   ", "guilhem")
    with pytest.raises(ValueError, match="kind"):
        service.comment(tid, "texte", "guilhem", kind="ordre")
    assert service.require(tid).directives == ()


def test_la_consigne_est_injectee_au_prompt_suivant_puis_marquee_livree(projet: Path) -> None:
    """Le critère de l'issue : une consigne écrite dans le cockpit apparaît
    dans le contexte de la session au tour suivant."""
    service, tid = reclame(projet)
    service.comment(tid, "priorise le test rouge-avant", "guilhem", kind="directive")

    context = prompt(projet)
    assert "[Grimoire — consigne de l'orchestrateur] guilhem," in context
    assert "priorise le test rouge-avant" in context
    assert "task ack" in context or "accuse" in context.lower()

    livree = TaskService(projet).require(tid).directives[0]  # instance fraîche : le hook a écrit hors de `service`
    assert livree.delivered_at != ""
    assert "priorise le test rouge-avant" not in prompt(projet), "livrée une fois, pas à chaque tour"


def test_la_consigne_est_aussi_livree_au_session_start(projet: Path) -> None:
    service, tid = reclame(projet)
    service.comment(tid, "commence par lire l'ADR", "guilhem")
    hook = HookInput(event=HookEvent.SESSION_START, project_root=projet, session_id="s-2")
    context = decide_activation(hook).context
    assert "commence par lire l'ADR" in context
    assert TaskService(projet).require(tid).directives[0].delivered_at != ""


def test_accuser_reception_horodate_la_consigne(projet: Path) -> None:
    service, tid = reclame(projet)
    note = service.comment(tid, "relis le CHANGELOG", "guilhem")
    assert note.directive is not None
    ack = service.acknowledge(tid, note.directive.id, "claude-session")
    assert ack.task.directives[0].acknowledged_at != ""
    with pytest.raises(ValueError, match="inconnue"):
        service.acknowledge(tid, "dir-inexistante", "claude-session")


def test_show_et_recall_listent_les_consignes_livrees_ou_non(projet: Path) -> None:
    service, tid = reclame(projet)
    service.comment(tid, "une consigne", "guilhem", kind="directive")
    recall = service.recall(tid)
    assert [d.text for d in recall.directives] == ["une consigne"]
    assert "une consigne" in recall.text


# ── annulation ───────────────────────────────────────────────────────────────


def test_annuler_sans_raison_est_refuse_et_ne_change_rien(projet: Path) -> None:
    service, tid = reclame(projet)
    with pytest.raises(TaskRefusedError) as refus:
        service.cancel(tid, "", "guilhem")
    assert refus.value.to_dict()["blocked"] is True
    assert any(r.evidence == "reason" for r in refus.value.verdict.refusals)
    assert service.require(tid).status is TaskState.CLAIMED


def test_annuler_une_tache_tenue_par_une_autre_session_exige_force(projet: Path) -> None:
    service, tid = reclame(projet, actor="claude-session")
    service.transition(tid, TaskState.RUNNING, "claude-session")
    with pytest.raises(TaskRefusedError) as refus:
        service.cancel(tid, "chantier abandonné", "guilhem")
    assert "claude-session" in str(refus.value)
    assert service.require(tid).status is TaskState.RUNNING

    moved = service.cancel(tid, "chantier abandonné", "guilhem", force=True)
    assert moved.task.status is TaskState.CANCELLED
    assert moved.to_dict()["transition"] == "running → cancelled"
    event = [e for e in service.ledger.list_events(tid) if e.event_type == "task.transitioned"][-1]
    assert event.payload["reason"] == "chantier abandonné"
    assert event.payload["forced"] is True


def test_le_detenteur_annule_sa_propre_tache_sans_force(projet: Path) -> None:
    service, tid = reclame(projet, actor="claude-session")
    moved = service.cancel(tid, "doublon de GAO-autre", "claude-session")
    assert moved.task.status is TaskState.CANCELLED


def test_une_tache_annulee_depuis_le_cockpit_est_annoncee_au_prompt_suivant_et_le_stop_ne_bloque_plus(
    projet: Path,
) -> None:
    service, tid = reclame(projet, actor="claude-session")
    assert f"Tâche courante : {tid}" in prompt(projet, "s-9")

    service.cancel(tid, "l'issue est close en amont", "guilhem", force=True)

    context = prompt(projet, "s-9")
    assert tid in context and "annulée" in context
    assert "l'issue est close en amont" in context
    assert "annulée" not in prompt(projet, "s-9"), "annoncée une fois par session"

    stop = decide_evidence_gate(HookInput(event=HookEvent.STOP, project_root=projet, session_id="s-9"))
    assert stop.outcome.value != "block"
