"""Grimoire CLI — ``grimoire history`` and ``grimoire repair`` commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from grimoire.cli._cli_helpers import (
    _AUDIT_FILENAME,
    _get_fmt,
    _log_operation,
)
from grimoire.core.project_layout import detect_project_layout

console = Console(stderr=True)


def history_cmd(
    ctx: typer.Context,
    limit: int = typer.Option(20, "--limit", "-n", help="Number of entries to show."),
    filter_cmd: str = typer.Option(None, "--filter", "-f", help="Filter by command name."),
    clear: bool = typer.Option(False, "--clear", help="Clear the audit log (requires --yes or confirmation)."),
) -> None:
    """Show audit trail of recent CLI operations.

    [dim]Examples:[/dim]
      [cyan]grimoire history[/cyan]                 Last 20 operations
      [cyan]grimoire history -n 50 -f init[/cyan]   Last 50 'init' operations
      [cyan]grimoire history -o json[/cyan]         Machine-readable audit log
      [cyan]grimoire history --clear[/cyan]         Clear audit log
    """
    from grimoire.tools._common import find_project_root

    fmt = _get_fmt(ctx)

    try:
        root = find_project_root()
    except FileNotFoundError:
        console.print("[red]Not a Grimoire project.[/red]")
        raise typer.Exit(1) from None

    log_file = root / "_grimoire" / "_memory" / _AUDIT_FILENAME

    if clear:
        if not log_file.is_file():
            console.print("[dim]No audit history to clear.[/dim]")
            return
        yes = (ctx.obj or {}).get("yes", False)
        if not yes:
            confirm = typer.confirm("Clear the entire audit log?")
            if not confirm:
                raise typer.Abort
        log_file.write_text("", encoding="utf-8")
        _log_operation("history_clear", {})
        if fmt == "json":
            typer.echo(json.dumps({"cleared": True}))
        else:
            console.print("[green]✓[/green] Audit log cleared.")
        return

    if not log_file.is_file():
        if fmt == "json":
            typer.echo(json.dumps({"entries": [], "total": 0}))
        else:
            console.print("[dim]No audit history yet.[/dim]")
        return

    all_entries: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    skipped = 0
    with log_file.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                all_entries.append(entry)
                if filter_cmd and entry.get("cmd") != filter_cmd:
                    continue
                entries.append(entry)
            except json.JSONDecodeError:
                skipped += 1

    total_entries = len(all_entries)
    entries = entries[-limit:][::-1]

    if fmt == "json":
        typer.echo(json.dumps({
            "entries": entries,
            "total": len(entries),
            "total_entries": total_entries,
            "skipped": skipped,
        }, indent=2))
        return

    if not entries:
        console.print("[dim]No matching entries.[/dim]")
        return

    if skipped > 0:
        console.print(f"[yellow]⚠ {skipped} corrupted entries skipped[/yellow]")

    tbl = Table(title=f"Audit History (last {limit})")
    tbl.add_column("Timestamp", style="dim")
    tbl.add_column("Version", style="dim")
    tbl.add_column("Command", style="bold")
    tbl.add_column("Status")
    tbl.add_column("Args", style="dim")
    for e in entries:
        ts = e.get("ts", "?")[:19].replace("T", " ")
        ver = e.get("v", "—")
        cmd = e.get("cmd", "?")
        status_icon = "[green]✓[/green]" if e.get("ok") else "[red]✗[/red]"
        args_str = ", ".join(f"{k}={v}" for k, v in (e.get("args") or {}).items())
        tbl.add_row(ts, ver, cmd, status_icon, args_str)
    console.print(tbl)


def repair(
    ctx: typer.Context,
    path: Path = typer.Argument(Path(), help="Project root to repair."),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show what would be repaired without making changes."),
) -> None:
    """Auto-repair common project issues detected by ``grimoire doctor``.

    [dim]Examples:[/dim]
      [cyan]grimoire repair .[/cyan]            Repair current project
      [cyan]grimoire repair . --dry-run[/cyan]  Preview repairs
      [cyan]grimoire repair . -o json[/cyan]    Machine-readable output
    """
    import datetime as _dt

    target = path.resolve()
    fmt = _get_fmt(ctx)
    actions: list[dict[str, str]] = []

    config_path = target / "project-context.yaml"
    if not config_path.is_file():
        if fmt == "json":
            typer.echo(json.dumps({"repaired": False, "error": "No project-context.yaml found"}, indent=2))
        else:
            console.print("[red]No project-context.yaml found — cannot repair.[/red]")
        raise typer.Exit(1)

    if fmt != "json":
        mode_label = "[yellow]DRY RUN[/yellow] — " if dry_run else ""
        console.print(f"{mode_label}[bold]Grimoire Repair[/bold] — {target}\n")

    layout = detect_project_layout(target)

    # 1. Create missing directories
    for d in layout.required_dirs:
        dp = target / d
        if not dp.is_dir():
            actions.append({"action": "create_dir", "path": d})
            if not dry_run:
                dp.mkdir(parents=True, exist_ok=True)
            if fmt != "json":
                tag = "[dim]would create[/dim]" if dry_run else "[green]created[/green]"
                console.print(f"  {tag}  {d}/")

    # 2. Remove stale audit log entries (>90 days)
    audit_log = layout.memory_path(target) / _AUDIT_FILENAME
    if audit_log.is_file():
        cutoff = _dt.datetime.now(_dt.UTC) - _dt.timedelta(days=90)
        trimmed = 0
        kept: list[str] = []
        with open(audit_log, encoding="utf-8") as fh:
            for line in fh:
                line = line.rstrip("\n")
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    ts = _dt.datetime.fromisoformat(entry.get("ts", ""))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=_dt.UTC)
                    if ts >= cutoff:
                        kept.append(line + "\n")
                    else:
                        trimmed += 1
                except (json.JSONDecodeError, ValueError):
                    kept.append(line + "\n")
        if trimmed > 0:
            actions.append({"action": "trim_audit_log", "removed": str(trimmed)})
            if not dry_run:
                with open(audit_log, "w", encoding="utf-8") as fh:
                    fh.writelines(kept)
            if fmt != "json":
                tag = "[dim]would trim[/dim]" if dry_run else "[green]trimmed[/green]"
                console.print(f"  {tag}  audit.jsonl — {trimmed} old entries")

    # 3. Log + Summary
    if not dry_run and actions:
        _log_operation("repair", {"count": str(len(actions))})

    if fmt == "json":
        typer.echo(json.dumps({
            "ok": True,
            "project": str(target),
            "dry_run": dry_run,
            "actions": actions,
            "count": len(actions),
        }, indent=2))
    elif not actions:
        console.print("  [green]No issues found — project is healthy.[/green]")
    else:
        verb = "would be applied" if dry_run else "applied"
        console.print(f"\n[bold]{len(actions)} repair(s) {verb}.[/bold]")
