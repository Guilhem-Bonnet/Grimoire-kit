"""ADR-007 point 3 — le pas ``task_unification`` de ``grimoire up``.

Extrait de :mod:`grimoire.cli.cmd_up` (au lieu d'y grossir un fichier passé
au-dessus du seuil de taille, ``scripts/code-ratchet-baseline.json``) : la
seule appelante est :func:`~grimoire.cli.cmd_up.run_up_pipeline`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from grimoire.cli.cmd_up import _UpState

__all__ = ["step_task_unification"]


def step_task_unification(state: _UpState, target: Path, *, dry_run: bool, blocked: bool) -> None:
    """Migre en meilleur effort un board scaffoldé sans ledger.

    `standard init` ouvre désormais le Mission Ledger dès l'init (point 1),
    mais un projet enrôlé par une version antérieure du kit — ou dont le board
    a été écrit à la main — peut porter un ``task-board.yaml`` sans jamais
    avoir ouvert de ledger (issue #521). Ce pas rejoue exactement
    ``grimoire task migrate-standard`` : idempotent (rien à réimporter une
    fois migré) et réversible (instantané horodaté). Jamais bloquant : un
    import qui échoue laisse `doctor` nommer la divergence et son remède,
    plutôt que de faire échouer `up` sur un projet qu'il vient par ailleurs de
    remettre en état.
    """
    from grimoire.cli.cmd_up import StepResult
    from grimoire.core.exceptions import GrimoireError
    from grimoire.core.standard_state import is_standard_enrolled
    from grimoire.missions.task_unification import migrate_standard_tasks, tasks_unification_status

    if blocked:
        state.steps.append(StepResult("task_unification", "skipped", "blocked: no project configuration"))
        return

    if not is_standard_enrolled(target):
        state.steps.append(StepResult("task_unification", "skipped", "project not enrolled in the standard"))
        return

    status = tasks_unification_status(target)
    if not status["diverged"]:
        state.steps.append(StepResult("task_unification", "done", "Mission Ledger already covers the board"))
        return

    if dry_run:
        missing = len(status["missing_in_ledger"])
        state.steps.append(StepResult(
            "task_unification", "planned",
            f"migrate-standard would import {missing} task(s) missing from the Mission Ledger",
        ))
        return

    try:
        report = migrate_standard_tasks(target)
    except (GrimoireError, OSError) as exc:
        state.steps.append(StepResult("task_unification", "failed", f"migrate-standard error: {exc}"))
        return

    if report.tasks_imported:
        state.steps.append(StepResult(
            "task_unification", "changed",
            f"{report.tasks_imported} task(s) migrated from task-board.yaml to the Mission Ledger "
            f"(snapshot {report.snapshot_path} — restore with "
            f"`grimoire task migrate-standard --restore {report.stamp}`)",
        ))
        state.actions.append(f"Migrated {report.tasks_imported} task(s) from task-board.yaml to the Mission Ledger")
    else:
        state.steps.append(StepResult("task_unification", "done", "Mission Ledger already covers the board"))
