"""La commande de notification dans `grimoire doctor` (I1).

Hors de ``cli/app.py`` : ce fichier est sous un cliquet de taille qui ne le laisse que rétrécir.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import Console


def apply_notify_check(results: list[dict[str, Any]], project_root: Path, *, fmt: str, console: Console) -> None:
    """Signale une ``notify.command`` illisible (guillemets non fermés). Rien n'est dit sans commande.

    WARN, jamais FAIL : la notification est au mieux, une commande cassée ne bloque rien mais ne prévient personne.
    """
    from grimoire.missions.decisions import load_notify_command
    from grimoire.tools.workspace_notify import command_problem

    command = load_notify_command(project_root)
    if not command:
        return
    problem = command_problem(command)
    detail = (
        f"notify.command illisible ({problem}) : aucune notification ne partira"
        if problem else "notify.command lisible"
    )
    results.append({"name": "notify_command", "passed": not problem, "detail": detail, "level": "warn" if problem else "ok"})
    if fmt != "json":
        console.print(f"  {'[yellow]WARN[/yellow]' if problem else '[green]OK[/green]'}  {detail}")
