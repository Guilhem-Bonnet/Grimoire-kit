"""``grimoire debugger`` — wrapper ergonomique autour de l'agent debugger."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import typer
from rich.console import Console

debugger_app = typer.Typer(help="Inspect and visualize agent reality/debug state.")
console = Console(stderr=True)
OUTPUT_OPTION = typer.Option(None, "--output", "-o", help="Output HTML path.")
SERVE_OUTPUT_OPTION = typer.Option(None, "--output", help="Output HTML path.")
PORT_OPTION = typer.Option(8765, "--port", help="Port to serve the dashboard on.")
OPEN_BROWSER_OPTION = typer.Option(False, "--open-browser", help="Open browser automatically.")


def _get_fmt(ctx: typer.Context) -> str:
    return (ctx.obj or {}).get("output", "text")


def _project_root(path: Path = Path()) -> Path:
    from grimoire.tools._common import find_project_root

    target = path.resolve()
    if (target / "project-context.yaml").is_file():
        return target
    try:
        return find_project_root(target)
    except (FileNotFoundError, PermissionError, OSError):
        console.print("[red]Not a Grimoire project[/red] — run [bold]grimoire init[/bold] first.")
        raise typer.Exit(1) from None


def _load_debugger_module(project_root: Path):
    tool = project_root / "framework" / "tools" / "agent-debugger.py"
    if not tool.exists():
        console.print(f"[red]Debugger tool not found:[/red] {tool}")
        raise typer.Exit(1)
    spec = importlib.util.spec_from_file_location("grimoire_agent_debugger", tool)
    if not spec or not spec.loader:
        console.print(f"[red]Unable to load debugger:[/red] {tool}")
        raise typer.Exit(1)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@debugger_app.command("status")
def debugger_status(ctx: typer.Context) -> None:
    root = _project_root()
    mod = _load_debugger_module(root)
    snapshot = mod.build_snapshot(root)
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps(snapshot.to_dict(), indent=2, ensure_ascii=False))
        return
    mod.print_status(snapshot)


@debugger_app.command("claims")
def debugger_claims(ctx: typer.Context) -> None:
    root = _project_root()
    mod = _load_debugger_module(root)
    snapshot = mod.build_snapshot(root)
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps([claim.__dict__ for claim in snapshot.claims], indent=2, ensure_ascii=False))
        return
    mod.print_claims(snapshot)


@debugger_app.command("plan")
def debugger_plan(ctx: typer.Context) -> None:
    root = _project_root()
    mod = _load_debugger_module(root)
    snapshot = mod.build_snapshot(root)
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps(snapshot.plan.__dict__ if snapshot.plan else {}, indent=2, ensure_ascii=False, default=str))
        return
    mod.print_plan(snapshot)


@debugger_app.command("generate")
def debugger_generate(output: Path | None = OUTPUT_OPTION) -> None:
    root = _project_root()
    mod = _load_debugger_module(root)
    target = mod.write_dashboard(root, output)
    typer.echo(str(target))


@debugger_app.command("serve")
def debugger_serve(
    port: int = PORT_OPTION,
    output: Path | None = SERVE_OUTPUT_OPTION,
    open_browser: bool = OPEN_BROWSER_OPTION,
) -> None:
    root = _project_root()
    mod = _load_debugger_module(root)
    raise typer.Exit(mod.serve_dashboard(root, output, port, open_browser))