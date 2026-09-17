"""ADR-007 point 1 — ``standard init`` ouvre le Mission Ledger pour la tâche bootstrap.

Extrait de :mod:`grimoire.core.agentic_standard` (au lieu d'y grossir un fichier déjà
au-dessus du seuil de taille, ``scripts/code-ratchet-baseline.json``) : la seule
appelante est :func:`~grimoire.core.agentic_standard.setup_standard_profile`, pour
l'artefact ``task_board``.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["ensure_task_board_via_ledger"]

# ADR-007 point 1 — la mission de rattachement pour la tâche bootstrap ouverte par
# `standard init`, distincte de `task_unification.TASK_UNIFICATION_MISSION_ID`
# (celle-ci sert la migration d'un board scaffoldé *sans* ledger ; celle-là ouvre
# le ledger dès l'init, cas qui ne se recoupent jamais : un projet qui vient d'être
# initialisé via ce chemin n'a par construction rien à migrer).
_MISSION_ID = "MIS-standard-bootstrap-001"
_MISSION_TITLE = "Standard bootstrap"
_ACTOR = "standard-init"
# Le template statique qu'on remplace codait déjà "bootstrap" en dur dans le
# board, quel que soit le `--task-id` de la commande (celui-ci ne pilote que les
# chemins d'enveloppe des *autres* artefacts, jamais le contenu de task-board.yaml).
# On préserve ce comportement pour rester équivalent au gate check existant.
_TASK_ID = "bootstrap"
_TASK_TITLE = "Bootstrap agentic standard runtime"
_ACCEPTANCE = ("Standard artifacts are generated and verified.",)
_OWNER = "project-maintainer"


def ensure_task_board_via_ledger(root: Path, dest: Path, *, project_name: str) -> None:
    """Ouvre le Mission Ledger pour la tâche bootstrap et projette le board (ADR-007).

    Remplace la copie du template YAML statique : ``standard init`` écrit
    désormais la tâche ``bootstrap`` via ``ledger.create_mission`` +
    ``ledger.create_task``, puis régénère ``task-board.yaml`` depuis le ledger
    (``build_board``/``write_board``), exactement comme le fait déjà
    ``TaskService.project_board()`` pour chaque transition. Idempotent :
    rejouer sur un projet déjà initialisé ne recrée ni mission ni tâche —
    seule la projection est réécrite.

    *dest* est le chemin de destination déjà résolu et confiné par
    l'appelante (``setup_standard_profile``, via ``_ensure_inside_root``) —
    cette fonction ne le recalcule pas elle-même à partir de *root* seul, ce
    qui donnait à l'analyse statique un second chemin d'écriture non passé
    par ce garde-fou (CodeQL ``py/path-injection`` sur la PR #587).
    """
    from grimoire.core.standard_state import invalidate_cache
    from grimoire.missions.board import build_board, write_board
    from grimoire.missions.ledger import MissionLedger
    from grimoire.missions.schemas import MissionState
    from grimoire.missions.service import DEFAULT_LEDGER_RELPATH

    ledger = MissionLedger(root / DEFAULT_LEDGER_RELPATH)
    if ledger.get_mission(_MISSION_ID) is None:
        mission = ledger.create_mission(
            _MISSION_TITLE,
            origin="standard-init",
            description="Tâche bootstrap ouverte par `grimoire standard init` (ADR-007).",
            created_by=_ACTOR,
            mission_id=_MISSION_ID,
        )
        ledger.transition_mission(mission.id, MissionState.OPEN, actor_id=_ACTOR, reason="standard init")
    if ledger.get_task(_TASK_ID) is None:
        ledger.create_task(
            _MISSION_ID,
            _TASK_TITLE,
            acceptance=_ACCEPTANCE,
            owner=_OWNER,
            task_id=_TASK_ID,
        )
    write_board(dest, build_board(ledger, project=project_name))
    invalidate_cache(root)
