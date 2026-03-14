"""Grimoire CLI entry point — ``grimoire [command]``."""

from __future__ import annotations

import difflib
import json
import os
import platform
import signal
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from grimoire.__version__ import __version__
from grimoire.core.config import GrimoireConfig
from grimoire.core.exceptions import GrimoireConfigError, GrimoireError, GrimoireProjectError
from grimoire.core.log import configure_logging

# ── Command Aliases ───────────────────────────────────────────────────────────
# Short aliases for frequently used commands (à la git/gh/kubectl).

_ALIASES: dict[str, str] = {
    "i": "init",
    "d": "doctor",
    "s": "status",
    "v": "validate",
    "l": "lint",
    "u": "up",
    "c": "config",
    "r": "registry",
    "st": "status",
    "ck": "check",
}


def _expand_aliases() -> None:
    """Expand short command aliases in sys.argv before Typer parsing."""
    if len(sys.argv) > 1 and sys.argv[1] in _ALIASES:
        sys.argv[1] = _ALIASES[sys.argv[1]]


# Known top-level commands — auto-populated once at import time (after all
# @app.command decorators have run).  Populated lazily by _suggest_command().
_KNOWN_COMMANDS: set[str] = set()


def _suggest_command() -> None:
    """If the first positional arg is not a known command, suggest fuzzy matches."""
    if len(sys.argv) < 2:
        return
    arg = sys.argv[1]
    if arg.startswith("-"):
        return
    # Lazy-populate the command set on first call
    if not _KNOWN_COMMANDS:
        for cmd_info in app.registered_commands:
            name = cmd_info.name or (cmd_info.callback.__name__ if cmd_info.callback else None)
            if name:
                _KNOWN_COMMANDS.add(name)
        for group_info in app.registered_groups:
            if group_info.name:
                _KNOWN_COMMANDS.add(group_info.name)
        _KNOWN_COMMANDS.update(_ALIASES)
    if arg in _KNOWN_COMMANDS:
        return
    matches = difflib.get_close_matches(arg, sorted(_KNOWN_COMMANDS), n=3, cutoff=0.5)
    if matches:
        suggestions = ", ".join(f"[cyan]{m}[/cyan]" for m in matches)
        console.print(f"[red]Unknown command:[/red] {arg}")
        console.print(f"Did you mean: {suggestions}?")
        console.print("[dim]Run 'grimoire --help' for all commands.[/dim]")
        raise SystemExit(2)

app = typer.Typer(
    name="grimoire",
    help="Grimoire Kit — Composable AI agent platform.",
    no_args_is_help=True,
    rich_markup_mode="rich",
    epilog=(
        "[dim]Examples:[/dim]\n"
        "  [cyan]grimoire init myproject[/cyan]    Initialize a new project\n"
        "  [cyan]grimoire doctor .[/cyan]          Run health checks\n"
        "  [cyan]grimoire config show[/cyan]       Display current config\n"
        "  [cyan]grimoire version[/cyan]           Show extended version info\n"
        "\n"
        "[dim]Aliases:[/dim]  "
        "[cyan]i[/cyan]=init  [cyan]d[/cyan]=doctor  [cyan]s[/cyan]=status  "
        "[cyan]v[/cyan]=validate  [cyan]l[/cyan]=lint  [cyan]ck[/cyan]=check\n"
        "\n"
        "[dim]For more help:[/dim]\n"
        "  [bold]grimoire COMMAND --help[/bold]    Show command-specific help\n"
        "  [bold]grimoire env[/bold]               Show full environment info"
    ),
)

console = Console(stderr=True)


def _get_fmt(ctx: typer.Context) -> str:
    """Return the output format from context — 'text' or 'json'."""
    return (ctx.obj or {}).get("output", "text")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"grimoire-kit {__version__}")
        raise typer.Exit


@app.callback()
def main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-V", callback=_version_callback, is_eager=True,
                                 help="Show version and exit."),
    verbose: int = typer.Option(0, "--verbose", "-v", count=True,
                               help="Increase verbosity (-v info, -vv debug)."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress non-error output."),
    no_color: bool = typer.Option(False, "--no-color", help="Disable coloured output."),
    log_format: str = typer.Option("text", "--log-format", help="Log format: text or json."),
    output: str = typer.Option("text", "--output", "-o", help="Output format: text or json."),
    show_time: bool = typer.Option(False, "--time", help="Show elapsed time after command execution."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompts."),
    profile: bool = typer.Option(False, "--profile", help="Show per-phase timing breakdown."),
) -> None:
    """Grimoire Kit — Composable AI agent platform."""
    ctx.ensure_object(dict)

    # Env var overrides (priority: CLI flag > env var > default)
    # GRIMOIRE_OUTPUT: default output format
    if output == "text" and os.environ.get("GRIMOIRE_OUTPUT", "").lower() == "json":
        output = "json"
    # GRIMOIRE_QUIET: suppress non-error output
    if not quiet and os.environ.get("GRIMOIRE_QUIET", "").lower() in ("1", "true"):
        quiet = True
    # NO_COLOR: standard (https://no-color.org/)
    if not no_color and os.environ.get("NO_COLOR", ""):
        no_color = True

    ctx.obj["output"] = output
    ctx.obj["quiet"] = quiet
    ctx.obj["show_time"] = show_time
    ctx.obj["profile"] = profile
    ctx.obj["yes"] = yes or output == "json"
    ctx.obj["_timings"] = []
    if show_time or profile:
        ctx.obj["_start_time"] = time.perf_counter()
    if no_color:
        console.no_color = True
        console._force_terminal = False
    if verbose:
        level = ("INFO" if verbose == 1 else "DEBUG")
        configure_logging(level, fmt=log_format)


# ── grimoire version ──────────────────────────────────────────────────────────────


@app.command("version", rich_help_panel="Info")
def version_cmd(ctx: typer.Context) -> None:
    """Show extended version info (grimoire, Python, platform, project)."""
    fmt = _get_fmt(ctx)

    project_name: str | None = None
    try:
        ctx_file = _find_config(Path())
        cfg = GrimoireConfig.from_yaml(ctx_file)
        project_name = cfg.project.name
    except (typer.Exit, GrimoireError):
        pass

    if fmt == "json":
        data: dict[str, Any] = {
            "grimoire_version": __version__,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "install_path": str(Path(__file__).resolve().parent.parent),
        }
        if project_name:
            data["project"] = project_name
        typer.echo(json.dumps(data, indent=2))
        return

    console.print(f"[bold]grimoire-kit[/bold]  {__version__}")
    console.print(f"  Python     {sys.version.split()[0]}")
    console.print(f"  Platform   {platform.platform()}")
    console.print(f"  Install    {Path(__file__).resolve().parent.parent}")
    if project_name:
        console.print(f"  Project    {project_name}")


# ── grimoire init ─────────────────────────────────────────────────────────────────

_TEMPLATE_YAML = """\
# Grimoire Kit — Project Context
# Run: grimoire doctor  to validate this file.

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

_REQUIRED_DIRS = ("_grimoire", "_grimoire-output")
_MEMORY_DIR = "_grimoire/_memory"

_init_path_arg = typer.Argument(Path(), help="Project directory to initialise.")
_init_name_opt = typer.Option("", help="Project name (default: directory name).")
_init_force_opt = typer.Option(False, "--force", "-f", help="Overwrite existing config.")
_init_archetype_opt = typer.Option("minimal", "--archetype", "-a", help="Agent archetype to use.")
_init_backend_opt = typer.Option("auto", "--backend", "-b", help="Memory backend (auto, local, qdrant-local, qdrant-server, ollama).")


@app.command(rich_help_panel="Project")
def init(
    ctx: typer.Context,
    path: Path = _init_path_arg,
    name: str = _init_name_opt,
    force: bool = _init_force_opt,
    archetype: str = _init_archetype_opt,
    backend: str = _init_backend_opt,
    dry_run: bool = typer.Option(False, "--dry-run", help="Show plan without writing."),
) -> None:
    """Initialise a Grimoire project (creates project-context.yaml).

    [dim]Examples:[/dim]
      [cyan]grimoire init myproject[/cyan]
      [cyan]grimoire init . --name demo -a web-app -b qdrant-local[/cyan]
      [cyan]grimoire init --dry-run[/cyan]
    """
    target = path.resolve()

    # Validate archetype
    if archetype not in _KNOWN_ARCHETYPES:
        console.print(f"[red]Unknown archetype:[/red] {archetype}")
        matches = difflib.get_close_matches(archetype, sorted(_KNOWN_ARCHETYPES), n=2, cutoff=0.5)
        if matches:
            console.print(f"Did you mean: [cyan]{', '.join(matches)}[/cyan]?")
        else:
            console.print(f"Available: {', '.join(sorted(_KNOWN_ARCHETYPES))}")
        raise typer.Exit(1)

    # Validate backend
    if backend not in _KNOWN_BACKENDS:
        console.print(f"[red]Unknown backend:[/red] {backend}")
        matches = difflib.get_close_matches(backend, sorted(_KNOWN_BACKENDS), n=2, cutoff=0.5)
        if matches:
            console.print(f"Did you mean: [cyan]{', '.join(matches)}[/cyan]?")
        else:
            console.print(f"Available: {', '.join(sorted(_KNOWN_BACKENDS))}")
        raise typer.Exit(1)

    config_file = target / "project-context.yaml"
    project_name = name or target.name

    if dry_run:
        console.print("[bold]grimoire init --dry-run[/bold]\n")
        console.print(f"  [cyan]plan[/cyan]  Create {config_file}")
        for d in (*_REQUIRED_DIRS, _MEMORY_DIR):
            dp = target / d
            if not dp.is_dir():
                console.print(f"  [cyan]plan[/cyan]  Create directory: {d}/")
        console.print(f"\n  Project: {project_name}")
        console.print(f"  Archetype: {archetype}")
        console.print(f"  Backend: {backend}")
        return

    target.mkdir(parents=True, exist_ok=True)

    if config_file.exists() and not force:
        console.print(f"[yellow]project-context.yaml already exists at {target}[/yellow]")
        console.print("Use --force to overwrite.")
        raise typer.Exit(1)

    config_file.write_text(_TEMPLATE_YAML.format(name=project_name, archetype=archetype, backend=backend))

    # Create standard directories
    dirs_created = []
    for d in (*_REQUIRED_DIRS, _MEMORY_DIR):
        (target / d).mkdir(parents=True, exist_ok=True)
        dirs_created.append(d)

    _log_operation("init", {"project": project_name, "archetype": archetype, "backend": backend})

    fmt = _get_fmt(ctx)
    if fmt == "json":
        typer.echo(json.dumps({
            "ok": True, "project": project_name, "path": str(config_file),
            "archetype": archetype, "backend": backend, "directories": dirs_created,
        }, indent=2))
    else:
        console.print(f"[green]Initialised Grimoire project:[/green] {project_name}")
        console.print(f"  Config: {config_file}")


# ── grimoire doctor ───────────────────────────────────────────────────────────────

_doctor_path_arg = typer.Argument(Path(), help="Project root to diagnose.")
_doctor_fix_opt = typer.Option(False, "--fix", help="Auto-fix recoverable issues (missing directories).")


@app.command(rich_help_panel="Project")
def doctor(
    ctx: typer.Context,
    path: Path = _doctor_path_arg,
    fix: bool = _doctor_fix_opt,
) -> None:
    """Diagnose a Grimoire project — check config, structure, health.

    [dim]Examples:[/dim]
      [cyan]grimoire doctor[/cyan]             Check current directory
      [cyan]grimoire doctor /path --fix[/cyan]  Fix missing directories
      [cyan]grimoire doctor -o json .[/cyan]    Machine-readable output
    """
    target = path.resolve()
    results: list[dict[str, Any]] = []
    fmt = _get_fmt(ctx)

    def _record(name: str, *, passed: bool, detail: str = "") -> None:
        results.append({"name": name, "passed": passed, "detail": detail})
        if fmt != "json":
            tag = "[green]OK[/green]" if passed else "[red]FAIL[/red]"
            console.print(f"  {tag}  {detail or name}")

    if fmt != "json":
        console.print(f"[bold]Grimoire Doctor[/bold] — grimoire-kit {__version__}")
        console.print(f"Project: {target}\n")

    # 1. Config file
    config_path = target / "project-context.yaml"
    with _timed_phase("config_check"):
        if config_path.is_file():
            _record("config_exists", passed=True, detail="project-context.yaml found")
        else:
            _record("config_exists", passed=False, detail="project-context.yaml not found")
            if fmt == "json":
                typer.echo(json.dumps({"project": str(target), "checks": results, "passed": 0, "failed": 1}, indent=2))
            else:
                console.print("\n[bold]0 OK, 1 FAIL[/bold]")
            raise typer.Exit(1)

    # 2. Parse config
    cfg: GrimoireConfig | None = None
    with _timed_phase("config_parse"):
        try:
            cfg = GrimoireConfig.from_yaml(config_path)
            _record("config_valid", passed=True, detail=f"Config valid — project: {cfg.project.name}")
        except GrimoireConfigError as exc:
            _record("config_valid", passed=False, detail=f"Config parse error: {exc}")

    # 3. Structure checks
    fixed: list[str] = []
    with _timed_phase("structure_check"):
        for d in (*_REQUIRED_DIRS, _MEMORY_DIR):
            dp = target / d
            present = dp.is_dir()
            if not present and fix:
                dp.mkdir(parents=True, exist_ok=True)
                fixed.append(d)
                _record(f"dir_{d}", passed=True, detail=f"{d}/ created (--fix)")
            else:
                _record(f"dir_{d}", passed=present, detail=f"{d}/ {'present' if present else 'missing'}")

    # 4. Archetype check
    if cfg and cfg.agents.archetype:
        _record("archetype", passed=True, detail=f"Archetype configured: {cfg.agents.archetype}")

    # 5. Config semantic validation
    if cfg:
        warnings = cfg.validate()
        for w in warnings:
            _record("semantic", passed=False, detail=w)

    # 6. Optional dependencies
    with _timed_phase("dependency_scan"):
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as pkg_version

        for _use_case, pkg_name in (("qdrant", "qdrant-client"), ("ollama", "ollama"), ("mcp", "mcp")):
            try:
                ver = pkg_version(pkg_name)
                _record(f"optional_{pkg_name}", passed=True, detail=f"Optional: {pkg_name} {ver}")
            except PackageNotFoundError:
                results.append({"name": f"optional_{pkg_name}", "passed": True, "detail": f"Optional: {pkg_name} not installed", "optional": True})
                if fmt != "json":
                    console.print(f"  [dim]○[/dim]  Optional: {pkg_name} not installed")

    # 7. Python version
    _record("python", passed=True, detail=f"Python {sys.version.split()[0]}")

    # 8. Summary
    ok_count = sum(1 for r in results if r["passed"])
    fail_count = sum(1 for r in results if not r["passed"])

    if fmt == "json":
        data: dict[str, Any] = {"project": str(target), "checks": results, "passed": ok_count, "failed": fail_count}
        if fixed:
            data["fixed"] = fixed
        typer.echo(json.dumps(data, indent=2))
    else:
        if fixed:
            console.print(f"\n[green]Fixed {len(fixed)} issue(s):[/green] {', '.join(fixed)}")
        console.print(f"\n[bold]{ok_count}/{ok_count + fail_count} checks passed[/bold]")

    if fixed:
        _log_operation("doctor", {"fixed": fixed})

    if fail_count > 0:
        raise typer.Exit(1)


# ── grimoire status ───────────────────────────────────────────────────────────────

_status_path_arg = typer.Argument(Path(), help="Project root.")


@app.command(rich_help_panel="Project")
def status(
    ctx: typer.Context,
    path: Path = _status_path_arg,
) -> None:
    """Show project dashboard — config, agents, memory, health.

    [dim]Examples:[/dim]
      [cyan]grimoire status[/cyan]              Project in current directory
      [cyan]grimoire status -o json . | jq .agents[/cyan]
    """
    target = path.resolve()
    config_path = target / "project-context.yaml"

    if not config_path.is_file():
        console.print("[red]Not a Grimoire project[/red] — run [bold]grimoire init[/bold] first.")
        raise typer.Exit(1)

    try:
        cfg = GrimoireConfig.from_yaml(config_path)
    except GrimoireConfigError as exc:
        console.print(f"[red]Config error:[/red] {exc}")
        raise typer.Exit(1) from None

    dirs = [*_REQUIRED_DIRS, _MEMORY_DIR]
    dir_status = {d: (target / d).is_dir() for d in dirs}

    if _get_fmt(ctx) == "json":
        data = {
            "project": {"name": cfg.project.name, "type": cfg.project.type, "stack": list(cfg.project.stack)},
            "user": {"name": cfg.user.name, "language": cfg.user.language, "skill_level": cfg.user.skill_level},
            "agents": {"archetype": cfg.agents.archetype, "custom_agents": list(cfg.agents.custom_agents)},
            "memory": {"backend": cfg.memory.backend},
            "structure": dir_status,
            "version": __version__,
        }
        typer.echo(json.dumps(data, indent=2))
        return

    # Header
    console.print(f"\n[bold]Grimoire Project:[/bold] {cfg.project.name}")
    console.print(f"grimoire-kit {__version__}\n")

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
    for d, ok in dir_status.items():
        icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
        console.print(f"  {icon} {d}/")

    console.print()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _status_spinner(msg: str, *, show: bool = True) -> Any:
    """Return a Rich Status spinner (or a no-op context manager when silent)."""
    if show:
        return console.status(f"[bold]{msg}[/bold]", spinner="dots")
    return nullcontext()


# ── grimoire add / remove ─────────────────────────────────────────────────────────

def _load_yaml_rw(config_path: Path) -> tuple[Any, Any]:
    """Load YAML preserving formatting (for round-trip editing)."""
    from ruamel.yaml import YAML

    yaml = YAML()
    yaml.preserve_quotes = True
    with open(config_path, encoding="utf-8") as fh:
        data = yaml.load(fh)
    return yaml, data


def _save_yaml_rw(yaml: Any, data: Any, config_path: Path) -> None:
    """Write YAML back preserving formatting."""
    with open(config_path, "w", encoding="utf-8") as fh:
        yaml.dump(data, fh)


def _find_config(path: Path) -> Path:
    """Resolve project-context.yaml — walk up directories if needed."""
    from grimoire.tools._common import find_project_root

    target = path.resolve()
    config_path = target / "project-context.yaml"
    if config_path.is_file():
        return config_path
    # Walk up from target to find project root
    try:
        root = find_project_root(target)
        return root / "project-context.yaml"
    except (FileNotFoundError, PermissionError, OSError):
        console.print("[red]Not a Grimoire project[/red] — run [bold]grimoire init[/bold] first.")
        raise typer.Exit(1) from None


_add_agent_id = typer.Argument(..., help="Agent identifier to add.")
_add_path_arg = typer.Argument(Path(), help="Project root.")
_add_dry_run_opt = typer.Option(False, "--dry-run", "-n", help="Show plan without modifying config.")


@app.command("add", rich_help_panel="Agents")
def add_agent(
    ctx: typer.Context,
    agent_id: str = _add_agent_id,
    path: Path = _add_path_arg,
    dry_run: bool = _add_dry_run_opt,
) -> None:
    """Add a custom agent to the project configuration.

    [dim]Examples:[/dim]
      [cyan]grimoire add my-agent .[/cyan]
      [cyan]grimoire add analyst --dry-run[/cyan]
    """
    config_path = _find_config(path)
    yaml, data = _load_yaml_rw(config_path)
    fmt = _get_fmt(ctx)

    agents = data.get("agents") or {}
    custom: list[str] = agents.get("custom_agents") or []

    if agent_id in custom:
        if fmt == "json":
            typer.echo(json.dumps({"ok": True, "action": "add", "agent": agent_id, "status": "already_exists"}))
        else:
            console.print(f"[yellow]Agent '{agent_id}' already in project.[/yellow]")
        raise typer.Exit(0)

    if dry_run:
        if fmt == "json":
            typer.echo(json.dumps({"ok": True, "action": "add", "agent": agent_id, "dry_run": True}))
        else:
            console.print("[bold]grimoire add --dry-run[/bold]")
            console.print(f"  [cyan]plan[/cyan]  Add agent: {agent_id}")
        return

    custom.append(agent_id)
    agents["custom_agents"] = custom
    data["agents"] = agents
    _save_yaml_rw(yaml, data, config_path)
    _log_operation("add", {"agent": agent_id})

    if fmt == "json":
        typer.echo(json.dumps({"ok": True, "action": "add", "agent": agent_id}))
    else:
        console.print(f"[green]Added agent:[/green] {agent_id}")


_rm_agent_id = typer.Argument(..., help="Agent identifier to remove.")
_rm_path_arg = typer.Argument(Path(), help="Project root.")
_rm_dry_run_opt = typer.Option(False, "--dry-run", "-n", help="Show plan without modifying config.")


@app.command("remove", rich_help_panel="Agents")
def remove_agent(
    ctx: typer.Context,
    agent_id: str = _rm_agent_id,
    path: Path = _rm_path_arg,
    dry_run: bool = _rm_dry_run_opt,
) -> None:
    """Remove a custom agent from the project configuration.

    [dim]Examples:[/dim]
      [cyan]grimoire remove my-agent . --yes[/cyan]
      [cyan]grimoire remove analyst --dry-run[/cyan]
    """
    config_path = _find_config(path)
    yaml, data = _load_yaml_rw(config_path)
    fmt = _get_fmt(ctx)

    agents = data.get("agents") or {}
    custom: list[str] = agents.get("custom_agents") or []

    if agent_id not in custom:
        if fmt == "json":
            typer.echo(json.dumps({"ok": False, "action": "remove", "agent": agent_id, "status": "not_found"}))
        else:
            console.print(f"[yellow]Agent '{agent_id}' not in project.[/yellow]")
        raise typer.Exit(1)

    if dry_run:
        if fmt == "json":
            typer.echo(json.dumps({"ok": True, "action": "remove", "agent": agent_id, "dry_run": True}))
        else:
            console.print("[bold]grimoire remove --dry-run[/bold]")
            console.print(f"  [cyan]plan[/cyan]  Remove agent: {agent_id}")
        return

    if not (ctx.obj or {}).get("yes"):
        typer.confirm(f"Remove agent '{agent_id}' from project?", abort=True)

    custom.remove(agent_id)
    agents["custom_agents"] = custom
    data["agents"] = agents
    _save_yaml_rw(yaml, data, config_path)
    _log_operation("remove", {"agent": agent_id})

    if fmt == "json":
        typer.echo(json.dumps({"ok": True, "action": "remove", "agent": agent_id}))
    else:
        console.print(f"[green]Removed agent:[/green] {agent_id}")


# ── grimoire validate ─────────────────────────────────────────────────────────────

_validate_path_arg = typer.Argument(Path(), help="Project root to validate.")


@app.command("validate", rich_help_panel="Validation")
def validate(
    ctx: typer.Context,
    path: Path = _validate_path_arg,
) -> None:
    """Validate project-context.yaml against the Grimoire schema.

    [dim]Examples:[/dim]
      [cyan]grimoire validate .[/cyan]          Validate current project
      [cyan]grimoire validate -o json .[/cyan]  JSON output for CI
    """
    from grimoire.core.validator import validate_config
    from grimoire.tools._common import load_yaml

    fmt = _get_fmt(ctx)
    target = path.resolve()
    config_path = target / "project-context.yaml"

    if not config_path.is_file():
        if fmt == "json":
            typer.echo(json.dumps({"valid": False, "errors": [{"path": "", "message": "project-context.yaml not found"}]}, indent=2))
        else:
            console.print("[red]No project-context.yaml found.[/red]")
        raise typer.Exit(1)

    data = load_yaml(config_path)
    errors = validate_config(data, project_root=target)

    if fmt == "json":
        items = [{"path": e.path, "message": e.message} for e in errors]
        typer.echo(json.dumps({"valid": not errors, "errors": items, "count": len(errors)}, indent=2))
        raise typer.Exit(0 if not errors else 1)

    if not errors:
        console.print("[green]project-context.yaml is valid.[/green]")
        raise typer.Exit(0)

    console.print(f"[red]Found {len(errors)} validation error(s):[/red]\n")
    for err in errors:
        console.print(f"  [red]•[/red] {err}")
    raise typer.Exit(1)


# ── grimoire lint ─────────────────────────────────────────────────────────────────

_lint_path_arg = typer.Argument(Path(), help="Path to YAML config file or project directory.")


@app.command("lint", rich_help_panel="Validation")
def lint(
    ctx: typer.Context,
    path: Path = _lint_path_arg,
    format_: str = typer.Option("", "--format", "-f", help="Output format (deprecated — use global --output)."),
) -> None:
    """Lint ``project-context.yaml`` — validate structure, types, and references."""
    from grimoire.core.validator import validate_config
    from grimoire.tools._common import load_yaml

    fmt = format_ or _get_fmt(ctx)
    target = path.resolve()
    config_path = target if target.suffix in {".yaml", ".yml"} else target / "project-context.yaml"

    if not config_path.is_file():
        console.print(f"[red]File not found:[/red] {config_path}")
        raise typer.Exit(1)

    data = load_yaml(config_path)
    errors = validate_config(data, project_root=config_path.parent)

    if fmt == "json":
        items = [{"path": e.path, "message": e.message, "suggestion": e.suggestion} for e in errors]
        typer.echo(json.dumps({"file": str(config_path), "errors": items, "count": len(errors)}, indent=2))
        raise typer.Exit(0 if not errors else 1)

    if not errors:
        console.print(f"[green]✓[/green] {config_path.name} — no issues found.")
        raise typer.Exit(0)

    console.print(f"[bold]{config_path.name}[/bold] — [red]{len(errors)} issue(s)[/red]\n")
    for err in errors:
        line = f"  [red]✗[/red] [bold]{err.path}[/bold]: {err.message}"
        if err.suggestion:
            line += f"\n    [dim]→ {err.suggestion}[/dim]"
        console.print(line)
    raise typer.Exit(1)


# ── grimoire up ───────────────────────────────────────────────────────────────────

_up_path_arg = typer.Argument(Path(), help="Project root.")
_up_dry_run_opt = typer.Option(False, "--dry-run", help="Show plan without applying.")


@app.command("up", rich_help_panel="Project")
def up(
    ctx: typer.Context,
    path: Path = _up_path_arg,
    dry_run: bool = _up_dry_run_opt,
) -> None:
    """Reconcile the project state with project-context.yaml."""
    from grimoire.core.project import GrimoireProject

    target = path.resolve()
    try:
        project = GrimoireProject(target)
    except GrimoireProjectError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None

    cfg = project.config
    status = project.status()
    fmt = _get_fmt(ctx)

    if fmt != "json":
        if dry_run:
            console.print("[bold]grimoire up --dry-run[/bold]\n")
        else:
            console.print("[bold]grimoire up[/bold]\n")

    actions: list[str] = []

    # Ensure standard directories exist
    for d in (*_REQUIRED_DIRS, _MEMORY_DIR):
        dp = target / d
        if not dp.is_dir():
            actions.append(f"Create directory: {d}/")
            if not dry_run:
                dp.mkdir(parents=True, exist_ok=True)

    # Ensure agents dir exists
    agents_dir = target / "_grimoire" / "agents"
    if not agents_dir.is_dir():
        actions.append("Create directory: _grimoire/agents/")
        if not dry_run:
            agents_dir.mkdir(parents=True, exist_ok=True)

    # Summary
    if fmt != "json":
        if actions:
            for a in actions:
                icon = "[cyan]plan[/cyan]" if dry_run else "[green]done[/green]"
                console.print(f"  {icon}  {a}")
        else:
            console.print("  [green]Everything up to date.[/green]")

    # Output
    if fmt == "json":
        typer.echo(json.dumps({
            "ok": True, "project": cfg.project.name,
            "actions": actions, "dry_run": dry_run,
            "agents_count": status.agents_count,
            "directories_missing": list(status.directories_missing),
        }, indent=2))
    else:
        # Health summary
        console.print(f"\n[bold]Project:[/bold] {cfg.project.name}")
        console.print(f"  Archetype: {cfg.agents.archetype}")
        console.print(f"  Memory: {cfg.memory.backend}")
        console.print(f"  Agents: {status.agents_count}")

        if status.directories_missing:
            missing = ", ".join(status.directories_missing)
            console.print(f"\n[yellow]Missing dirs (after up):[/yellow] {missing}")


# ── grimoire registry ─────────────────────────────────────────────────────────────

registry_app = typer.Typer(help="Browse the agent registry.")
app.add_typer(registry_app, name="registry", rich_help_panel="Agents")

_reg_query_arg = typer.Argument(None, help="Search query.")


@registry_app.command("list")
def registry_list(ctx: typer.Context) -> None:
    """List all available archetypes and agents."""
    from grimoire.registry.local import LocalRegistry
    from grimoire.tools._common import find_project_root

    try:
        root = find_project_root()
    except FileNotFoundError:
        console.print("[red]Not in a Grimoire project — cannot locate kit root.[/red]")
        raise typer.Exit(1) from None

    reg = LocalRegistry(root)
    archs = reg.list_archetypes()
    if not archs:
        console.print("[yellow]No archetypes found.[/yellow]")
        return

    if _get_fmt(ctx) == "json":
        items = []
        for arch_id in archs:
            try:
                dna = reg.inspect_archetype(arch_id)
                items.append({"archetype": arch_id, "agents": len(dna.agents)})
            except Exception:
                items.append({"archetype": arch_id, "agents": None})
        typer.echo(json.dumps(items, indent=2))
        return

    tbl = Table(title="Available Archetypes")
    tbl.add_column("Archetype", style="bold")
    tbl.add_column("Agents", justify="right")

    for arch_id in archs:
        try:
            dna = reg.inspect_archetype(arch_id)
            tbl.add_row(arch_id, str(len(dna.agents)))
        except Exception:
            tbl.add_row(arch_id, "?")

    console.print(tbl)


@registry_app.command("search")
def registry_search(
    ctx: typer.Context,
    query: str = _reg_query_arg,
) -> None:
    """Search agents by keyword."""
    from grimoire.registry.local import LocalRegistry
    from grimoire.tools._common import find_project_root

    if not query:
        console.print("[red]Please provide a search query.[/red]")
        raise typer.Exit(1)

    try:
        root = find_project_root()
    except FileNotFoundError:
        console.print("[red]Not in a Grimoire project.[/red]")
        raise typer.Exit(1) from None

    reg = LocalRegistry(root)
    results = reg.search(query)

    if not results:
        console.print(f"[yellow]No agents matching '{query}'.[/yellow]")
        return

    if _get_fmt(ctx) == "json":
        items = [{"id": r.id, "archetype": r.archetype, "description": r.description or ""} for r in results]
        typer.echo(json.dumps(items, indent=2))
        return

    tbl = Table(title=f"Search: {query}")
    tbl.add_column("Agent", style="bold")
    tbl.add_column("Archetype")
    tbl.add_column("Description")

    for item in results:
        tbl.add_row(item.id, item.archetype, item.description or "—")

    console.print(tbl)


# ── grimoire diff ─────────────────────────────────────────────────────────────────


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


_diff_path_arg = typer.Argument(Path(), help="Project root.")


@app.command("diff", rich_help_panel="Configuration")
def diff_config(
    ctx: typer.Context,
    path: Path = _diff_path_arg,
) -> None:
    """Show config drift between current project and archetype defaults."""
    from ruamel.yaml import YAML

    target = path.resolve()
    config_path = target / "project-context.yaml"
    if not config_path.is_file():
        console.print("[red]No project-context.yaml found.[/red]")
        raise typer.Exit(1)

    yaml = YAML()
    with config_path.open(encoding="utf-8") as fh:
        current = yaml.load(fh)

    archetype_name = (current.get("agents") or {}).get("archetype", "minimal")

    # Compare to the _TEMPLATE_YAML defaults
    defaults = yaml.load(_TEMPLATE_YAML.format(name="__DEFAULT__", archetype=archetype_name, backend="auto"))

    flat_current = _flatten(dict(current))
    flat_defaults = _flatten(dict(defaults))

    all_keys = sorted(set(flat_current) | set(flat_defaults))

    fmt = _get_fmt(ctx)
    diffs: list[dict[str, Any]] = []

    for key in all_keys:
        cur = flat_current.get(key)
        default = flat_defaults.get(key)
        if cur != default:
            diffs.append({"key": key, "current": cur, "default": default})

    if fmt == "json":
        typer.echo(json.dumps({"archetype": archetype_name, "diffs": diffs}, indent=2, default=str))
        return

    if not diffs:
        console.print(f"[green]No drift from archetype defaults ({archetype_name}).[/green]")
        return

    console.print(f"[bold]Config drift vs archetype '{archetype_name}' defaults[/bold]\n")
    tbl = Table()
    tbl.add_column("Key", style="bold")
    tbl.add_column("Current", style="green")
    tbl.add_column("Default", style="dim")
    for d in diffs:
        tbl.add_row(d["key"], str(d["current"] or "—"), str(d["default"] or "—"))
    console.print(tbl)
    console.print(f"\n[dim]{len(diffs)} difference(s) found.[/dim]")


# ── grimoire schema ───────────────────────────────────────────────────────────────


@app.command("schema", rich_help_panel="Validation")
def schema_cmd() -> None:
    """Export JSON Schema for ``project-context.yaml``."""
    from grimoire.core.schema import generate_schema

    typer.echo(json.dumps(generate_schema(), indent=2))


# ── grimoire check ────────────────────────────────────────────────────────────────

_check_path_arg = typer.Argument(Path(), help="Project directory to check.")


@app.command("check", rich_help_panel="Validation")
def check(
    ctx: typer.Context,
    path: Path = _check_path_arg,
) -> None:
    """Run lint + validate + doctor in one pass.

    [dim]Examples:[/dim]
      [cyan]grimoire check .[/cyan]             Full project validation
      [cyan]grimoire check -o json . | jq .all_ok[/cyan]
    """
    from grimoire.core.validator import validate_config
    from grimoire.tools._common import load_yaml

    fmt = _get_fmt(ctx)
    quiet = (ctx.obj or {}).get("quiet", False)
    target = path.resolve()
    config_path = target / "project-context.yaml"
    phases: list[dict[str, Any]] = []
    all_ok = True

    def _phase_header(label: str) -> None:
        if fmt != "json" and not quiet:
            console.print(f"[bold]{label}[/bold]")

    # ── Phase 1: lint ─────────
    _phase_header("1/3 Lint")
    with _timed_phase("check/lint"):
        if not config_path.is_file():
            phases.append({"phase": "lint", "ok": False, "errors": ["project-context.yaml not found"]})
            if fmt == "json":
                typer.echo(json.dumps({"all_ok": False, "phases": phases}, indent=2))
            else:
                console.print("  [red]✗[/red] project-context.yaml not found")
            raise typer.Exit(1)

        data = load_yaml(config_path)
        errors = validate_config(data, project_root=target)
        lint_errors = [str(e) for e in errors]
        if errors:
            all_ok = False
        phases.append({"phase": "lint", "ok": not errors, "errors": lint_errors})
        if fmt != "json":
            if errors:
                for err in errors:
                    console.print(f"  [red]✗[/red] {err}")
            else:
                console.print("  [green]✓[/green] No lint issues")

    # ── Phase 2: validate ─────
    _phase_header("2/3 Validate")
    validate_errors: list[str] = []
    with _timed_phase("check/validate"):
        try:
            cfg = GrimoireConfig.from_yaml(config_path)
            warnings = cfg.validate()
            if warnings:
                all_ok = False
                validate_errors = list(warnings)
        except GrimoireConfigError as exc:
            all_ok = False
            validate_errors = [str(exc)]
    phases.append({"phase": "validate", "ok": not validate_errors, "errors": validate_errors})
    if fmt != "json":
        if validate_errors:
            for w in validate_errors:
                console.print(f"  [yellow]![/yellow] {w}")
        else:
            console.print("  [green]✓[/green] Config valid")

    # ── Phase 3: structure ────
    _phase_header("3/3 Structure")
    with _timed_phase("check/structure"):
        missing = [d for d in (*_REQUIRED_DIRS, _MEMORY_DIR) if not (target / d).is_dir()]
    if missing:
        all_ok = False
    phases.append({"phase": "structure", "ok": not missing, "missing": missing})
    if fmt != "json":
        if missing:
            for d in missing:
                console.print(f"  [yellow]![/yellow] Missing directory: {d}")
        else:
            console.print("  [green]✓[/green] All directories present")

    if fmt == "json":
        typer.echo(json.dumps({"all_ok": all_ok, "phases": phases}, indent=2))
    elif all_ok:
        console.print("\n[bold green]All checks passed.[/bold green]")
    else:
        console.print("\n[bold yellow]Some issues found — see above.[/bold yellow]")

    if not all_ok:
        raise typer.Exit(1)


# ── grimoire config ───────────────────────────────────────────────────────────────

config_app = typer.Typer(help="Manage project configuration.")
app.add_typer(config_app, name="config", rich_help_panel="Configuration")


def _resolve_config_key(data: Any, key: str) -> Any:
    """Walk YAML data by dot-notation key — raise typer.Exit(1) if not found."""
    value = data
    for part in key.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            console.print(f"[red]Key not found:[/red] {key}")
            raise typer.Exit(1)
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
            raise typer.Exit(1)

    last = parts[-1]
    if not isinstance(target, dict) or last not in target:
        console.print(f"[red]Key not found:[/red] {key}")
        raise typer.Exit(1)

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


# ── grimoire completion ───────────────────────────────────────────────────────────

completion_app = typer.Typer(help="Shell completion utilities.")
app.add_typer(completion_app, name="completion", rich_help_panel="Utilities")

_SUPPORTED_SHELLS = frozenset({"bash", "zsh", "fish"})


def _generate_completion_script(shell: str) -> str:
    """Generate a shell completion script via subprocess — raise typer.Exit(1) on failure."""
    import subprocess

    if shell not in _SUPPORTED_SHELLS:
        console.print(f"[red]Unsupported shell:[/red] {shell} (supported: {', '.join(sorted(_SUPPORTED_SHELLS))})")
        raise typer.Exit(1)

    env = {**os.environ, "_GRIMOIRE_COMPLETE": f"{shell}_source"}
    result = subprocess.run(
        [sys.executable, "-m", "grimoire"],
        capture_output=True, text=True, env=env, timeout=10,
    )

    if result.returncode != 0 or not result.stdout.strip():
        console.print("[yellow]Completion script could not be generated.[/yellow]")
        raise typer.Exit(1)

    return result.stdout.strip()


@completion_app.command("install")
def completion_install(
    shell: str = typer.Option(
        ...,
        "--shell", "-s",
        help="Shell to install completion for: bash, zsh, fish.",
    ),
) -> None:
    """Install shell auto-completion for grimoire CLI."""
    script = _generate_completion_script(shell)

    shell_rc = {"bash": "~/.bashrc", "zsh": "~/.zshrc", "fish": "~/.config/fish/completions/grimoire.fish"}
    target = Path(shell_rc[shell]).expanduser()

    if shell == "fish":
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(script + "\n", encoding="utf-8")
        console.print(f"[green]✓[/green] Completion installed to {target}")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        marker = "# grimoire shell completion"
        existing = target.read_text(encoding="utf-8") if target.is_file() else ""
        if marker in existing:
            console.print(f"[dim]Completion already installed in {target}[/dim]")
            return
        with target.open("a", encoding="utf-8") as fh:
            fh.write(f"\n{marker}\n{script}\n")
        console.print(f"[green]✓[/green] Completion appended to {target}")
        console.print(f"[dim]Reload with: source {target}[/dim]")

    _log_operation("completion_install", {"shell": shell})


@completion_app.command("export")
def completion_export(
    shell: str = typer.Option(
        ...,
        "--shell", "-s",
        help="Shell to export completion for: bash, zsh, fish.",
    ),
) -> None:
    """Export shell completion script to stdout (for piping/redirection)."""
    typer.echo(_generate_completion_script(shell))


# ── grimoire self ─────────────────────────────────────────────────────────────────

self_app = typer.Typer(help="Grimoire self-management utilities.")
app.add_typer(self_app, name="self", rich_help_panel="Info")


@self_app.command("version")
def self_version(ctx: typer.Context) -> None:
    """Show installed grimoire-kit version and check for updates on PyPI."""
    fmt = _get_fmt(ctx)
    installed = __version__

    # Detect editable install
    install_path = Path(__file__).resolve().parent.parent
    editable = (install_path / "__pycache__").parent.parent.name != "site-packages"

    # Check PyPI for latest version (skip when offline)
    latest: str | None = None
    if is_online():
        try:
            from urllib.request import urlopen

            url = "https://pypi.org/pypi/grimoire-kit/json"
            with urlopen(url, timeout=5) as resp:  # noqa: S310
                pypi_data = json.loads(resp.read())
                latest = pypi_data.get("info", {}).get("version")
        except Exception:
            latest = None

    update_available = bool(latest and latest != installed)

    if fmt == "json":
        data: dict[str, Any] = {
            "installed": installed,
            "latest": latest,
            "update_available": update_available,
            "editable_install": editable,
            "install_path": str(install_path),
        }
        typer.echo(json.dumps(data, indent=2))
        return

    console.print(f"[bold]grimoire-kit[/bold]  {installed}")
    if editable:
        console.print("  [dim]Editable install (development mode)[/dim]")
    if latest and update_available:
        console.print(f"  [yellow]Update available:[/yellow] {latest}")
        console.print("  [dim]Run: pip install --upgrade grimoire-kit[/dim]")
    elif latest:
        console.print("  [green]Up to date[/green]")
    else:
        console.print("  [dim]Could not check for updates[/dim]")


@self_app.command("diagnose")
def self_diagnose(ctx: typer.Context) -> None:
    """Run a self-diagnostic on the grimoire-kit installation."""
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as pkg_version

    fmt = _get_fmt(ctx)

    checks: list[dict[str, Any]] = []

    # Check core dependencies
    required_deps = ("ruamel.yaml", "typer", "rich")
    optional_deps = ("mcp",)

    for dep in required_deps:
        try:
            ver = pkg_version(dep)
            checks.append({"name": dep, "status": "ok", "version": ver, "required": True})
        except PackageNotFoundError:
            checks.append({"name": dep, "status": "missing", "version": None, "required": True})

    for dep in optional_deps:
        try:
            ver = pkg_version(dep)
            checks.append({"name": dep, "status": "ok", "version": ver, "required": False})
        except PackageNotFoundError:
            checks.append({"name": dep, "status": "missing", "version": None, "required": False})

    # Check Python version
    py_ver = sys.version_info
    py_ok = py_ver >= (3, 12)
    checks.append({
        "name": "python",
        "status": "ok" if py_ok else "warn",
        "version": f"{py_ver.major}.{py_ver.minor}.{py_ver.micro}",
        "required": True,
        "detail": None if py_ok else "Python 3.12+ recommended",
    })

    # Check entry point
    import shutil
    grimoire_bin = shutil.which("grimoire")
    checks.append({
        "name": "grimoire-cli",
        "status": "ok" if grimoire_bin else "warn",
        "version": None,
        "required": True,
        "detail": grimoire_bin or "Not found in PATH",
    })

    all_ok = all(c["status"] == "ok" for c in checks if c["required"])

    if fmt == "json":
        typer.echo(json.dumps({"all_ok": all_ok, "checks": checks}, indent=2))
        return

    console.print("[bold]grimoire self diagnose[/bold]\n")
    for c in checks:
        icon = "[green]✓[/green]" if c["status"] == "ok" else (
            "[yellow]![/yellow]" if c["status"] == "warn" else "[red]✗[/red]"
        )
        ver = f" {c['version']}" if c.get("version") else ""
        opt = " [dim](optional)[/dim]" if not c["required"] else ""
        console.print(f"  {icon} {c['name']}{ver}{opt}")
        if c.get("detail"):
            console.print(f"    [dim]{c['detail']}[/dim]")

    console.print()
    if all_ok:
        console.print("[bold green]All checks passed.[/bold green]")
    else:
        console.print("[bold yellow]Some issues found — see above.[/bold yellow]")


# ── grimoire plugins ──────────────────────────────────────────────────────────────

plugins_app = typer.Typer(help="Discover installed plugins.")
app.add_typer(plugins_app, name="plugins", rich_help_panel="Utilities")


@plugins_app.command("list")
def plugins_list(ctx: typer.Context) -> None:
    """List discovered tools and backend plugins."""
    from grimoire.registry.discovery import discover_backends, discover_tools

    fmt = _get_fmt(ctx)
    tools = discover_tools()
    backends = discover_backends()

    if fmt == "json":
        data: dict[str, Any] = {
            "tools": sorted(tools),
            "backends": sorted(backends),
        }
        typer.echo(json.dumps(data, indent=2))
        return

    if tools:
        console.print("\n[bold]Tools[/bold]")
        for tid in sorted(tools):
            console.print(f"  [green]•[/green] {tid}")

    if backends:
        console.print("\n[bold]Backends[/bold]")
        for bid in sorted(backends):
            console.print(f"  [green]•[/green] {bid}")

    if not tools and not backends:
        console.print("[dim]No plugins discovered.[/dim]")

    console.print()


# ── grimoire upgrade ──────────────────────────────────────────────────────────────

_upgrade_path_arg = typer.Argument(Path(), help="Path to the v2 project.")
_upgrade_dry_run_opt = typer.Option(False, "--dry-run", "-n", help="Show plan without applying.")


@app.command("upgrade", rich_help_panel="Utilities")
def upgrade(
    ctx: typer.Context,
    path: Path = _upgrade_path_arg,
    dry_run: bool = _upgrade_dry_run_opt,
) -> None:
    """Migrate a v2 project to v3 structure.

    [dim]Examples:[/dim]
      [cyan]grimoire upgrade . --dry-run[/cyan]  Preview migration
      [cyan]grimoire upgrade -o json .[/cyan]    JSON output for CI
    """
    from grimoire.cli.cmd_upgrade import (
        detect_version,
        execute_upgrade,
        plan_upgrade,
    )

    fmt = _get_fmt(ctx)
    target = path.resolve()
    version = detect_version(target)

    if version == "v3":
        if fmt == "json":
            typer.echo(json.dumps({"ok": True, "version": "v3", "status": "already_v3", "actions": []}))
        else:
            console.print("[green]Project is already v3 — nothing to do.[/green]")
        return

    if version == "unknown":
        if fmt == "json":
            typer.echo(json.dumps({"ok": False, "error": "No v2 project found"}))
        else:
            console.print("[red]No v2 project-context.yaml found at this path.[/red]")
        raise typer.Exit(1)

    plan = plan_upgrade(target)
    quiet = (ctx.obj or {}).get("quiet", False)
    with _status_spinner("Upgrading…", show=(fmt != "json" and not quiet and not dry_run)):
        completed = execute_upgrade(target, plan, dry_run=dry_run)

    if not dry_run:
        _log_operation("upgrade", {"from": version, "actions": len(completed)})

    if fmt == "json":
        typer.echo(json.dumps({
            "ok": True,
            "version": version,
            "dry_run": dry_run,
            "warnings": plan.warnings,
            "actions": completed,
        }, indent=2))
        return

    if dry_run:
        console.print("[bold]grimoire upgrade --dry-run[/bold]\n")
    else:
        console.print("[bold]grimoire upgrade[/bold]\n")

    if plan.warnings:
        for w in plan.warnings:
            console.print(f"  [yellow]⚠ {w}[/yellow]")

    for desc in completed:
        icon = "[cyan]plan[/cyan]" if dry_run else "[green]done[/green]"
        console.print(f"  {icon}  {desc}")

    if not completed and not plan.warnings:
        console.print("  [green]Nothing to do.[/green]")

    console.print(f"\n[bold]Migration {'planned' if dry_run else 'complete'}.[/bold]")


# ── grimoire merge ────────────────────────────────────────────────────────────────

_merge_from_arg = typer.Argument(..., help="Source directory to merge from.")
_merge_target_opt = typer.Option(Path(), "--target", "-t", help="Target project directory.")
_merge_dry_run_opt = typer.Option(False, "--dry-run", "-n", help="Show plan without merging.")
_merge_force_opt = typer.Option(False, "--force", "-f", help="Overwrite conflicting files.")


@app.command("merge", rich_help_panel="Utilities")
def merge(
    ctx: typer.Context,
    source: Path = _merge_from_arg,
    target: Path = _merge_target_opt,
    dry_run: bool = _merge_dry_run_opt,
    force: bool = _merge_force_opt,
    undo: bool = typer.Option(False, "--undo", help="Undo the last merge in the target."),
) -> None:
    """Merge Grimoire files from a source into a project."""
    from grimoire.cli.cmd_merge import run_merge, run_undo
    from grimoire.core.exceptions import GrimoireMergeError

    resolved_target = target.resolve()

    if undo:
        if not (ctx.obj or {}).get("yes"):
            typer.confirm("Undo last merge? This will delete merged files.", abort=True)
        try:
            deleted = run_undo(resolved_target)
        except GrimoireMergeError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from None

        if deleted:
            for f in deleted:
                console.print(f"  [red]deleted[/red]  {f}")
            console.print(f"\n[bold]Undo complete — {len(deleted)} file(s) removed.[/bold]")
        else:
            console.print("[yellow]Nothing to undo.[/yellow]")
        return

    resolved_source = source.resolve()

    try:
        with _status_spinner("Merging…", show=(not dry_run)):
            plan, result = run_merge(
                resolved_source, resolved_target, dry_run=dry_run, force=force,
            )
    except GrimoireMergeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None

    label = "grimoire merge --dry-run" if dry_run else "grimoire merge"
    console.print(f"[bold]{label}[/bold]\n")

    if plan.warnings:
        for w in plan.warnings:
            console.print(f"  [yellow]⚠ {w}[/yellow]")

    for f in result.files_created:
        icon = "[cyan]plan[/cyan]" if dry_run else "[green]created[/green]"
        console.print(f"  {icon}  {f}")

    for f in result.files_skipped:
        console.print(f"  [yellow]skipped[/yellow]  {f}")

    for d in result.directories_created:
        icon = "[cyan]plan[/cyan]" if dry_run else "[green]mkdir[/green]"
        console.print(f"  {icon}  {d}/")

    total = len(result.files_created) + len(result.files_skipped)
    if not dry_run:
        _log_operation("merge", {"source": str(resolved_source), "files": len(result.files_created)})
    console.print(f"\n[bold]{total} file(s) processed.[/bold]")


# ── grimoire setup ──────────────────────────────────────────────────────────────

_setup_path_arg = typer.Argument(Path(), help="Project root.")
_setup_check_opt = typer.Option(False, "--check", help="Audit only — no changes.")
_setup_sync_opt = typer.Option(False, "--sync", help="Sync from project-context.yaml.")
_setup_json_opt = typer.Option(False, "--json", help="JSON output.")
_setup_user_opt = typer.Option(None, "--user", help="User name.")
_setup_lang_opt = typer.Option(None, "--lang", help="Communication language.")
_setup_doc_lang_opt = typer.Option(None, "--doc-lang", help="Document language.")
_setup_skill_opt = typer.Option(None, "--skill-level", help="Skill level (beginner/intermediate/expert).")


@app.command("setup", rich_help_panel="Utilities")
def setup(
    ctx: typer.Context,
    path: Path = _setup_path_arg,
    check_only: bool = _setup_check_opt,
    sync: bool = _setup_sync_opt,
    json_out: bool = _setup_json_opt,
    user: str | None = _setup_user_opt,
    lang: str | None = _setup_lang_opt,
    doc_lang: str | None = _setup_doc_lang_opt,
    skill_level: str | None = _setup_skill_opt,
) -> None:
    """Sync user config (name, language, skill) across all BMAD config files."""
    from grimoire.cli.cmd_setup import SetupResult, apply, check, load_user_values

    # Respect both --json flag and global -o json
    use_json = json_out or _get_fmt(ctx) == "json"
    target = path.resolve()
    pcy = target / "project-context.yaml"

    if not pcy.is_file():
        console.print("[red]project-context.yaml not found[/red] — run [bold]grimoire init[/bold] first.")
        raise typer.Exit(1)

    current = load_user_values(pcy)

    # Apply CLI overrides if any
    has_override = any([user, lang, doc_lang, skill_level])
    if has_override:
        current.user_name = user or current.user_name
        current.communication_language = lang or current.communication_language
        current.document_output_language = doc_lang or current.document_output_language
        current.user_skill_level = skill_level or current.user_skill_level

    def _print_result(result: SetupResult) -> None:
        if use_json:
            data = {
                "synced": result.is_synced,
                "diffs": [{"file": d.file, "key": d.key, "current": d.current, "expected": d.expected} for d in result.diffs],
                "updated": result.updated_files,
                "skipped": result.skipped_files,
                "errors": result.errors,
            }
            typer.echo(json.dumps(data, indent=2, ensure_ascii=False))
            return
        if result.updated_files:
            console.print("\n[green]Updated:[/green]")
            for f in result.updated_files:
                console.print(f"  • {f}")
        if result.skipped_files:
            console.print("\n[yellow]Skipped (not found):[/yellow]")
            for f in result.skipped_files:
                console.print(f"  • {f}")
        if result.diffs:
            console.print("\n[red]Remaining diffs:[/red]")
            for d in result.diffs:
                console.print(f"  • {d.file} → {d.key}: '{d.current}' ≠ '{d.expected}'")
        elif not result.errors:
            console.print("[green]All config files are in sync.[/green]")

    if check_only:
        result = check(target, current)
        _print_result(result)
        raise typer.Exit(0 if result.is_synced else 1)

    if sync or has_override:
        result = apply(target, current)
        _print_result(result)
        if result.updated_files:
            _log_operation("setup", {"updated": str(len(result.updated_files))})
        raise typer.Exit(0 if not result.errors else 1)

    # Default: sync (same as --sync)
    result = apply(target, current)
    _print_result(result)
    if result.updated_files:
        _log_operation("setup", {"updated": str(len(result.updated_files))})
    raise typer.Exit(0 if not result.errors else 1)


# ── grimoire env ──────────────────────────────────────────────────────────────


@app.command("env", rich_help_panel="Info")
def env_cmd(ctx: typer.Context) -> None:
    """Show environment info for debugging and bug reports."""
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as pkg_version

    fmt = _get_fmt(ctx)

    deps: dict[str, str | None] = {}
    for dep in ("ruamel.yaml", "typer", "rich", "mcp"):
        try:
            deps[dep] = pkg_version(dep)
        except PackageNotFoundError:
            deps[dep] = None

    _env_var_names = (
        "GRIMOIRE_LOG_LEVEL", "GRIMOIRE_DEBUG",
        "GRIMOIRE_OUTPUT", "GRIMOIRE_QUIET", "GRIMOIRE_OFFLINE",
        "NO_COLOR",
    )
    env_vars = {var: os.environ.get(var) for var in _env_var_names}
    online = is_online()

    project_info: dict[str, str] | None = None
    try:
        ctx_file = _find_config(Path())
        cfg = GrimoireConfig.from_yaml(ctx_file)
        project_info = {"root": str(ctx_file.parent), "name": cfg.project.name}
    except (typer.Exit, GrimoireError):
        project_info = None

    if fmt == "json":
        data: dict[str, Any] = {
            "grimoire_version": __version__,
            "python": sys.version.split()[0],
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "arch": platform.machine(),
            "online": online,
            "dependencies": deps,
            "environment": env_vars,
            "project": project_info,
        }
        typer.echo(json.dumps(data, indent=2))
        return

    console.print(f"\n[bold]Grimoire Kit[/bold]  v{__version__}\n")

    console.print("[bold]Runtime[/bold]")
    console.print(f"  Python       {sys.version.split()[0]} ({platform.python_implementation()})")
    console.print(f"  Platform     {platform.platform()}")
    console.print(f"  Arch         {platform.machine()}")
    online_icon = "[green]✓[/green]" if online else "[yellow]✗[/yellow]"
    console.print(f"  Online       {online_icon}")

    console.print("\n[bold]Dependencies[/bold]")
    for dep, ver in deps.items():
        if ver:
            console.print(f"  [green]✓[/green] {dep} {ver}")
        else:
            console.print(f"  [dim]✗ {dep} (not installed)[/dim]")

    console.print("\n[bold]Environment[/bold]")
    for var, val in env_vars.items():
        console.print(f"  {var}={val or '[dim]—[/dim]'}")

    if project_info and project_info["name"] != "(unreadable)":
        console.print("\n[bold]Project[/bold]")
        console.print(f"  Root  {project_info['root']}")
        console.print(f"  Name  {project_info['name']}")
    elif project_info:
        console.print("\n[yellow]project-context.yaml found but unreadable[/yellow]")
    else:
        console.print("\n[dim]No project-context.yaml in current directory.[/dim]")

    console.print()


# ── Audit log ─────────────────────────────────────────────────────────────────

_AUDIT_FILENAME = ".grimoire-audit.jsonl"
_AUDIT_MAX_ENTRIES = 5000


def _log_operation(command: str, args: dict[str, Any] | None = None, *, ok: bool = True) -> None:
    """Append an entry to the project audit log (best-effort, silent on failure)."""
    import datetime as _dt

    try:
        from grimoire.tools._common import find_project_root

        root = find_project_root()
    except FileNotFoundError:
        return
    log_dir = root / "_grimoire" / "_memory"
    if not log_dir.is_dir():
        return
    log_file = log_dir / _AUDIT_FILENAME
    record = {
        "ts": _dt.datetime.now(tz=_dt.UTC).isoformat(),
        "v": __version__,
        "cmd": command,
        "ok": ok,
    }
    if args:
        record["args"] = {k: str(v) for k, v in args.items()}
    try:
        with open(log_file, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")
        # Truncate if too large — atomic read+write
        with open(log_file, "r+", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
            if len(lines) > _AUDIT_MAX_ENTRIES:
                fh.seek(0)
                fh.truncate()
                fh.write("\n".join(lines[-_AUDIT_MAX_ENTRIES:]) + "\n")
    except OSError as exc:
        if os.environ.get("GRIMOIRE_DEBUG"):
            console.print(f"[dim]Audit log write failed: {exc}[/dim]")


@app.command("history", rich_help_panel="Info")
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
    # Most recent first, limited
    entries = entries[-limit:][::-1]

    if fmt == "json":
        typer.echo(json.dumps({"entries": entries, "total": len(entries), "total_entries": total_entries, "skipped": skipped}, indent=2))
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


# ── grimoire repair ──────────────────────────────────────────────────────────────

_repair_path_arg = typer.Argument(Path(), help="Project root to repair.")


@app.command("repair", rich_help_panel="Utilities")
def repair(
    ctx: typer.Context,
    path: Path = _repair_path_arg,
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show what would be repaired without making changes."),
) -> None:
    """Auto-repair common project issues detected by ``grimoire doctor``.

    [dim]Examples:[/dim]
      [cyan]grimoire repair .[/cyan]            Repair current project
      [cyan]grimoire repair . --dry-run[/cyan]  Preview repairs
      [cyan]grimoire repair . -o json[/cyan]    Machine-readable output
    """
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

    # 1. Create missing directories
    for d in (*_REQUIRED_DIRS, _MEMORY_DIR):
        dp = target / d
        if not dp.is_dir():
            actions.append({"action": "create_dir", "path": d})
            if not dry_run:
                dp.mkdir(parents=True, exist_ok=True)
            if fmt != "json":
                tag = "[dim]would create[/dim]" if dry_run else "[green]created[/green]"
                console.print(f"  {tag}  {d}/")

    # 2. Remove stale audit log entries (>90 days)
    audit_log = target / "_grimoire" / "_memory" / _AUDIT_FILENAME
    if audit_log.is_file():
        import datetime as _dt

        cutoff = _dt.datetime.now(_dt.UTC) - _dt.timedelta(days=90)
        lines = audit_log.read_text(encoding="utf-8").splitlines()
        kept: list[str] = []
        trimmed = 0
        for line in lines:
            try:
                entry = json.loads(line)
                ts = _dt.datetime.fromisoformat(entry.get("ts", ""))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=_dt.UTC)
                if ts >= cutoff:
                    kept.append(line)
                else:
                    trimmed += 1
            except (json.JSONDecodeError, ValueError):
                kept.append(line)
        if trimmed > 0:
            actions.append({"action": "trim_audit_log", "removed": str(trimmed)})
            if not dry_run:
                audit_log.write_text("\n".join(kept) + "\n" if kept else "", encoding="utf-8")
            if fmt != "json":
                tag = "[dim]would trim[/dim]" if dry_run else "[green]trimmed[/green]"
                console.print(f"  {tag}  audit.jsonl — {trimmed} old entries")

    # 3. Log + Summary
    if not dry_run and actions:
        _log_operation("repair", {"count": str(len(actions))})

    if fmt == "json":
        typer.echo(json.dumps({"ok": True, "project": str(target), "dry_run": dry_run, "actions": actions, "count": len(actions)}, indent=2))
    elif not actions:
        console.print("  [green]No issues found — project is healthy.[/green]")
    else:
        verb = "would be applied" if dry_run else "applied"
        console.print(f"\n[bold]{len(actions)} repair(s) {verb}.[/bold]")


# ── Deprecation warnings ─────────────────────────────────────────────────────

# Map old flag → (new flag, removal version)
_DEPRECATED_FLAGS: dict[str, tuple[str, str]] = {
    # Example: "--old-flag": ("--new-flag", "4.0"),
}


def _warn_deprecated() -> None:
    """Emit warnings for deprecated flags present in sys.argv."""
    for old_flag, (new_flag, version) in _DEPRECATED_FLAGS.items():
        if old_flag in sys.argv:
            console.print(
                f"[yellow]⚠ '{old_flag}' is deprecated and will be removed in v{version}. "
                f"Use '{new_flag}' instead.[/yellow]"
            )


# ── Offline mode detection ────────────────────────────────────────────────────


def _is_online(*, timeout: float = 1.0) -> bool:
    """Quick connectivity test — returns *False* if unreachable.

    Respects ``GRIMOIRE_OFFLINE=1`` env override (always offline).
    Result is cached for the process lifetime.
    """
    if os.environ.get("GRIMOIRE_OFFLINE", "").lower() in ("1", "true"):
        return False
    import socket

    try:
        socket.create_connection(("1.1.1.1", 53), timeout=timeout).close()
    except OSError:
        return False
    return True


# Lazy wrapper — avoids network probe until first use
_online_cache: bool | None = None


def is_online() -> bool:
    """Cached online check — probes network at most once per process."""
    global _online_cache
    if _online_cache is None:
        _online_cache = _is_online(timeout=0.5)
    return _online_cache


# ── Signal handling ───────────────────────────────────────────────────────────

def _handle_signal(signum: int, _frame: Any) -> None:
    """Handle SIGINT/SIGTERM — print message and exit cleanly."""
    sig_name = signal.Signals(signum).name
    console.print(f"\n[yellow]⚠ Interrupted ({sig_name})[/yellow]")
    raise SystemExit(128 + signum)


def _install_signal_handlers() -> None:
    """Register graceful signal handlers for SIGINT and SIGTERM."""
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)


# ── Performance profiling ─────────────────────────────────────────────────────

_phase_timings: list[tuple[str, float]] = []


@contextmanager
def _timed_phase(name: str) -> Generator[None, None, None]:
    """Record wall-clock duration of a named phase (used with ``--profile``)."""
    start = time.perf_counter()
    yield
    _phase_timings.append((name, time.perf_counter() - start))


def _display_profile(total: float) -> None:
    """Print a Rich tree with per-phase timings and percentages."""
    from rich.tree import Tree

    tree = Tree(f"[bold]Profile[/bold]  total={total * 1000:.0f}ms")
    for name, elapsed in _phase_timings:
        pct = (elapsed / total * 100) if total > 0 else 0
        ms = elapsed * 1000
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        tree.add(f"[cyan]{name:<24}[/cyan] {ms:>7.0f}ms  {bar} {pct:>5.1f}%")
    console.print(tree)
    _phase_timings.clear()


# ── Error formatting ──────────────────────────────────────────────────────────

_RECOVERY_HINTS: dict[str, str] = {
    "GR001": "Check your project-context.yaml syntax with: grimoire validate",
    "GR002": "Run: grimoire init <path> to create a project",
    "GR003": "Verify agent ID with: grimoire registry search <name>",
    "GR004": "Check tool output with: grimoire doctor",
    "GR005": "Resolve merge conflicts manually, then retry without --force",
    "GR010": "Check your network connection and retry",
    "GR050": "Run: grimoire validate to check schema",
}


def _format_error(exc: GrimoireError) -> None:
    """Print a Grimoire error with code and recovery hint."""
    console.print(f"[bold red]Error:[/bold red] {exc}")
    code = getattr(exc, "error_code", None)
    if code:
        hint = _RECOVERY_HINTS.get(code, "")
        console.print(f"[dim]Code: {code}[/dim]")
        if hint:
            console.print(f"[yellow]→ {hint}[/yellow]")


# ── Entry point ───────────────────────────────────────────────────────────────

def cli() -> None:
    """Typer entry point for ``grimoire`` console script."""
    _install_signal_handlers()
    _expand_aliases()
    _warn_deprecated()
    _suggest_command()
    _phase_timings.clear()
    _start = time.perf_counter()
    _show_time = "--time" in sys.argv
    _show_profile = "--profile" in sys.argv
    try:
        app()
    except GrimoireError as exc:
        _format_error(exc)
        raise typer.Exit(1) from None
    except Exception as exc:
        if os.environ.get("GRIMOIRE_DEBUG"):
            from rich.traceback import Traceback

            console.print(Traceback.from_exception(type(exc), exc, exc.__traceback__, width=120, show_locals=True))
            raise typer.Exit(2) from None
        console.print(f"[bold red]Unexpected error:[/bold red] {exc}")
        console.print("[dim]Set GRIMOIRE_DEBUG=1 for full traceback.[/dim]")
        raise typer.Exit(2) from None
    finally:
        total = time.perf_counter() - _start
        if _show_profile and _phase_timings:
            _display_profile(total)
        elif _show_time:
            console.print(f"\n[dim]⏱  {total * 1000:.0f}ms[/dim]")

