"""``grimoire phase`` — la phase du projet (exploration, construction, exploitation)."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions.finition import PHASE_FINITION, project_phase, set_project_phase

console = Console()
phase_app = typer.Typer(help="Phase du projet : exploration, construction ou exploitation.")

_path_opt = typer.Option(Path(), "--path", help="Racine du projet.")


@phase_app.command("set")
def phase_set(phase: str = typer.Argument(..., help=" | ".join(PHASE_FINITION)), path: Path = _path_opt) -> None:
    """Écrit `phase` dans project-context.yaml."""
    root = path.resolve()
    try:
        set_project_phase(root, phase)
    except GrimoireMissionError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"[green]phase[/green] : {phase.strip().lower()}")


@phase_app.command("show")
def phase_show(path: Path = _path_opt) -> None:
    """Affiche la phase déclarée."""
    console.print(project_phase(path.resolve()) or "[dim]aucune phase déclarée[/dim]")
