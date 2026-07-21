"""``grimoire memory`` lexical/docs retrieval commands.

Extracted from :mod:`grimoire.cli.cmd_memory` so the core command module stays
within its code-size ratchet. The commands are registered on the shared Typer
apps (``vector_app`` / ``memory_app``) at import time; importing this module is
what wires ``memory vector sync-docs`` and ``memory reindex-lexical`` into the
CLI. ``grimoire.cli.app`` imports ``memory_app`` from here for that side effect.
"""

from __future__ import annotations

import json

import typer

from grimoire.cli.cmd_memory import (
    _get_fmt,
    _graph_exclude_opt,
    _load_manager,
    _load_manager_context,
    _parse_exclude,
    _parse_paths,
    console,
    memory_app,
    vector_app,
)

__all__ = ["memory_app", "vector_app"]

_docs_paths_opt = typer.Option("docs,README.md", "--paths", help="Comma-separated files or directories to index.")


@vector_app.command("sync-docs")
def memory_vector_sync_docs(
    ctx: typer.Context,
    paths: str = _docs_paths_opt,
    exclude: str = _graph_exclude_opt,
) -> None:
    """Upsert deterministic markdown docs pages into the active memory backend."""
    from grimoire.memory.projections import sync_docs_projection

    mgr, _, root = _load_manager_context()
    stats = sync_docs_projection(
        mgr,
        project_root=root,
        paths=_parse_paths(paths),
        exclude=_parse_exclude(exclude),
    )

    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps(stats, indent=2, default=str))
        return
    console.print("[green]Docs projection synced[/green]")
    console.print(f"  Pages expected : {stats['expected']}")
    console.print(f"  Upserted       : {stats['upserted']}")
    console.print(f"  Unchanged      : {stats['unchanged']}")


@memory_app.command("reindex-lexical")
def memory_reindex_lexical(ctx: typer.Context) -> None:
    """Backfill the lexical companion index from the primary backend.

    Use after enabling hybrid retrieval on a project with pre-existing
    memories.  No-op when the primary backend is already lexical/local.
    """
    mgr = _load_manager()
    mirrored = mgr.reindex_lexical_companion()
    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps({
            "mirrored": mirrored,
            "companion": mgr.lexical_companion is not None,
        }))
        return
    if mgr.lexical_companion is None:
        console.print("[yellow]No lexical companion configured (primary backend is already lexical/local).[/yellow]")
        return
    console.print(f"[green][OK][/green] {mirrored} entries mirrored into the lexical companion index.")
