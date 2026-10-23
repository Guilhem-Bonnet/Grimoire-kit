"""``grimoire memory supersede`` et rendu des résultats sourcés (portail E1).

Module voisin de :mod:`grimoire.cli.cmd_memory`, qui est au plafond du cliquet
de lignes. Importer ce module câble ``memory supersede`` sur ``memory_app`` ;
:func:`render_search_table` est le rendu texte de ``memory search``.
"""

from __future__ import annotations

import json
from typing import Any

import typer
from rich.table import Table

from grimoire.cli.cmd_memory import _get_fmt, _load_manager, console, memory_app
from grimoire.core.exceptions import GrimoireMemoryError
from grimoire.memory.knowledge import label_of

__all__ = ["render_search_table"]

_STATE_STYLE = {
    "a_jour": "green",
    "perime": "red",
    "source_disparue": "red",
    "non_verifie": "yellow",
    "hypothese": "dim",
}


def _short_date(fact: dict[str, Any]) -> str:
    when = fact.get("date_source") or fact.get("date_confirmation") or fact.get("date_indexation") or ""
    return str(when)[:10]


def render_search_table(query: str, results: list[Any]) -> Table:
    """Tableau texte : chaque résultat porte sa fraîcheur, sa source et sa date."""
    tbl = Table(title=f"Search: {query}")
    tbl.add_column("ID", style="dim", max_width=12)
    tbl.add_column("Text", max_width=48)
    tbl.add_column("Fraîcheur")
    tbl.add_column("Source", max_width=32)
    tbl.add_column("Date")
    tbl.add_column("Score", justify="right")
    for entry in results:
        fact = entry.provenance or {}
        style = _STATE_STYLE.get(entry.freshness, "white")
        text = entry.text[:45] + "…" if len(entry.text) > 48 else entry.text
        cycle = " (remplacé)" if fact.get("cycle") == "remplacé" else ""
        tbl.add_row(
            entry.id[:12],
            text,
            f"[{style}]{label_of(entry.freshness)}[/{style}]{cycle}",
            entry.source or "inconnue",
            _short_date(fact) or "—",
            f"{entry.score:.3f}" if entry.score else "—",
        )
    return tbl


@memory_app.command("supersede")
def memory_supersede(
    ctx: typer.Context,
    old_id: str = typer.Argument(..., help="ID of the memory being replaced."),
    new_id: str = typer.Argument(..., help="ID of the memory that replaces it (the latest link)."),
) -> None:
    """Replace a decision: OLD is no longer served, but stays readable.

    The replaced memory is hidden from `memory search` unless
    --include-superseded is given. Refuses an unknown or already-replaced
    replacement and any cycle.
    """
    mgr = _load_manager()
    fmt = _get_fmt(ctx)
    try:
        updated = mgr.supersede(old_id, new_id)
    except GrimoireMemoryError as exc:
        if fmt == "json":
            typer.echo(json.dumps({"superseded": False, "old_id": old_id, "new_id": new_id, "error": str(exc)}, indent=2))
        else:
            console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None
    if updated is None:
        if fmt == "json":
            typer.echo(json.dumps({"superseded": False, "old_id": old_id, "new_id": new_id, "error": "not found"}, indent=2))
        else:
            console.print(f"[yellow]Entry not found:[/yellow] {old_id}")
        raise typer.Exit(1)
    if fmt == "json":
        typer.echo(json.dumps({"superseded": True, "old_id": old_id, "new_id": new_id}, indent=2))
        return
    console.print(f"[green]OK[/green] {old_id} remplacé par {new_id} (lisible avec --include-superseded)")
