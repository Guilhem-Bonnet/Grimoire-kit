"""``gate check --strict`` exécute lui-même ``gate run-tests`` quand aucun run frais n'existe (issue #582 lot G1).

Depuis le lot B, ``gate run-tests`` enregistre un run réel dans
``_grimoire-output/evidence/<task>/test-run.json`` avec l'empreinte de l'arbre
de travail, et ``verify``/``gate check`` relisent ce fichier. Mais c'étaient
deux commandes, et la directive de session devait les nommer toutes les deux
dans l'ordre ; un agent qui n'appelait que ``gate check --strict`` (la commande
que le hook mandate) voyait un avertissement lui demandant d'aller lancer
l'autre. Ce module ferme la boucle : ``gate check --strict`` décide s'il doit
exécuter les tests, les exécute, puis évalue comme avant — ``--no-run``
restaure l'ancien comportement.

Un run n'est lancé que si trois conditions tiennent, dans cet ordre, la moins
chère d'abord :

1. l'état de la tâche doit une preuve d'exécution (:data:`AUTO_RUN_STATES` :
   le même ensemble que celui où le gate exige le context bundle — une tâche
   ``proposed`` ou ``ready`` n'a encore rien à tester) ;
2. le projet a une commande de test connue
   (:func:`grimoire.core.execution_needs.resolve_need`) ;
3. aucun run vert et frais n'est enregistré (empreinte égale à celle de
   l'arbre courant, :mod:`grimoire.core.standard_checks.tree_fingerprint`).

Le hook ``Stop`` n'appelle pas ce module : il évalue les gates via
:func:`grimoire.core.agentic_standard.check_evidence_gates` et ne doit jamais
lancer une suite de tests dans le dos d'une session.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grimoire.core.execution_needs import resolve_need
from grimoire.core.standard_checks.controls import _load_recorded_test_run
from grimoire.core.standard_generation import STANDARD_DIR, normalize_task_id

__all__ = ["AUTO_RUN_STATES", "GateTestRunOutcome", "board_state_of_task", "ensure_fresh_test_run"]

#: États du board où ``gate check --strict`` exécute les tests s'il le faut.
AUTO_RUN_STATES: frozenset[str] = frozenset({"in_progress", "review", "accepted", "released"})


@dataclass(frozen=True, slots=True)
class GateTestRunOutcome:
    """Ce que ``gate check`` a décidé à propos des tests, et pourquoi.

    ``reason`` vaut ``state_owes_no_run``, ``no_test_command``, ``fresh_run``
    ou ``executed`` ; ``command`` est la commande résolue (vide si aucune) ;
    ``ok``/``exit_code`` ne sont renseignés que pour ``executed``.
    """

    ran: bool
    reason: str
    command: str = ""
    ok: bool | None = None
    exit_code: int | None = None
    path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ran": self.ran,
            "reason": self.reason,
            "command": self.command,
            "ok": self.ok,
            "exit_code": self.exit_code,
            "path": str(self.path) if self.path is not None else None,
        }


def board_state_of_task(project_root: Path, task_id: str, *, target_state: str | None = None) -> str:
    """L'état que ``check_evidence_gates`` évaluera : *target_state* s'il est donné, sinon la colonne du board."""
    if target_state:
        return target_state
    from grimoire.core.standard_state import _load_mapping, task_from_board

    board = _load_mapping(project_root.resolve() / STANDARD_DIR / "task-board.yaml")
    return str(task_from_board(board, normalize_task_id(task_id)).get("status") or "")


def _recorded_run_is_fresh_and_green(root: Path, task_id: str) -> bool:
    run = _load_recorded_test_run(root, task_id)
    if run is None or run.get("ok") is not True:
        return False
    stored = run.get("tree_fingerprint")
    if not stored:
        return False
    from grimoire.core.standard_checks.tree_fingerprint import compute_tree_fingerprint

    return bool(stored == compute_tree_fingerprint(root))


def ensure_fresh_test_run(project_root: Path, *, task_id: str, state: str) -> GateTestRunOutcome:
    """Exécute et enregistre les tests si (et seulement si) le gate en a besoin — voir le module."""
    root = project_root.resolve()
    normalized_task_id = normalize_task_id(task_id)
    if state not in AUTO_RUN_STATES:
        return GateTestRunOutcome(ran=False, reason="state_owes_no_run")
    need = resolve_need("test-runner", root)
    if not need.resolved or need.command is None:
        return GateTestRunOutcome(ran=False, reason="no_test_command")
    if _recorded_run_is_fresh_and_green(root, normalized_task_id):
        return GateTestRunOutcome(ran=False, reason="fresh_run", command=need.command)
    # Importé ici : `record_acceptance_test_run` tire `missions.dispatch`
    # (~60 ms d'imports mesurés), un coût que seule l'exécution réelle justifie.
    from grimoire.core.standard_checks.acceptance_test_run import record_acceptance_test_run

    result = record_acceptance_test_run(root, task_id=normalized_task_id)
    return GateTestRunOutcome(
        ran=True,
        reason="executed",
        command=result.command,
        ok=result.ok,
        exit_code=result.exit_code,
        path=result.path,
    )
