"""Grimoire CLI — ``grimoire config`` sub-commands."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from grimoire.cli._cli_helpers import (
    _EXIT_CONFIG,
    _find_config,
    _get_fmt,
    _log_operation,
)
from grimoire.core.config import GrimoireConfig
from grimoire.core.exceptions import GrimoireConfigError

console = Console(stderr=True)

# ── grimoire config ───────────────────────────────────────────────────────────

config_app = typer.Typer(help="Manage project configuration.")


def _flatten(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Flatten nested dict to dot-notation keys."""
    out: dict[str, Any] = {}
    for k, v in data.items():
        full = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, full))
        elif isinstance(v, list) and v and isinstance(v[0], dict):
            for i, item in enumerate(v):
                if isinstance(item, dict):
                    out.update(_flatten(item, f"{full}.{i}"))
                else:
                    out[f"{full}.{i}"] = item
        else:
            out[full] = v
    return out


def _resolve_config_key(data: Any, key: str) -> Any:
    """Walk YAML data by dot-notation key — raise typer.Exit(_EXIT_CONFIG) if not found."""
    value = data
    for part in key.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            console.print(f"[red]Key not found:[/red] {key}")
            raise typer.Exit(_EXIT_CONFIG)
    return value


@config_app.command("show")
def config_show(
    ctx: typer.Context,
    key: str = typer.Argument("", help="Dot-notation key to extract (e.g. project.name). Omit for full config."),
) -> None:
    """Display current project configuration (read-only)."""
    from ruamel.yaml import YAML

    cfg_path = _find_config(Path())

    yaml = YAML()
    with cfg_path.open(encoding="utf-8") as fh:
        data = yaml.load(fh)

    fmt = _get_fmt(ctx)

    if key:
        value = _resolve_config_key(data, key)
        if fmt == "json":
            typer.echo(json.dumps({key: value}, indent=2, default=str))
        else:
            typer.echo(value)
        return

    if fmt == "json":
        typer.echo(json.dumps(dict(data), indent=2, default=str))
    else:
        from io import StringIO
        buf = StringIO()
        yaml.dump(data, buf)
        typer.echo(buf.getvalue())


@config_app.command("get")
def config_get(
    ctx: typer.Context,
    key: str = typer.Argument(..., help="Dot-notation key (e.g. project.name)."),
) -> None:
    """Get a single config value by dot-notation key."""
    from ruamel.yaml import YAML

    cfg_path = _find_config(Path())

    yaml = YAML()
    with cfg_path.open(encoding="utf-8") as fh:
        data = yaml.load(fh)

    value = _resolve_config_key(data, key)
    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps({key: value}, indent=2, default=str))
    else:
        typer.echo(value)


@config_app.command("path")
def config_path() -> None:
    """Show the resolved path to project-context.yaml."""
    cfg_path = _find_config(Path())
    typer.echo(str(cfg_path.resolve()))


@config_app.command("set")
def config_set(
    ctx: typer.Context,
    key: str = typer.Argument(..., help="Dot-notation key (e.g. project.name)."),
    value: str = typer.Argument(..., help="New value to set."),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show plan without modifying config."),
) -> None:
    """Set a single config value by dot-notation key."""
    from ruamel.yaml import YAML

    cfg_path = _find_config(Path())

    yaml = YAML()
    yaml.preserve_quotes = True
    with cfg_path.open(encoding="utf-8") as fh:
        data = yaml.load(fh)

    parts = key.split(".")
    target = data
    for part in parts[:-1]:
        if isinstance(target, dict) and part in target:
            target = target[part]
        else:
            console.print(f"[red]Key not found:[/red] {key}")
            raise typer.Exit(_EXIT_CONFIG)

    last = parts[-1]
    if not isinstance(target, dict) or last not in target:
        console.print(f"[red]Key not found:[/red] {key}")
        raise typer.Exit(_EXIT_CONFIG)

    old_value = target[last]

    # Coerce value to match existing type
    coerced: Any = value
    if isinstance(old_value, bool):
        coerced = value.lower() in ("true", "1", "yes")
    elif isinstance(old_value, int):
        try:
            coerced = int(value)
        except ValueError:
            console.print(f"[red]Expected integer for key '{key}', got:[/red] {value}")
            raise typer.Exit(1) from None
    elif isinstance(old_value, float):
        try:
            coerced = float(value)
        except ValueError:
            console.print(f"[red]Expected number for key '{key}', got:[/red] {value}")
            raise typer.Exit(1) from None
    elif isinstance(old_value, list):
        console.print("[red]Cannot set list values via CLI.[/red] Use [bold]grimoire config edit[/bold] instead.")
        raise typer.Exit(1)

    fmt = _get_fmt(ctx)

    if dry_run:
        if fmt == "json":
            typer.echo(json.dumps({"key": key, "old": old_value, "new": coerced, "dry_run": True}, indent=2, default=str))
        else:
            console.print("[bold]config set --dry-run[/bold]")
            console.print(f"  [cyan]{key}[/cyan]: {old_value!r} → {coerced!r}")
        return

    target[last] = coerced
    with cfg_path.open("w", encoding="utf-8") as fh:
        yaml.dump(data, fh)

    _log_operation("config_set", {"key": key, "old": str(old_value), "new": str(coerced)})

    if fmt == "json":
        typer.echo(json.dumps({"key": key, "old": old_value, "new": coerced}, indent=2, default=str))
    else:
        console.print(f"[green]Updated:[/green] {key} = {coerced!r}")


@config_app.command("list")
def config_list(ctx: typer.Context) -> None:
    """List all config keys with their current values."""
    from ruamel.yaml import YAML

    cfg_path = _find_config(Path())

    yaml = YAML()
    with cfg_path.open(encoding="utf-8") as fh:
        data = yaml.load(fh)

    flat = list(_flatten(dict(data)).items())
    fmt = _get_fmt(ctx)

    if fmt == "json":
        typer.echo(json.dumps(dict(flat), indent=2, default=str))
        return

    table = Table(title="Configuration", show_lines=False)
    table.add_column("Key", style="cyan")
    table.add_column("Value")
    for k, v in flat:
        table.add_row(k, str(v))
    console.print(table)


@config_app.command("edit")
def config_edit() -> None:
    """Open project-context.yaml in $EDITOR."""
    import shutil

    cfg_path = _find_config(Path())
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    if not shutil.which(editor):
        console.print(f"[red]Editor not found:[/red] {editor}")
        console.print("[dim]Set $VISUAL or $EDITOR to a valid editor.[/dim]")
        raise typer.Exit(2)
    console.print(f"[dim]Opening {cfg_path} in {editor}…[/dim]")
    os.execvp(editor, [editor, str(cfg_path)])  # noqa: S606


@config_app.command("validate")
def config_validate(ctx: typer.Context) -> None:
    """Validate project-context.yaml against the Grimoire schema."""
    cfg_path = _find_config(Path())
    fmt = _get_fmt(ctx)

    try:
        cfg = GrimoireConfig.from_yaml(cfg_path)
    except GrimoireConfigError as exc:
        if fmt == "json":
            typer.echo(json.dumps({"valid": False, "error": str(exc)}, indent=2))
        else:
            console.print(f"[red]Invalid config:[/red] {exc}")
        raise typer.Exit(1) from None

    warnings = cfg.validate()

    if fmt == "json":
        typer.echo(json.dumps({"valid": True, "warnings": warnings}, indent=2))
        return

    if warnings:
        console.print("[yellow]Config valid with warnings:[/yellow]")
        for w in warnings:
            console.print(f"  [yellow]⚠[/yellow] {w}")
    else:
        console.print("[green]✓[/green] Config valid — no issues found.")
