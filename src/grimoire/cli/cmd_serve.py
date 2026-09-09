"""CLI ``grimoire serve`` — alias déprécié de ``grimoire cockpit serve``.

Historiquement, ``serve`` servait le projet courant seul et ``cockpit serve``
servait le registre multi-projets — deux commandes pour la même UI (#351).
Décision : une seule commande survit, ``cockpit serve``, capable de s'ouvrir
directement sur le projet courant sans perdre le multi-projet. ``serve`` reste
disponible pour ne pas casser les scripts existants, mais n'est plus
documenté comme commande à part entière.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

console = Console()

_PROJECT_OPTION = typer.Option(
    "--project-root",
    help="Ignoré (déprécié) — le cockpit détecte le projet courant lui-même.",
)
_PORT_OPTION = typer.Option("--port", "-p", help="Port local.")
_OPEN_OPTION = typer.Option("--open/--no-open", help="Ouvrir le navigateur.")


def serve(
    project_root: Annotated[Path | None, _PROJECT_OPTION] = None,
    port: Annotated[int, _PORT_OPTION] = 8420,
    open_browser: Annotated[bool, _OPEN_OPTION] = True,
) -> None:
    """[déprécié] Alias de ``grimoire cockpit serve`` — lancer directement cockpit."""
    from grimoire.cli.cmd_cockpit import serve as cockpit_serve

    console.print(
        "[yellow]⚠[/yellow] [b]grimoire serve[/b] est déprécié, "
        "utilise [b]grimoire cockpit serve[/b] : même commande, désormais seule."
    )
    cockpit_serve(project_root=project_root, port=port, open_browser=open_browser, do_refresh=True, with_tests=False)
