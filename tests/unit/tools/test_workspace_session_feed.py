"""P69v2 : le bloc Activité — le fil de l'agent dérivé du ledger, jamais inventé.

``task_feed`` raconte claim, transitions, questions/réponses et notes de
progression avec des libellés lisibles ; ``session_feed`` rend l'union des fils
des tâches que la session tient (et le dit quand elle n'en tient aucune) ;
``last_feed_labels`` rend la dernière entrée par tâche en une passe. La
commande ``claude logs`` vient du contrat session (``session_link``), jamais
composée ailleurs. Le brief des workers demande une note de progression —
testé via la commande équivalente du préflight (API publique).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions.schemas import TaskClaim, TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.session_link import logs_command, session_fields
from grimoire.tools.workspace_session_feed import (
    FEED_LIMIT,
    last_feed_labels,
    session_feed,
    task_feed,
)

ACC = "le fil se lit"
SESSION = "41c89d23-0000-0000-0000-000000000000"
HOST = "claude-code-cli"


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    (tmp_path / "_grimoire/standard").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def service(projet: Path) -> TaskService:
    return TaskService(projet)


@pytest.fixture
def mission(service: TaskService) -> str:
    return service.ledger.create_mission(title="Activité", origin="test").id


def _held(service: TaskService, mission: str, titre: str, *, session: str = SESSION) -> str:
    task = service.ledger.create_task(mission, titre, acceptance=(ACC,), owner="amelia")
    service.ledger.transition_task(task.id, TaskState.READY)
    service.ledger.transition_task(
        task.id, TaskState.CLAIMED, actor_id="winston", claim=TaskClaim("winston", "local")
    )
    service.ledger.attach_session(task.id, session, session_host=HOST, actor_id="hook")
    service.ledger.transition_task(task.id, TaskState.RUNNING, actor_id="winston")
    return task.id


# ── Le fil d'une tâche ───────────────────────────────────────────────────────


def test_le_fil_raconte_claim_question_reponse_et_progression(
    projet: Path, service: TaskService, mission: str
) -> None:
    tid = _held(service, mission, "fil complet")
    service.ledger.note_progress(tid, "plan arrêté, tests en cours", actor_id="winston")
    service.ask(tid, "quelle couleur pour le liseré ?", "winston", origin="agent")
    service.answer(tid, "le ton du propriétaire", "guilhem", origin="human")

    feed = task_feed(projet, tid)
    kinds = [e["kind"] for e in feed["entries"]]
    assert kinds[0] == "task.created"
    assert "task.session_attached" in kinds
    assert kinds.count("task.transitioned") >= 3  # ready, claimed, running, waiting, réveil
    labels = {e["kind"]: e["label"] for e in feed["entries"]}
    assert labels["task.created"] == "ouverte par amelia"
    assert "passée réclamée par winston" in labels["task.transitioned"] or any(
        "réclamée" in e["label"] for e in feed["entries"] if e["kind"] == "task.transitioned"
    )
    assert labels["task.session_attached"] == f"session {SESSION[:8]} rattachée au claim"
    assert labels["task.progress_noted"] == "progression : plan arrêté, tests en cours"
    assert labels["task.question_asked"] == "question posée : quelle couleur pour le liseré ?"
    assert labels["task.human_answered"] == "réponse reçue : le ton du propriétaire"
    # Chronologique, du plus ancien au plus récent ; total = ce qui existe.
    assert [e["at"] for e in feed["entries"]] == sorted(e["at"] for e in feed["entries"])
    assert feed["total"] == len(feed["entries"]) <= FEED_LIMIT
    # Le contrat session porte la commande claude logs — jamais composée ici.
    assert feed["session"]["logs_command"] == f"claude logs {SESSION}"


def test_une_transition_bloquee_est_un_echec_et_un_bail_renouvele_n_est_pas_une_entree(
    projet: Path, service: TaskService, mission: str
) -> None:
    tid = _held(service, mission, "bloquée en route")
    claim = service.require(tid).claim
    assert claim is not None
    service.ledger.renew_lease(tid, claim.lease_epoch, actor_id="winston")
    service.ledger.transition_task(tid, TaskState.BLOCKED, actor_id="winston", reason="la clé d'API manque")
    feed = task_feed(projet, tid)
    kinds = [e["kind"] for e in feed["entries"]]
    assert "task.lease_renewed" not in kinds
    blocked = [e for e in feed["entries"] if e["failure"]]
    assert len(blocked) == 1
    assert "bloquée" in blocked[0]["label"] and "la clé d'API manque" in blocked[0]["label"]


def test_une_tache_inconnue_leve_l_erreur_mission(projet: Path, service: TaskService, mission: str) -> None:
    _held(service, mission, "pour le ledger")
    with pytest.raises(GrimoireMissionError):
        task_feed(projet, "GAO-inconnue-000")


# ── Le fil d'une session ─────────────────────────────────────────────────────


def test_le_fil_de_session_unit_les_taches_tenues_et_porte_claude_logs(
    projet: Path, service: TaskService, mission: str
) -> None:
    first = _held(service, mission, "première tenue")
    second = _held(service, mission, "seconde tenue")
    feed = session_feed(projet, SESSION)
    assert sorted(feed["tasks"]) == sorted([first, second])
    assert {e["task_id"] for e in feed["entries"]} == {first, second}
    assert [e["at"] for e in feed["entries"]] == sorted(e["at"] for e in feed["entries"])
    assert feed["logs_command"] == f"claude logs {SESSION}"
    assert feed["note"] == ""


def test_une_session_sans_claim_rend_un_fil_vide_qui_le_dit(
    projet: Path, service: TaskService, mission: str
) -> None:
    _held(service, mission, "tenue par une autre")
    feed = session_feed(projet, "0000dead-0000-0000-0000-000000000000")
    assert feed["entries"] == [] and feed["tasks"] == []
    assert "aucun claim" in feed["note"]
    assert feed["logs_command"] == ""


# ── La ligne sur la carte ────────────────────────────────────────────────────


def test_last_feed_labels_rend_la_derniere_entree_par_tache_en_une_passe(
    projet: Path, service: TaskService, mission: str
) -> None:
    tid = _held(service, mission, "dernière entrée")
    service.ledger.note_progress(tid, "PR ouverte, CI en cours", actor_id="winston")
    found = last_feed_labels(service, {tid})
    assert found[tid]["label"] == "progression : PR ouverte, CI en cours"
    assert found[tid]["at"]
    assert last_feed_labels(service, set()) == {}


# ── Le contrat session et le brief du worker ─────────────────────────────────


def test_le_contrat_session_porte_logs_command_et_un_hote_inconnu_n_en_a_pas(
    projet: Path, service: TaskService, mission: str
) -> None:
    tid = _held(service, mission, "contrat session")
    fields = session_fields(service.require(tid))
    assert fields["logs_command"] == f"claude logs {SESSION}"
    assert fields["resume_command"] == f"claude --resume {SESSION}"
    assert logs_command("hote-inconnu", SESSION) == ""
    assert logs_command(HOST, "") == ""


def test_le_brief_du_worker_demande_une_note_de_progression_sans_garde(
    projet: Path, service: TaskService, mission: str
) -> None:
    from grimoire.tools.workspace_launch_preflight import launch_preflight

    task = service.ledger.create_task(mission, "brief du worker", acceptance=(ACC,), owner="amelia")
    service.ledger.transition_task(task.id, TaskState.READY)
    prompt = launch_preflight(projet, task.id)["commande"]["argv"][-1]
    assert "note ta progression" in prompt
    assert f"grimoire task note {task.id}" in prompt
    assert "rien ne te bloque si tu l'oublies" in prompt
