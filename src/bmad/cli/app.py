"""BMAD CLI entry point — ``bmad [command]``."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from bmad.__version__ import __version__
from bmad.core.config import BmadConfig
from bmad.core.exceptions import BmadConfigError

app = typer.Typer(
    name="bmad",
    help="BMAD Kit — Composable AI agent platform.",
    no_args_is_help=True,
)

console = Console(stderr=True)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"bmad-kit {__version__}")
        raise typer.Exit


@app.callback()
def main(
    version: bool = typer.Option(False, "--version", "-V", callback=_version_callback, is_eager=True,
                                 help="Show version and exit."),
) -> None:
    """BMAD Kit — Composable AI agent platform."""


# ── bmad init ─────────────────────────────────────────────────────────────────

_TEMPLATE_YAML = """\
# BMAD Kit — Project Context
# Run: bmad doctor  to validate this file.

project:
  name: "{name}"
  description: ""
  type: "webapp"
  stack: []
  repos:
    - name: "{name}"
      path: "."
      default_branch: "main"

user:
  name: ""
  language: "Français"
  skill_level: "intermediate"

memory:
  backend: "auto"

agents:
  archetype: "minimal"
  custom_agents: []

installed_archetypes: []
"""


_init_path_arg = typer.Argument(Path("."), help="Project directory to initialise.")
_init_name_opt = typer.Option("", help="Project name (default: directory name).")
_init_force_opt = typer.Option(False, "--force", "-f", help="Overwrite existing config.")


@app.command()
def init(
    path: Path = _init_path_arg,
    name: str = _init_name_opt,
    force: bool = _init_force_opt,
) -> None:
    """Initialise a BMAD project (creates project-context.yaml)."""
    target = path.resolve()
    target.mkdir(parents=True, exist_ok=True)
    config_file = target / "project-context.yaml"

    if config_file.exists() and not force:
        console.print(f"[yellow]project-context.yaml already exists at {target}[/yellow]")
        console.print("Use --force to overwrite.")
        raise typer.Exit(1)

    project_name = name or target.name
    config_file.write_text(_TEMPLATE_YAML.format(name=project_name))

    # Create standard directories
    for d in ("_bmad/_memory", "_bmad-output"):
        (target / d).mkdir(parents=True, exist_ok=True)

    console.print(f"[green]Initialised BMAD project:[/green] {project_name}")
    console.print(f"  Config: {config_file}")


# ── bmad doctor ───────────────────────────────────────────────────────────────

_doctor_path_arg = typer.Argument(Path("."), help="Project root to diagnose.")


@app.command()
def doctor(
    path: Path = _doctor_path_arg,
) -> None:
    """Diagnose a BMAD project — check config, structure, health."""
    target = path.resolve()
    checks_ok = 0
    checks_fail = 0

    def ok(msg: str) -> None:
        nonlocal checks_ok
        checks_ok += 1
        console.print(f"  [green]OK[/green]  {msg}")

    def fail(msg: str) -> None:
        nonlocal checks_fail
        checks_fail += 1
        console.print(f"  [red]FAIL[/red]  {msg}")

    console.print(f"[bold]BMAD Doctor[/bold] — bmad-kit {__version__}")
    console.print(f"Project: {target}\n")

    # 1. Config file
    config_path = target / "project-context.yaml"
    if config_path.is_file():
        ok("project-context.yaml found")
    else:
        fail("project-context.yaml not found — run [bold]bmad init[/bold]")
        console.print(f"\n[bold]{checks_ok} OK, {checks_fail} FAIL[/bold]")
        raise typer.Exit(1)

    # 2. Parse config
    try:
        cfg = BmadConfig.from_yaml(config_path)
        ok(f"Config valid — project: {cfg.project.name}")
    except BmadConfigError as exc:
        fail(f"Config parse error: {exc}")
        console.print(f"\n[bold]{checks_ok} OK, {checks_fail} FAIL[/bold]")
        raise typer.Exit(1) from None

    # 3. Structure checks
    for d in ("_bmad", "_bmad-output"):
        if (target / d).is_dir():
            ok(f"{d}/ directory present")
        else:
            fail(f"{d}/ directory missing")

    # 4. Memory directory
    mem_dir = target / "_bmad" / "_memory"
    if mem_dir.is_dir():
        ok("_bmad/_memory/ exists")
    else:
        fail("_bmad/_memory/ missing")

    # 5. Archetype check
    if cfg.agents.archetype:
        ok(f"Archetype configured: {cfg.agents.archetype}")

    # 6. Summary
    total = checks_ok + checks_fail
    console.print(f"\n[bold]{checks_ok}/{total} checks passed[/bold]")
    if checks_fail > 0:
        raise typer.Exit(1)


# ── bmad status ───────────────────────────────────────────────────────────────

_status_path_arg = typer.Argument(Path("."), help="Project root.")


@app.command()
def status(
    path: Path = _status_path_arg,
) -> None:
    """Show project dashboard — config, agents, memory, health."""
    target = path.resolve()
    config_path = target / "project-context.yaml"

    if not config_path.is_file():
        console.print("[red]Not a BMAD project[/red] — run [bold]bmad init[/bold] first.")
        raise typer.Exit(1)

    try:
        cfg = BmadConfig.from_yaml(config_path)
    except BmadConfigError as exc:
        console.print(f"[red]Config error:[/red] {exc}")
        raise typer.Exit(1) from None

    # Header
    console.print(f"\n[bold]BMAD Project:[/bold] {cfg.project.name}")
    console.print(f"bmad-kit {__version__}\n")

    # Project table
    tbl = Table(title="Project", show_header=False, padding=(0, 2))
    tbl.add_column("Key", style="bold")
    tbl.add_column("Value")
    tbl.add_row("Name", cfg.project.name)
    tbl.add_row("Type", cfg.project.type)
    if cfg.project.stack:
        tbl.add_row("Stack", ", ".join(cfg.project.stack))
    if cfg.project.repos:
        tbl.add_row("Repos", ", ".join(r.name for r in cfg.project.repos))
    tbl.add_row("User", cfg.user.name or "(not set)")
    tbl.add_row("Language", cfg.user.language)
    tbl.add_row("Skill level", cfg.user.skill_level)
    console.print(tbl)

    # Agents
    console.print(f"\n[bold]Agents[/bold]")
    console.print(f"  Archetype: {cfg.agents.archetype}")
    if cfg.agents.custom_agents:
        console.print(f"  Custom: {', '.join(cfg.agents.custom_agents)}")

    # Memory
    console.print(f"\n[bold]Memory[/bold]")
    console.print(f"  Backend: {cfg.memory.backend}")

    # Structure health
    console.print(f"\n[bold]Structure[/bold]")
    dirs = ["_bmad", "_bmad-output", "_bmad/_memory"]
    for d in dirs:
        icon = "[green]✓[/green]" if (target / d).is_dir() else "[red]✗[/red]"
        console.print(f"  {icon} {d}/")

    console.print()
