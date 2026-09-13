"""CLI ``grimoire needs`` — la vue humaine sur la résolution des besoins d'exécution (issue #205).

Un flow déclare un besoin (``test-runner``), jamais une commande. La
résolution (:mod:`grimoire.core.execution_needs`) est la même que celle que
``grimoire flow run``/``resume`` appliquent en silence au chargement d'une
acceptance ``run_need`` — cette commande ne recalcule rien de différent,
elle montre le verdict et sa source avant qu'un blueprint n'en dépende.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from grimoire.core.execution_needs import resolve_execution_needs

needs_app = typer.Typer(help="Résoudre les besoins d'exécution d'un flow (issue #205).", no_args_is_help=False)
console = Console()

_PROJECT_ROOT_OPTION = typer.Option("--project-root", help="Racine du projet.", show_default=False)
_JSON_OPTION = typer.Option("--json", help="Sortie JSON.")


def _get_fmt(ctx: typer.Context) -> str:
    return str((ctx.obj or {}).get("output", "text") or "text")


@needs_app.command("resolve")
def needs_resolve(
    ctx: typer.Context,
    project_root: Annotated[Path, _PROJECT_ROOT_OPTION] = Path(),
    json_output: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Résout chaque besoin du catalogue pour ce projet, et dit d'où vient la commande.

    Trois sources possibles par besoin : ``declared`` (``needs.commands`` dans
    ``project-context.yaml``), ``detected`` (un marqueur de projet connu —
    ``pyproject.toml``, ``package.json``, ``Cargo.toml``, ``go.mod``), ou
    ``unresolved`` — le même verdict qu'une acceptance ``run_need`` recevrait
    au chargement d'un blueprint, avant tout refus nommé.
    """
    root = project_root.resolve()
    resolved = resolve_execution_needs(root)
    as_json = json_output or _get_fmt(ctx) == "json"

    if as_json:
        typer.echo(
            json.dumps(
                {
                    need_id: {
                        "command": r.command,
                        "source": r.source,
                        "evidence": r.evidence,
                    }
                    for need_id, r in resolved.items()
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    table = Table(title=f"Besoins d'exécution — {root}")
    for column in ("Besoin", "Source", "Commande", "Preuve"):
        table.add_column(column)
    for need_id, r in resolved.items():
        table.add_row(
            need_id,
            r.source,
            r.command or "[dim]—[/dim]",
            r.evidence or "[dim]—[/dim]",
        )
    console.print(table)

    unresolved = [need_id for need_id, r in resolved.items() if not r.resolved]
    if unresolved:
        console.print(
            f"[yellow]non résolus[/yellow] : {', '.join(unresolved)} — déclarez needs.commands dans "
            "project-context.yaml, ou un marqueur de projet reconnu."
        )
