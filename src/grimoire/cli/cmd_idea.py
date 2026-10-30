"""CLI ``grimoire idea`` — la boîte à idées : garder maintenant, décider plus tard (I13)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.console import Console
from rich.markup import escape

if TYPE_CHECKING:
    from grimoire.missions.ideas import Idea, IdeaStore

idea_app = typer.Typer(help="Boîte à idées : add, list, drop, promote.", no_args_is_help=True)
console = Console()

_PROJECT_ROOT = typer.Option("--project-root", help="Racine du projet.", show_default=False)
_LEDGER_ROOT = typer.Option("--ledger-root", help="Racine du Mission Ledger (relative au projet).")
_DEFAULT_LEDGER = Path("_grimoire-runtime-output/ledger")


def _store(project_root: Path, ledger_root: Path) -> IdeaStore:
    from grimoire.missions.ideas import IdeaStore

    return IdeaStore(project_root.resolve(), ledger_root)


def _fail(message: str) -> typer.Exit:
    console.print(f"[red]✗[/red] {escape(message)}")
    return typer.Exit(1)


def _line(idea: Idea) -> str:
    tasks = f" → {', '.join(idea.tasks)}" if idea.tasks else ""
    return f"{idea.id}  {escape(f'[{idea.state}]')}  {escape(idea.text)}{tasks}"


@idea_app.command("add")
def idea_add(
    text: Annotated[str, typer.Argument(help="L'idée, en une phrase (500 caractères au plus).")],
    project_root: Annotated[Path, _PROJECT_ROOT] = Path(),
    ledger_root: Annotated[Path, _LEDGER_ROOT] = _DEFAULT_LEDGER,
    json_output: Annotated[bool, typer.Option("--json", help="Sortie JSON.")] = False,
) -> None:
    """Garde une idée sans la trier."""
    from grimoire.missions.ideas import IdeaError

    try:
        idea = _store(project_root, ledger_root).add(text, "cli")
    except IdeaError as exc:
        raise _fail(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps(idea.to_dict(), indent=2, ensure_ascii=False))
        return
    console.print(f"[green]✓[/green] {_line(idea)}")


@idea_app.command("list")
def idea_list(
    project_root: Annotated[Path, _PROJECT_ROOT] = Path(),
    ledger_root: Annotated[Path, _LEDGER_ROOT] = _DEFAULT_LEDGER,
    state: Annotated[str, typer.Option("--state", help="Filtrer : open, promoted ou dropped.")] = "",
    json_output: Annotated[bool, typer.Option("--json", help="Sortie JSON.")] = False,
) -> None:
    """Les idées, dans l'ordre où elles ont été gardées."""
    ideas = _store(project_root, ledger_root).list(state)
    if json_output:
        typer.echo(json.dumps([i.to_dict() for i in ideas], indent=2, ensure_ascii=False))
        return
    if not ideas:
        console.print("[dim]Aucune idée.[/dim]")
    for idea in ideas:
        console.print(_line(idea))


@idea_app.command("drop")
def idea_drop(
    idea_id: Annotated[str, typer.Argument(help="Identifiant de l'idée.")],
    project_root: Annotated[Path, _PROJECT_ROOT] = Path(),
    ledger_root: Annotated[Path, _LEDGER_ROOT] = _DEFAULT_LEDGER,
) -> None:
    """Écarte une idée ouverte (le journal garde la trace)."""
    from grimoire.missions.ideas import IdeaError

    try:
        idea = _store(project_root, ledger_root).drop(idea_id)
    except IdeaError as exc:
        raise _fail(str(exc)) from exc
    console.print(f"[green]✓[/green] {_line(idea)}")


@idea_app.command("promote")
def idea_promote(
    idea_id: Annotated[str, typer.Argument(help="Identifiant de l'idée.")],
    project_root: Annotated[Path, _PROJECT_ROOT] = Path(),
    ledger_root: Annotated[Path, _LEDGER_ROOT] = _DEFAULT_LEDGER,
    json_output: Annotated[bool, typer.Option("--json", help="Sortie JSON.")] = False,
) -> None:
    """Crée les tâches que l'intake propose (comme `task add --intake --create`), puis marque l'idée promue."""
    from grimoire.core.exceptions import GrimoireError
    from grimoire.missions.ideas import IdeaError
    from grimoire.missions.service import TaskService

    service = TaskService(project_root.resolve(), ledger_root)
    try:
        idea, created = _store(project_root, ledger_root).promote(idea_id, service)
    except (IdeaError, GrimoireError, ValueError) as exc:
        raise _fail(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps({"idea": idea.to_dict(), "created": created}, indent=2, ensure_ascii=False))
        return
    console.print(f"[green]✓[/green] {_line(idea)}")
    for entry in created:
        console.print(f"    {entry['id']} ({entry['status']}) {escape(entry['title'])}")
        if entry["reason"]:
            console.print(f"    [yellow]{escape(entry['reason'])}[/yellow]")
