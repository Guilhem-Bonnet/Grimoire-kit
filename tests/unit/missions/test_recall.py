"""Le rappel de tâche : ce que la mémoire du projet sait avant un claim (#141).

Le critère d'acceptation de l'issue, opposé tel quel : sur une tâche dont une
jumelle a échoué précédemment, le rappel fait remonter la cause du blocage
passé — vérifiable dans le rappel produit, sans backend mémoire configuré.
Chaque test décrit un défaut qu'il empêche : le retirer fait échouer le test,
jamais seulement passer silencieusement.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.memory.backends.local import LocalMemoryBackend
from grimoire.memory.manager import MemoryManager
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.recall import (
    DEFAULT_LEDGER_RELPATH,
    build_task_recall,
    consolidate_task_memory,
)
from grimoire.missions.schemas import DependencyKind, TaskDependency, TaskState

ACCEPTATION = "le rappel remonte la cause du blocage passé"


@pytest.fixture
def ledger(tmp_path: Path) -> MissionLedger:
    return MissionLedger(tmp_path / DEFAULT_LEDGER_RELPATH)


@pytest.fixture
def memoire(tmp_path: Path) -> MemoryManager:
    """Un vrai backend mémoire, sans configuration de projet ni embeddings."""
    return MemoryManager.from_backend(LocalMemoryBackend(tmp_path / "memoire.json"))


# ── absence honnête ───────────────────────────────────────────────────────────


def test_absence_honnete_sans_ledger(tmp_path: Path) -> None:
    recall = build_task_recall(tmp_path, "GAO-fantome-001")
    assert recall.is_empty
    assert recall.text == ""


def test_absence_honnete_tache_inconnue(tmp_path: Path, ledger: MissionLedger) -> None:
    ledger.create_mission(title="Travaux", origin="test")
    recall = build_task_recall(tmp_path, "GAO-nulle-part-001")
    assert recall.is_empty


def test_rappel_vide_pour_une_tache_neuve(tmp_path: Path, ledger: MissionLedger) -> None:
    """Une tâche qui n'a encore rien traversé n'a rien à rappeler — et le dit."""
    mission = ledger.create_mission(title="Travaux", origin="test")
    task = ledger.create_task(mission.id, "Ouvrir un module inédit", acceptance=(ACCEPTATION,))

    recall = build_task_recall(tmp_path, task.id)

    assert not recall.is_empty
    assert not recall.has_content
    assert "rien en mémoire" in recall.text


# ── historique propre ─────────────────────────────────────────────────────────


def test_historique_propre_remonte_les_tentatives_precedentes(tmp_path: Path, ledger: MissionLedger) -> None:
    """Une tâche déjà bloquée puis rouverte doit rappeler pourquoi, au reclaim."""
    mission = ledger.create_mission(title="Travaux", origin="test")
    task = ledger.create_task(mission.id, "Brancher le fournisseur tiers", acceptance=(ACCEPTATION,))
    ledger.transition_task(task.id, TaskState.READY, actor_id="a")
    ledger.transition_task(task.id, TaskState.CLAIMED, actor_id="a")
    ledger.transition_task(task.id, TaskState.RUNNING, actor_id="a")
    ledger.transition_task(
        task.id, TaskState.BLOCKED, actor_id="a", reason="clé API absente du fournisseur tiers"
    )
    ledger.transition_task(task.id, TaskState.READY, actor_id="a")
    ledger.transition_task(task.id, TaskState.CLAIMED, actor_id="b")

    recall = build_task_recall(tmp_path, task.id)

    assert any("clé API absente du fournisseur tiers" in line for line in recall.own_history)
    assert "clé API absente du fournisseur tiers" in recall.text


# ── le critère d'acceptation de l'issue : la jumelle qui a échoué ────────────


def test_une_jumelle_qui_a_echoue_remonte_dans_le_rappel(tmp_path: Path, ledger: MissionLedger) -> None:
    """Sans backend mémoire : le seul ledger suffit à faire remonter la cause.

    C'est le critère d'acceptation de l'issue #141, mot pour mot : « sur une
    tâche dont une jumelle a échoué précédemment, le claim fait remonter la
    cause du blocage passé dans le contexte servi à l'agent ».
    """
    mission = ledger.create_mission(title="Migration Postgres", origin="test")
    premiere = ledger.create_task(
        mission.id, "Migrer la base de données vers Postgres 16", acceptance=(ACCEPTATION,)
    )
    ledger.transition_task(premiere.id, TaskState.READY, actor_id="a")
    ledger.transition_task(premiere.id, TaskState.CLAIMED, actor_id="a")
    ledger.transition_task(premiere.id, TaskState.RUNNING, actor_id="a")
    ledger.transition_task(
        premiere.id, TaskState.FAILED, actor_id="a",
        reason="connexion refusée par la base cible : pool épuisé",
    )

    jumelle = ledger.create_task(
        mission.id, "Migrer la base de données vers Postgres 16 (reprise)", acceptance=(ACCEPTATION,)
    )
    ledger.transition_task(jumelle.id, TaskState.READY, actor_id="b")
    ledger.transition_task(jumelle.id, TaskState.CLAIMED, actor_id="b")

    recall = build_task_recall(tmp_path, jumelle.id)

    assert [s.task_id for s in recall.siblings] == [premiere.id]
    sibling = recall.siblings[0]
    assert sibling.status == "failed"
    assert any("pool épuisé" in cause for cause in sibling.causes)
    assert "pool épuisé" in recall.text, "la cause doit être lisible dans le texte servi à l'agent"


def test_un_lien_declare_est_toujours_voisin_meme_sans_recoupement_de_titre(
    tmp_path: Path, ledger: MissionLedger
) -> None:
    """`task link` déclare la voisinage explicitement — un titre sans rapport ne l'efface pas."""
    mission = ledger.create_mission(title="Travaux", origin="test")
    bloquante = ledger.create_task(mission.id, "Ouvrir le pare-feu sortant", acceptance=(ACCEPTATION,))
    ledger.transition_task(bloquante.id, TaskState.READY, actor_id="a")
    ledger.transition_task(bloquante.id, TaskState.CLAIMED, actor_id="a")
    ledger.transition_task(bloquante.id, TaskState.RUNNING, actor_id="a")
    ledger.transition_task(bloquante.id, TaskState.BLOCKED, actor_id="a", reason="ticket réseau en attente")

    dependante = ledger.create_task(
        mission.id, "Rédiger la documentation utilisateur", acceptance=(ACCEPTATION,),
        dependencies=(TaskDependency(kind=DependencyKind.BLOCKS, target=bloquante.id),),
    )

    recall = build_task_recall(tmp_path, dependante.id)

    assert [s.task_id for s in recall.siblings] == [bloquante.id]
    assert recall.siblings[0].relation == "lien déclaré"
    assert "ticket réseau en attente" in recall.text


def test_un_titre_generique_sans_lien_ni_recoupement_suffisant_reste_absent(
    tmp_path: Path, ledger: MissionLedger
) -> None:
    """Un seul mot-clé partagé entre missions différentes ne fait pas une voisine — sinon
    la moitié du ledger apparaîtrait à chaque rappel."""
    mission_a = ledger.create_mission(title="Mission A", origin="test")
    mission_b = ledger.create_mission(title="Mission B", origin="test")
    ledger.create_task(mission_a.id, "Corriger le bug d'affichage", acceptance=(ACCEPTATION,))
    cible = ledger.create_task(mission_b.id, "Corriger le crash au démarrage", acceptance=(ACCEPTATION,))

    recall = build_task_recall(tmp_path, cible.id)

    assert recall.siblings == ()


# ── bornage en tokens ─────────────────────────────────────────────────────────


def test_le_rappel_est_borne_en_tokens(tmp_path: Path, ledger: MissionLedger) -> None:
    mission = ledger.create_mission(title="Grand chantier", origin="test")
    cible = ledger.create_task(mission.id, "Déployer le service de paiement", acceptance=(ACCEPTATION,))
    for i in range(8):
        voisine = ledger.create_task(
            mission.id, f"Déployer le service de paiement (tentative {i})", acceptance=(ACCEPTATION,)
        )
        ledger.transition_task(voisine.id, TaskState.READY, actor_id="a")
        ledger.transition_task(voisine.id, TaskState.CLAIMED, actor_id="a")
        ledger.transition_task(voisine.id, TaskState.RUNNING, actor_id="a")
        ledger.transition_task(
            voisine.id, TaskState.FAILED, actor_id="a",
            reason="échec détaillé numéro " + str(i) + " : " + ("motif verbeux " * 20),
        )

    recall = build_task_recall(tmp_path, cible.id, token_budget=30, sibling_limit=8)

    assert len(recall.text) <= 30 * 4 + 120, "le texte doit rester borné même avec beaucoup à dire"
    assert "tronqué" in recall.text


# ── consolidation à la clôture ou au blocage, et seulement là ────────────────


def test_consolidation_n_ecrit_que_sur_cloture_ou_blocage(
    tmp_path: Path, ledger: MissionLedger, memoire: MemoryManager
) -> None:
    mission = ledger.create_mission(title="Travaux", origin="test")
    task = ledger.create_task(mission.id, "Tâche ordinaire", acceptance=(ACCEPTATION,))
    ledger.transition_task(task.id, TaskState.READY, actor_id="a")
    claimed_task = ledger.transition_task(task.id, TaskState.CLAIMED, actor_id="a")

    assert consolidate_task_memory(memoire, ledger, claimed_task, TaskState.RUNNING, actor="a") is None
    assert memoire.count() == 0

    running_task = ledger.transition_task(task.id, TaskState.RUNNING, actor_id="a")
    entry = consolidate_task_memory(
        memoire, ledger, running_task, TaskState.BLOCKED, actor="a", reason="dépendance manquante"
    )
    assert entry is not None
    assert memoire.count() == 1
    assert "dépendance manquante" in entry.text


def test_consolidation_est_idempotente_sur_le_meme_motif(
    tmp_path: Path, ledger: MissionLedger, memoire: MemoryManager
) -> None:
    """Reclôturer sur le même motif ne double pas la mémoire — `remember` upserte."""
    mission = ledger.create_mission(title="Travaux", origin="test")
    task = ledger.create_task(mission.id, "Tâche répétée", acceptance=(ACCEPTATION,))

    consolidate_task_memory(memoire, ledger, task, TaskState.CLOSED, actor="a")
    consolidate_task_memory(memoire, ledger, task, TaskState.CLOSED, actor="a")

    assert memoire.count() == 1


def test_le_rappel_suivant_voit_ce_que_la_memoire_a_consolide(
    tmp_path: Path, ledger: MissionLedger, memoire: MemoryManager
) -> None:
    """La couche mémoire ajoute ce que le ledger seul ne sait pas relier : deux
    missions différentes, un motif partagé."""
    mission_a = ledger.create_mission(title="Chantier Alpha", origin="test")
    mission_b = ledger.create_mission(title="Chantier Beta", origin="test")
    passee = ledger.create_task(
        mission_a.id, "Configurer le webhook de paiement", acceptance=(ACCEPTATION,)
    )
    consolidate_task_memory(
        memoire, ledger, passee, TaskState.BLOCKED, actor="a",
        reason="webhook de paiement : certificat TLS expiré",
    )

    nouvelle = ledger.create_task(
        mission_b.id, "Réparer le webhook de paiement du portail", acceptance=(ACCEPTATION,)
    )

    recall = build_task_recall(tmp_path, nouvelle.id, memory_manager=memoire)

    assert recall.memory_hits, "la mémoire consolidée doit apparaître même hors de la mission d'origine"
    assert any("certificat TLS expiré" in hit.text for hit in recall.memory_hits)
    assert "certificat TLS expiré" in recall.text


def test_sans_manager_memoire_le_rappel_reste_celui_du_ledger_seul(
    tmp_path: Path, ledger: MissionLedger
) -> None:
    """Un projet sans mémoire configurée dégrade, il ne casse pas."""
    mission = ledger.create_mission(title="Travaux", origin="test")
    task = ledger.create_task(mission.id, "Tâche isolée", acceptance=(ACCEPTATION,))

    recall = build_task_recall(tmp_path, task.id, memory_manager=None)

    assert recall.memory_hits == ()
