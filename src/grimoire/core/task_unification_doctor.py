"""ADR-007 point 4 — le check ``task_unification`` de ``grimoire doctor``.

Extrait hors de :mod:`grimoire.cli.app` (au lieu d'y grossir un fichier déjà
au-dessus du seuil de taille, ``scripts/code-ratchet-baseline.json``) : la
seule appelante est la commande ``doctor``.

``grimoire standard init`` ouvre désormais le Mission Ledger pour un projet
nouvellement enrôlé, mais un board scaffoldé par une version antérieure du
kit — ou écrit à la main — peut encore manquer de ledger entièrement, ou de
tâches que le board porte déjà. Jamais un ``FAIL`` : c'est l'état d'un
projet avant que ``migrate-standard`` ne tourne, pas un projet cassé — mais
il doit être nommé, avec le remède exact, jamais laissé silencieux.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

__all__ = ["apply_task_unification_check", "task_unification_entry"]


def apply_task_unification_check(project_root: Path, results: list[dict[str, Any]], *, fmt: str, console: Any) -> None:
    """Append the ``task_unification`` doctor entry to *results* and print it.

    The one call `grimoire doctor` (``cli/app.py``) needs — kept to a single
    line there so the check-size ratchet on that file (already grandfathered
    above its threshold) does not see it grow.
    """
    entry = task_unification_entry(project_root)
    if entry is None:
        return
    results.append(entry)
    if fmt != "json":
        tag = "[yellow]WARN[/yellow]" if entry.get("level") == "warn" else "[green]OK[/green]"
        console.print(f"  {tag}  {entry['detail']}")


def task_unification_entry(project_root: Path) -> dict[str, Any] | None:
    """Un résultat ``doctor`` pour ``task_unification``, ou ``None`` sans rien à dire.

    ``None`` couvre les deux cas silencieux : le projet n'est pas enrôlé au
    standard, ou son ledger est déjà en phase avec son board.
    """
    from grimoire.missions.task_unification import tasks_unification_status

    status = tasks_unification_status(project_root)
    if not status["enrolled"]:
        return None
    if not status["diverged"]:
        return {
            "name": "task_unification",
            "passed": True,
            "detail": "Mission Ledger en phase avec le board du standard.",
        }
    if not status["ledger_exists"]:
        detail = (
            "board du standard sans Mission Ledger (ADR-007) — "
            "`grimoire task migrate-standard .` pour l'ouvrir"
        )
    else:
        missing = len(status["missing_in_ledger"])
        detail = (
            f"{missing} tâche(s) du board absente(s) du Mission Ledger (ADR-007) — "
            "`grimoire task migrate-standard .` pour les importer"
        )
    return {
        "name": "task_unification",
        "passed": True,
        "detail": detail,
        "level": "warn",
        "remedy": "grimoire task migrate-standard .",
    }
