"""``grimoire memory`` — inspect and manage the memory subsystem.

Sub-commands: status, search, list, export, import, gc, delete.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from grimoire.core.config import GrimoireConfig
from grimoire.core.exceptions import GrimoireConfigError, GrimoireMemoryError
from grimoire.memory.manager import MemoryManager

memory_app = typer.Typer(help="Inspect and manage the memory subsystem.")

console = Console(stderr=True)


def _get_fmt(ctx: typer.Context) -> str:
    return (ctx.obj or {}).get("output", "text")


def _load_manager(path: Path = Path()) -> MemoryManager:
    """Resolve project config and return a MemoryManager."""
    from grimoire.tools._common import find_project_root

    target = path.resolve()
    config_path = target / "project-context.yaml"
    if not config_path.is_file():
        try:
            root = find_project_root(target)
            config_path = root / "project-context.yaml"
        except (FileNotFoundError, PermissionError, OSError):
            console.print("[red]Not a Grimoire project[/red] — run [bold]grimoire init[/bold] first.")
            raise typer.Exit(1) from None
    else:
        root = target

    try:
        cfg = GrimoireConfig.from_yaml(config_path)
    except GrimoireConfigError as exc:
        console.print(f"[red]Config error:[/red] {exc}")
        raise typer.Exit(2) from None

    try:
        return MemoryManager.from_config(cfg, project_root=root)
    except GrimoireMemoryError as exc:
        console.print(f"[red]Memory backend error:[/red] {exc}")
        raise typer.Exit(1) from None


# ── grimoire memory status ────────────────────────────────────────────────────


@memory_app.command("status")
def memory_status(ctx: typer.Context) -> None:
    """Show memory backend health, entry count, and configuration."""
    mgr = _load_manager()
    health = mgr.health_check()
    total = mgr.count()
    fmt = _get_fmt(ctx)

    if fmt == "json":
        typer.echo(json.dumps({
            "backend": health.backend,
            "healthy": health.healthy,
            "entries": total,
            "detail": health.detail,
        }, indent=2, default=str))
        return

    status_icon = "[green]✓[/green]" if health.healthy else "[red]✗[/red]"
    console.print(f"{status_icon} Backend: [bold]{health.backend}[/bold]")
    console.print(f"  Entries : {total}")
    if health.detail:
        for k, v in health.detail.items():
            console.print(f"  {k}: {v}")


# ── grimoire memory search ────────────────────────────────────────────────────


@memory_app.command("search")
def memory_search(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Search query (keyword or semantic)."),
    limit: int = typer.Option(10, "--limit", "-n", help="Max results to return."),
    user_id: str = typer.Option("", "--user", "-u", help="Filter by user ID."),
) -> None:
    """Search memories by keyword or semantic similarity."""
    mgr = _load_manager()
    results = mgr.search(query, user_id=user_id, limit=limit)
    fmt = _get_fmt(ctx)

    if fmt == "json":
        typer.echo(json.dumps([e.to_dict() for e in results], indent=2, default=str))
        return

    if not results:
        console.print(f"[yellow]No memories matching '{query}'.[/yellow]")
        return

    tbl = Table(title=f"Search: {query}")
    tbl.add_column("ID", style="dim", max_width=12)
    tbl.add_column("Text", max_width=60)
    tbl.add_column("Score", justify="right")
    tbl.add_column("Tags")

    for entry in results:
        score = f"{entry.score:.3f}" if entry.score else "—"
        tags = ", ".join(entry.tags) if entry.tags else "—"
        text = entry.text[:57] + "…" if len(entry.text) > 60 else entry.text
        tbl.add_row(entry.id[:12], text, score, tags)

    console.print(tbl)


# ── grimoire memory list ──────────────────────────────────────────────────────


@memory_app.command("list")
def memory_list(
    ctx: typer.Context,
    offset: int = typer.Option(0, "--offset", help="Skip first N entries."),
    limit: int = typer.Option(50, "--limit", "-n", help="Max entries to return."),
    user_id: str = typer.Option("", "--user", "-u", help="Filter by user ID."),
) -> None:
    """List stored memories with pagination."""
    mgr = _load_manager()
    entries = mgr.get_all(user_id=user_id, offset=offset, limit=limit)
    total = mgr.count()
    fmt = _get_fmt(ctx)

    if fmt == "json":
        typer.echo(json.dumps({
            "total": total,
            "offset": offset,
            "limit": limit,
            "entries": [e.to_dict() for e in entries],
        }, indent=2, default=str))
        return

    if not entries:
        console.print("[yellow]No memories stored.[/yellow]")
        return

    tbl = Table(title=f"Memories ({offset+1}–{offset+len(entries)} of {total})")
    tbl.add_column("ID", style="dim", max_width=12)
    tbl.add_column("Text", max_width=50)
    tbl.add_column("Tags")
    tbl.add_column("Created", style="dim")

    for entry in entries:
        tags = ", ".join(entry.tags) if entry.tags else "—"
        text = entry.text[:47] + "…" if len(entry.text) > 50 else entry.text
        created = entry.created_at[:10] if entry.created_at else "—"
        tbl.add_row(entry.id[:12], text, tags, created)

    console.print(tbl)


# ── grimoire memory export ────────────────────────────────────────────────────

_export_file_opt = typer.Option(None, "--file", "-f", help="Output file (default: stdout).")


@memory_app.command("export")
def memory_export(
    ctx: typer.Context,
    file: Path = _export_file_opt,
    user_id: str = typer.Option("", "--user", "-u", help="Filter by user ID."),
) -> None:
    """Export all memories to JSON."""
    mgr = _load_manager()
    entries = mgr.get_all(user_id=user_id)
    data = {
        "version": 1,
        "count": len(entries),
        "entries": [e.to_dict() for e in entries],
    }
    payload = json.dumps(data, indent=2, default=str, ensure_ascii=False)

    if file:
        file.write_text(payload, encoding="utf-8")
        console.print(f"[green]Exported {len(entries)} entries →[/green] {file}")
    else:
        typer.echo(payload)


# ── grimoire memory import ────────────────────────────────────────────────────


def _validate_import_data(data: Any) -> list[dict[str, Any]]:
    """Validate imported JSON structure. Returns list of entry dicts."""
    if not isinstance(data, dict):
        console.print("[red]Invalid format:[/red] expected JSON object with 'entries' key.")
        raise typer.Exit(1)
    entries = data.get("entries")
    if not isinstance(entries, list):
        console.print("[red]Invalid format:[/red] 'entries' must be a list.")
        raise typer.Exit(1)
    for i, e in enumerate(entries):
        if not isinstance(e, dict) or "text" not in e:
            console.print(f"[red]Invalid entry at index {i}:[/red] must have 'text' field.")
            raise typer.Exit(1)
    return entries


_import_file_arg = typer.Argument(..., help="JSON file to import.", exists=True, readable=True)


@memory_app.command("import")
def memory_import(
    ctx: typer.Context,
    file: Path = _import_file_arg,
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show plan without importing."),
) -> None:
    """Import memories from a JSON export file."""
    raw = file.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        console.print(f"[red]Invalid JSON:[/red] {exc}")
        raise typer.Exit(1) from None

    entries = _validate_import_data(data)
    fmt = _get_fmt(ctx)

    if dry_run:
        if fmt == "json":
            typer.echo(json.dumps({"dry_run": True, "count": len(entries)}, indent=2))
        else:
            console.print(f"[bold]import --dry-run:[/bold] would import {len(entries)} entries.")
        return

    mgr = _load_manager()
    result = mgr.store_many(entries)

    if fmt == "json":
        typer.echo(json.dumps({"imported": len(result)}, indent=2))
    else:
        console.print(f"[green]Imported {len(result)} entries.[/green]")


# ── grimoire memory gc ────────────────────────────────────────────────────────


@memory_app.command("gc")
def memory_gc(ctx: typer.Context) -> None:
    """Consolidate and compact stored memories."""
    mgr = _load_manager()
    fmt = _get_fmt(ctx)
    affected = mgr.consolidate()

    if fmt == "json":
        typer.echo(json.dumps({"consolidated": affected}, indent=2))
    else:
        if affected:
            console.print(f"[green]Consolidated {affected} entries.[/green]")
        else:
            console.print("[dim]Nothing to consolidate.[/dim]")


# ── grimoire memory delete ────────────────────────────────────────────────────


@memory_app.command("delete")
def memory_delete(
    ctx: typer.Context,
    entry_id: str = typer.Argument(..., help="Entry ID to delete."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Delete a specific memory entry by ID."""
    mgr = _load_manager()
    fmt = _get_fmt(ctx)
    confirm = yes or (ctx.obj or {}).get("yes", False)

    # Verify it exists first
    entry = mgr.recall(entry_id)
    if entry is None:
        if fmt == "json":
            typer.echo(json.dumps({"deleted": False, "reason": "not found"}, indent=2))
        else:
            console.print(f"[yellow]Entry not found:[/yellow] {entry_id}")
        raise typer.Exit(1)

    if not confirm:
        text_preview = entry.text[:60] + "…" if len(entry.text) > 60 else entry.text
        console.print(f"[bold]Delete:[/bold] {entry_id}")
        console.print(f"  Text: {text_preview}")
        if not typer.confirm("Confirm deletion?"):
            console.print("[dim]Aborted.[/dim]")
            raise typer.Exit(0)

    deleted = mgr.delete(entry_id)

    if fmt == "json":
        typer.echo(json.dumps({"deleted": deleted, "entry_id": entry_id}, indent=2))
    else:
        if deleted:
            console.print(f"[green]Deleted:[/green] {entry_id}")
        else:
            console.print(f"[red]Failed to delete:[/red] {entry_id}")
