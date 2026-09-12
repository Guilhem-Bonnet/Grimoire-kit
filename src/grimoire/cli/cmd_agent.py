"""``grimoire agent`` — inspect and manage a project's own agent overrides.

Only one command lives here today: ``agent override convert`` (issue #427),
the migration path ``grimoire up`` names but never applies on its own (see
``_step_override_review`` in :mod:`grimoire.cli.cmd_up`). Named as its own
top-level group rather than folded into an existing one — ``registry`` reads
the kit's own catalog, ``standard`` is the governed profile — because a
project's agent overrides are neither.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

console = Console(stderr=True)

agent_app = typer.Typer(help="Inspect and manage this project's agent overrides.")
override_app = typer.Typer(help="Compare, convert and review agent overrides against the kit.")
agent_app.add_typer(override_app, name="override")

_convert_name_arg = typer.Argument(..., help="Agent name (the override file's stem).")
_convert_path_opt = typer.Option(Path(), "--path", help="Project root.")
_convert_dry_run_opt = typer.Option(
    False, "--dry-run", help="Show the resulting partial override without writing it."
)


@override_app.command("convert")
def convert(
    name: str = _convert_name_arg,
    path: Path = _convert_path_opt,
    dry_run: bool = _convert_dry_run_opt,
) -> None:
    """Convert a full-copy override into the equivalent partial one.

    A partial override (``extends: kit``) only redefines the frontmatter
    fields that differ from the kit's own file — the body and every other
    field stay live against future ``grimoire up`` runs. Refuses, naming the
    lines that differ, when the override's body itself diverges from the
    kit's: converting would otherwise silently drop a real customisation, and
    this command never merges text.

    [dim]Examples:[/dim]
      [cyan]grimoire agent override convert ops-engineer --dry-run[/cyan]
      [cyan]grimoire agent override convert ops-engineer[/cyan]
    """
    from grimoire.core.override_drift import OverrideConversionRefusedError, convert_override

    target = path.resolve()
    try:
        result = convert_override(target, name, dry_run=dry_run)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except OverrideConversionRefusedError as exc:
        console.print(f"[red]Refus :[/red] {exc}")
        raise typer.Exit(1) from exc

    fields_label = "aucun champ propre — juste `extends: kit`" if not result.fields else ", ".join(result.fields)

    if dry_run:
        console.print(f"[dim]dry-run — rien n'est écrit. Résultat pour « {name} » :[/dim]\n")
        console.print(result.rendered)
        console.print(f"\n[dim]Champs figés :[/dim] {fields_label}")
    else:
        console.print(f"[green]Converti :[/green] {result.override_ref}")
        console.print(f"[dim]Champs figés :[/dim] {fields_label}")
