"""BMAD CLI entry point — ``bmad [command]``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

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
  backend: "{backend}"

agents:
  archetype: "{archetype}"
  custom_agents: []

installed_archetypes: []
"""

_KNOWN_ARCHETYPES = frozenset({
    "minimal", "web-app", "creative-studio", "fix-loop",
    "infra-ops", "meta", "stack", "features", "platform-engineering",
})

_KNOWN_BACKENDS = frozenset({"auto", "local", "qdrant-local", "qdrant-server", "ollama"})

_init_path_arg = typer.Argument(Path("."), help="Project directory to initialise.")
_init_name_opt = typer.Option("", help="Project name (default: directory name).")
_init_force_opt = typer.Option(False, "--force", "-f", help="Overwrite existing config.")
_init_archetype_opt = typer.Option("minimal", "--archetype", "-a", help="Agent archetype to use.")
_init_backend_opt = typer.Option("auto", "--backend", "-b", help="Memory backend (auto, local, qdrant-local, qdrant-server, ollama).")


@app.command()
def init(
    path: Path = _init_path_arg,
    name: str = _init_name_opt,
    force: bool = _init_force_opt,
    archetype: str = _init_archetype_opt,
    backend: str = _init_backend_opt,
) -> None:
    """Initialise a BMAD project (creates project-context.yaml)."""
    target = path.resolve()
    target.mkdir(parents=True, exist_ok=True)
    config_file = target / "project-context.yaml"

    if config_file.exists() and not force:
        console.print(f"[yellow]project-context.yaml already exists at {target}[/yellow]")
        console.print("Use --force to overwrite.")
        raise typer.Exit(1)

    # Validate archetype
    if archetype not in _KNOWN_ARCHETYPES:
        console.print(f"[red]Unknown archetype:[/red] {archetype}")
        console.print(f"Available: {', '.join(sorted(_KNOWN_ARCHETYPES))}")
        raise typer.Exit(1)

    # Validate backend
    if backend not in _KNOWN_BACKENDS:
        console.print(f"[red]Unknown backend:[/red] {backend}")
        console.print(f"Available: {', '.join(sorted(_KNOWN_BACKENDS))}")
        raise typer.Exit(1)

    project_name = name or target.name
    config_file.write_text(_TEMPLATE_YAML.format(name=project_name, archetype=archetype, backend=backend))

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
    console.print("\n[bold]Agents[/bold]")
    console.print(f"  Archetype: {cfg.agents.archetype}")
    if cfg.agents.custom_agents:
        console.print(f"  Custom: {', '.join(cfg.agents.custom_agents)}")

    # Memory
    console.print("\n[bold]Memory[/bold]")
    console.print(f"  Backend: {cfg.memory.backend}")

    # Structure health
    console.print("\n[bold]Structure[/bold]")
    dirs = ["_bmad", "_bmad-output", "_bmad/_memory"]
    for d in dirs:
        icon = "[green]✓[/green]" if (target / d).is_dir() else "[red]✗[/red]"
        console.print(f"  {icon} {d}/")

    console.print()


# ── bmad add / remove ─────────────────────────────────────────────────────────

def _load_yaml_rw(config_path: Path) -> tuple[Any, Any]:
    """Load YAML preserving formatting (for round-trip editing)."""
    from ruamel.yaml import YAML

    yaml = YAML()
    yaml.preserve_quotes = True  # type: ignore[assignment]
    with open(config_path, encoding="utf-8") as fh:
        data = yaml.load(fh)
    return yaml, data


def _save_yaml_rw(yaml: Any, data: Any, config_path: Path) -> None:
    """Write YAML back preserving formatting."""
    with open(config_path, "w", encoding="utf-8") as fh:
        yaml.dump(data, fh)


def _find_config(path: Path) -> Path:
    """Resolve and validate project-context.yaml."""
    target = path.resolve()
    config_path = target / "project-context.yaml"
    if not config_path.is_file():
        console.print("[red]Not a BMAD project[/red] — run [bold]bmad init[/bold] first.")
        raise typer.Exit(1)
    return config_path


_add_agent_id = typer.Argument(..., help="Agent identifier to add.")
_add_path_arg = typer.Argument(Path("."), help="Project root.")


@app.command("add")
def add_agent(
    agent_id: str = _add_agent_id,
    path: Path = _add_path_arg,
) -> None:
    """Add a custom agent to the project configuration."""
    config_path = _find_config(path)
    yaml, data = _load_yaml_rw(config_path)

    agents = data.get("agents") or {}
    custom: list[str] = agents.get("custom_agents") or []

    if agent_id in custom:
        console.print(f"[yellow]Agent '{agent_id}' already in project.[/yellow]")
        raise typer.Exit(0)

    custom.append(agent_id)
    agents["custom_agents"] = custom
    data["agents"] = agents
    _save_yaml_rw(yaml, data, config_path)

    console.print(f"[green]Added agent:[/green] {agent_id}")


_rm_agent_id = typer.Argument(..., help="Agent identifier to remove.")
_rm_path_arg = typer.Argument(Path("."), help="Project root.")


@app.command("remove")
def remove_agent(
    agent_id: str = _rm_agent_id,
    path: Path = _rm_path_arg,
) -> None:
    """Remove a custom agent from the project configuration."""
    config_path = _find_config(path)
    yaml, data = _load_yaml_rw(config_path)

    agents = data.get("agents") or {}
    custom: list[str] = agents.get("custom_agents") or []

    if agent_id not in custom:
        console.print(f"[yellow]Agent '{agent_id}' not in project.[/yellow]")
        raise typer.Exit(1)

    custom.remove(agent_id)
    agents["custom_agents"] = custom
    data["agents"] = agents
    _save_yaml_rw(yaml, data, config_path)

    console.print(f"[green]Removed agent:[/green] {agent_id}")
