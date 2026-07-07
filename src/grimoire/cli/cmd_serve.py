"""CLI ``grimoire serve`` — atelier local (UI Forge + API blueprints).

Sert l'UI embarquée (marketplace, éditeur de blueprints, wizard de setup) et
l'API locale (statut, setup, extensions, blueprints, compilation) sur
``127.0.0.1``. C'est un outil local, pas un service : le serveur lit, valide
et écrit des artefacts ; l'exécution appartient au runtime existant.
"""

from __future__ import annotations

import webbrowser
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

console = Console()

_PROJECT_OPTION = typer.Option(
    "--project-root", help="Racine du projet servi (défaut : dossier courant)."
)
_PORT_OPTION = typer.Option("--port", "-p", help="Port local.")
_OPEN_OPTION = typer.Option("--open/--no-open", help="Ouvrir le navigateur.")


def _kit_root() -> Path:
    """Racine des ressources du kit (``archetypes``, ``extensions``, ``version.txt``).

    Editable / checkout : la racine du dépôt (``src/grimoire`` → dépôt),
    reconnue par ``pyproject.toml`` + ``extensions/``. Wheel : le paquet
    ``grimoire/`` (les ressources y sont force-includes).
    """
    import grimoire

    pkg = Path(grimoire.__file__).resolve().parent
    repo = pkg.parent.parent
    if (repo / "pyproject.toml").is_file() and (repo / "extensions").is_dir():
        return repo
    if (pkg / "extensions").is_dir():
        return pkg
    return repo


def serve(
    project_root: Annotated[Path | None, _PROJECT_OPTION] = None,
    port: Annotated[int, _PORT_OPTION] = 4173,
    open_browser: Annotated[bool, _OPEN_OPTION] = True,
) -> None:
    """Lancer l'atelier local : UI Forge + éditeur de blueprints (127.0.0.1)."""
    from grimoire.data import web_path
    from grimoire.tools.forge_server import serve as _serve

    root = (project_root or Path.cwd()).resolve()
    kit_root = _kit_root()
    try:
        ui_dir: Path | None = web_path()
    except FileNotFoundError:
        ui_dir = None

    try:
        server = _serve(root, kit_root, ui_dir, port)
    except OSError as exc:
        console.print(f"[red]✗[/red] Port {port} indisponible : {exc}")
        raise typer.Exit(1) from exc

    url = f"http://127.0.0.1:{port}/"
    console.print(
        f"[bold green]Atelier[/bold green] → [link]{url}[/link]  "
        f"[dim](Ctrl-C pour arrêter)[/dim]"
    )
    console.print(f"[dim]projet servi : {root}[/dim]")
    if ui_dir is None:
        console.print("[yellow]•[/yellow] UI embarquée introuvable — API seule.")
    if open_browser and ui_dir is not None:
        webbrowser.open(f"{url}atelier.html")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[dim]Atelier arrêté.[/dim]")
    finally:
        server.server_close()
