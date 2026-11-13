"""GAO-hook-session-001 — ``SessionStart`` : une note une fois, un seul emballage, worker sans bruit.

Constat mesuré par un autre projet enrôlé sur 37 sorties du hook (118 k
caractères) :

- la même note de tâche revenait deux fois — sous le « rappel », puis sous
  « note d'un agent » ;
- la bannière « DONNÉE EXTERNE » était répétée par note (~400 caractères,
  31 % du total) ;
- « Fournisseurs : … » et « N proposition(s) d'artefact en attente » sortaient
  à chaque session de worker, qui ne peut rien en faire.

Les quatre tests ci-dessous rejouent les quatre attendus de la tâche, vus
depuis ``decide_activation`` — l'émetteur réel du ``SessionStart``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts.decisions import HookInput, decide_activation
from grimoire.hosts.decisions.steering import DIRECTIVE_PREFIX, UNTRUSTED_NOTE_PREFIX
from grimoire.hosts.events import HookEvent
from grimoire.missions.schemas import ORIGIN_AGENT, ORIGIN_HUMAN, TaskState
from grimoire.missions.service import TaskService

NOTE_UNE = "vérifie le cache avant de conclure"
NOTE_DEUX = "la CI est lente, ne relance pas trois fois"
CONSIGNE_HUMAINE = "commence par lire l'ADR"

SESSION = "aaaa1111-0000-4000-8000-000000000001"


@pytest.fixture
def projet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un projet gouverné ; les lignes « hors sujet » ont de quoi s'afficher."""
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    # La ligne « propositions » n'a rien à lire sur un projet neuf : on la force.
    monkeypatch.setattr("grimoire.proposals.count_pending", lambda _root: 1)
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    # Le gate ``ready_to_in_progress`` exige un fournisseur activé.
    registry = tmp_path / "_grimoire/standard/llm-provider-registry.yaml"
    registry.write_text(
        registry.read_text(encoding="utf-8").replace("enabled: false", "enabled: true", 1),
        encoding="utf-8",
    )
    return tmp_path


def _claim(projet: Path) -> tuple[TaskService, str]:
    service = TaskService(projet)
    mission = service.ledger.create_mission(title="Travaux", origin="test")
    task = service.ledger.create_task(mission.id, "Un sujet", acceptance=("c'est fait",), owner="guilhem")
    service.transition(task.id, TaskState.READY, "guilhem")
    service.context(task.id)  # le context bundle que le gate réclame au claim
    service.claim(task.id, "claude-session", "claude")
    return service, task.id


def _session_start(projet: Path) -> str:
    hook = HookInput(event=HookEvent.SESSION_START, project_root=projet, session_id=SESSION)
    return decide_activation(hook).context


def _bannieres(context: str) -> int:
    return context.count("DONNÉE EXTERNE")


# ── 1. une note n'apparaît qu'une fois ───────────────────────────────────────


def test_une_note_non_livree_n_apparait_qu_une_fois(projet: Path) -> None:
    """Elle sort par la livraison du hook, pas en plus dans le rappel."""
    service, tid = _claim(projet)
    service.comment(tid, NOTE_UNE, "claude-sub", kind="comment", origin=ORIGIN_AGENT)

    context = _session_start(projet)

    assert context.count(NOTE_UNE) == 1
    assert context.count(UNTRUSTED_NOTE_PREFIX) == 1
    assert "rappel de tâche" not in context.lower(), "aucun rappel vide/faux quand seule une note attend"


def test_une_note_deja_livree_ne_se_repete_pas_a_la_session_suivante(projet: Path) -> None:
    """Livrée une fois, relue une fois : le rappel la garde, la livraison se tait."""
    service, tid = _claim(projet)
    service.comment(tid, NOTE_UNE, "claude-sub", kind="comment", origin=ORIGIN_AGENT)
    _session_start(projet)  # livre et marque la note

    context = _session_start(projet)

    assert context.count(NOTE_UNE) == 1
    assert UNTRUSTED_NOTE_PREFIX not in context, "déjà livrée : rien à relivrer"
    assert _bannieres(context) == 1, "le rappel emballe son bloc une seule fois"


# ── 2. un seul emballage par bloc de notes ───────────────────────────────────


def test_deux_notes_non_humaines_partagent_une_seule_banniere(projet: Path) -> None:
    service, tid = _claim(projet)
    service.comment(tid, NOTE_UNE, "claude-sub", kind="comment", origin=ORIGIN_AGENT)
    service.comment(tid, NOTE_DEUX, "claude-sub2", kind="comment", origin=ORIGIN_AGENT)

    context = _session_start(projet)

    assert _bannieres(context) == 1, "une bannière pour tout le bloc, pas une par note"
    assert NOTE_UNE in context and NOTE_DEUX in context
    assert context.count("<<<GRIMOIRE-UNTRUSTED") == 2, "une ouverture et une fermeture pour le bloc"


def test_la_consigne_humaine_reste_hors_de_l_emballage(projet: Path) -> None:
    service, tid = _claim(projet)
    service.comment(tid, CONSIGNE_HUMAINE, "guilhem", kind="directive", origin=ORIGIN_HUMAN)
    service.comment(tid, NOTE_UNE, "claude-sub", kind="comment", origin=ORIGIN_AGENT)

    context = _session_start(projet)

    assert f"{DIRECTIVE_PREFIX} guilhem" in context and CONSIGNE_HUMAINE in context
    assert _bannieres(context) == 1
    assert context.index(CONSIGNE_HUMAINE) < context.index("<<<GRIMOIRE-UNTRUSTED"), (
        "la consigne humaine se lit avant le bloc de données"
    )


# ── 3 et 4. lignes fournisseurs/propositions selon le type de session ────────


def test_une_session_de_worker_ne_recoit_pas_les_lignes_hors_sujet(
    projet: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, tid = _claim(projet)
    monkeypatch.setenv("GRIMOIRE_TASK_ID", tid)

    context = _session_start(projet)

    assert "Fournisseurs :" not in context
    assert "proposition(s) d'artefact" not in context


def test_une_session_interactive_sans_tache_garde_ces_lignes(projet: Path) -> None:
    context = _session_start(projet)

    assert "Fournisseurs :" in context
    assert "proposition(s) d'artefact" in context
