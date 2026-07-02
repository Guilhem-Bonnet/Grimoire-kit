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
from grimoire.cli._cli_helpers import (
    _AUDIT_FILENAME,  # noqa: F401 - compatibility export
    _AUDIT_MAX_ENTRIES,  # noqa: F401 - compatibility export
    _EXIT_CONFIG,
    _EXIT_OK,  # noqa: F401 - compatibility export
    _EXIT_USER,  # noqa: F401 - compatibility export
    _find_config,
    _get_fmt,
    _load_yaml_rw,
    _log_operation,
    _save_yaml_rw,
)
from grimoire.cli.cmd_config import _flatten, config_app
from grimoire.cli.cmd_debugger import debugger_app
from grimoire.cli.cmd_ext import ext_app
from grimoire.cli.cmd_history import history_cmd, repair
from grimoire.cli.cmd_memory import memory_app
from grimoire.cli.cmd_missions import cockpit_app, evidence_app, intake_app, missions_app, tasks_app
from grimoire.cli.cmd_self import completion_app, is_online, plugins_app, self_app, self_update
from grimoire.cli.cmd_standard import standard_app
from grimoire.cli.cmd_workflows import registry_app, workflows_app
from grimoire.core.config import GrimoireConfig
from grimoire.core.exceptions import GrimoireConfigError, GrimoireError, GrimoireProjectError
from grimoire.core.log import configure_logging
from grimoire.core.project_layout import detect_project_layout
from grimoire.data import framework_path  # noqa: F401 - compatibility export for workflows tests/extensions

# ── Command Aliases ───────────────────────────────────────────────────────────

_ALIASES: dict[str, str] = {
    "i": "init",
    "d": "doctor",
    "s": "status",
    "v": "validate",
    "l": "lint",
    "u": "up",
    "c": "config",
    "r": "registry",
    "m": "memory",
    "dbg": "debugger",
    "st": "status",
    "ck": "check",
    "wf": "workflows",
}


def _expand_aliases() -> None:
    """Expand short command aliases in sys.argv before Typer parsing."""
    if len(sys.argv) > 1 and sys.argv[1] in _ALIASES:
        sys.argv[1] = _ALIASES[sys.argv[1]]


_KNOWN_COMMANDS: set[str] = set()


def _suggest_command() -> None:
    """If the first positional arg is not a known command, suggest fuzzy matches."""
    if len(sys.argv) < 2:
        return
    arg = sys.argv[1]
    if arg.startswith("-"):
        return
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
        "  [cyan]grimoire workflows list[/cyan]    List available Copilot workflows\n"
        "  [cyan]grimoire config show[/cyan]       Display current config\n"
        "  [cyan]grimoire version[/cyan]           Show extended version info\n"
        "\n"
        "[dim]Aliases:[/dim]  "
        "[cyan]i[/cyan]=init  [cyan]d[/cyan]=doctor  [cyan]s[/cyan]=status  "
        "[cyan]v[/cyan]=validate  [cyan]l[/cyan]=lint  [cyan]m[/cyan]=memory  [cyan]dbg[/cyan]=debugger  "
        "[cyan]ck[/cyan]=check\n"
        "\n"
        "[dim]For more help:[/dim]\n"
        "  [bold]grimoire COMMAND --help[/bold]    Show command-specific help\n"
        "  [bold]grimoire env[/bold]               Show full environment info"
    ),
)

console = Console(stderr=True)

# ── Sub-app registrations ─────────────────────────────────────────────────────

app.add_typer(memory_app, name="memory", rich_help_panel="Data")
app.add_typer(debugger_app, name="debugger", rich_help_panel="Data")
app.add_typer(debugger_app, name="dbg", hidden=True)
app.add_typer(registry_app, name="registry", rich_help_panel="Agents")
app.add_typer(workflows_app, name="workflows", rich_help_panel="Project")
app.add_typer(workflows_app, name="wf", hidden=True)
app.add_typer(config_app, name="config", rich_help_panel="Configuration")
app.add_typer(completion_app, name="completion", rich_help_panel="Utilities")
app.add_typer(self_app, name="self", rich_help_panel="Info")
app.add_typer(plugins_app, name="plugins", rich_help_panel="Utilities")
app.add_typer(missions_app, name="missions", rich_help_panel="Agent OS")
app.add_typer(tasks_app, name="tasks", rich_help_panel="Agent OS")
app.add_typer(evidence_app, name="evidence", rich_help_panel="Agent OS")
app.add_typer(intake_app, name="intake", rich_help_panel="Agent OS")
app.add_typer(cockpit_app, name="cockpit", rich_help_panel="Agent OS")
app.add_typer(standard_app, name="standard", rich_help_panel="Project")
app.add_typer(ext_app, name="ext", rich_help_panel="Project")

# ── Registered top-level commands from extracted modules ──────────────────────

app.command("history", rich_help_panel="Info")(history_cmd)
app.command("repair", rich_help_panel="Utilities")(repair)


# ── grimoire callback ─────────────────────────────────────────────────────────

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
    output: str | None = typer.Option(None, "--output", "-o", help="Output format: text or json."),
    show_time: bool = typer.Option(False, "--time", help="Show elapsed time after command execution."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompts."),
    profile: bool = typer.Option(False, "--profile", help="Show per-phase timing breakdown."),
    debug: bool = typer.Option(False, "--debug", "-D", help="Enable debug mode (full tracebacks on error)."),
) -> None:
    """Grimoire Kit — Composable AI agent platform."""
    ctx.ensure_object(dict)

    if output is None:
        env_fmt = os.environ.get("GRIMOIRE_OUTPUT", "").lower()
        output = env_fmt if env_fmt in ("text", "json") else "text"
    if not quiet and os.environ.get("GRIMOIRE_QUIET", "").lower() in ("1", "true"):
        quiet = True
    if not no_color and os.environ.get("NO_COLOR", ""):
        no_color = True
    if not debug and os.environ.get("GRIMOIRE_DEBUG", "").lower() in ("1", "true"):
        debug = True

    ctx.obj["output"] = output
    ctx.obj["quiet"] = quiet
    ctx.obj["show_time"] = show_time
    ctx.obj["profile"] = profile
    ctx.obj["debug"] = debug
    ctx.obj["yes"] = yes or output == "json"
    ctx.obj["_timings"] = []
    if show_time or profile:
        ctx.obj["_start_time"] = time.perf_counter()
    if no_color:
        console.no_color = True
        console._force_terminal = False
    if debug:
        os.environ["GRIMOIRE_DEBUG"] = "1"
    if verbose:
        level = ("INFO" if verbose == 1 else "DEBUG")
        configure_logging(level, fmt=log_format)


# ── grimoire version ──────────────────────────────────────────────────────────

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


# ── grimoire init ─────────────────────────────────────────────────────────────

_KNOWN_ARCHETYPES = frozenset({
    "minimal", "web-app", "creative-studio", "fix-loop",
    "infra-ops", "meta", "stack", "features", "platform-engineering", "agentic-standard",
    "game-dev",
})

_KNOWN_BACKENDS = frozenset({"auto", "local", "qdrant-local", "qdrant-server", "weaviate-server", "mempalace", "ollama"})
_REDIS_SHORT_TERM_ARCHETYPES = frozenset({"infra-ops", "platform-engineering"})

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
  layer_profile: "standard"
  short_term_backend: "{short_term_backend}"
  redis_url: "{redis_url}"
  knowledge_graph: "sqlite-sidecar"
  memory_graph: "sqlite-sidecar"
  code_graph: "planned"
  task_memory: "planned"
  visualization: "runtime-dashboard"

agents:
  archetype: "{archetype}"
  custom_agents: []

installed_archetypes: []
"""


def _default_short_term_memory(archetype_name: str) -> tuple[str, str]:
    archetypes = {a.strip() for a in archetype_name.split(",") if a.strip()}
    if archetypes & _REDIS_SHORT_TERM_ARCHETYPES:
        return "redis", "redis://localhost:6379/0"
    return "sqlite", ""

_init_path_arg = typer.Argument(Path(), help="Project directory to initialise.")


@app.command(rich_help_panel="Project")
def init(
    ctx: typer.Context,
    path: Path = _init_path_arg,
    name: str = typer.Option("", help="Project name (default: directory name)."),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing config."),
    archetype: str = typer.Option("", "--archetype", "-a", help="Agent archetype(s), comma-separated (auto-detected if omitted)."),
    backend: str = typer.Option("auto", "--backend", "-b", help="Memory backend (auto, local, qdrant-local, qdrant-server, weaviate-server, mempalace, ollama)."),
    qdrant_docker: bool = typer.Option(False, "--qdrant-docker", help="Configure qdrant-server and start the generated Docker Compose service."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show plan without writing."),
    rtk: bool | None = typer.Option(None, "--rtk/--no-rtk", help="Installer RTK (Rust Token Killer) + activer le hook Claude. Auto en interactif."),
) -> None:
    """Initialise a Grimoire project — detect stack, deploy agents, scaffold.

    Without flags, launches an interactive wizard. Use [cyan]--yes[/cyan] for
    express mode (auto-detect everything, no questions asked).

    [dim]Examples:[/dim]
      [cyan]grimoire init .[/cyan]                               Interactive wizard
      [cyan]grimoire init . -y[/cyan]                            Express (auto-detect all)
      [cyan]grimoire init . -a infra-ops -b weaviate-server[/cyan]  Explicit archetype & backend
      [cyan]grimoire init . --qdrant-docker[/cyan]               Configure and start Qdrant Docker
      [cyan]grimoire init . -a web-app,infra-ops[/cyan]         Multiple archetypes
      [cyan]grimoire init --dry-run[/cyan]                       Show plan without writing
    """
    if archetype:
        parts = [a.strip() for a in archetype.split(",") if a.strip()]
        invalid = [a for a in parts if a not in _KNOWN_ARCHETYPES]
        if invalid:
            for a in invalid:
                console.print(f"[red]Unknown archetype:[/red] {a}")
                matches = difflib.get_close_matches(a, sorted(_KNOWN_ARCHETYPES), n=2, cutoff=0.5)
                if matches:
                    console.print(f"Did you mean: [cyan]{', '.join(matches)}[/cyan]?")
            console.print(f"Available: {', '.join(sorted(_KNOWN_ARCHETYPES))}")
            raise typer.Exit(1)

    if backend not in _KNOWN_BACKENDS:
        console.print(f"[red]Unknown backend:[/red] {backend}")
        matches = difflib.get_close_matches(backend, sorted(_KNOWN_BACKENDS), n=2, cutoff=0.5)
        if matches:
            console.print(f"Did you mean: [cyan]{', '.join(matches)}[/cyan]?")
        else:
            console.print(f"Available: {', '.join(sorted(_KNOWN_BACKENDS))}")
        raise typer.Exit(1)

    from grimoire.cli.cmd_init import run_init

    run_init(
        ctx,
        path,
        name=name,
        archetype=archetype,
        backend=backend,
        force=force,
        dry_run=dry_run,
        qdrant_docker=qdrant_docker,
        rtk=rtk,
    )


# ── grimoire doctor ───────────────────────────────────────────────────────────

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
    layout = detect_project_layout(target)
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

    cfg: GrimoireConfig | None = None
    with _timed_phase("config_parse"):
        try:
            cfg = GrimoireConfig.from_yaml(config_path)
            _record("config_valid", passed=True, detail=f"Config valid — project: {cfg.project.name}")
        except GrimoireConfigError as exc:
            _record("config_valid", passed=False, detail=f"Config parse error: {exc}")

    fixed: list[str] = []
    with _timed_phase("structure_check"):
        for d in layout.required_dirs:
            dp = target / d
            present = dp.is_dir()
            if not present and fix:
                dp.mkdir(parents=True, exist_ok=True)
                fixed.append(d)
                _record(f"dir_{d}", passed=True, detail=f"{d}/ created (--fix)")
            else:
                _record(f"dir_{d}", passed=present, detail=f"{d}/ {'present' if present else 'missing'}")

    if cfg and cfg.agents.archetype:
        _record("archetype", passed=True, detail=f"Archetype configured: {cfg.agents.archetype}")

    if cfg:
        warnings = cfg.validate()
        for w in warnings:
            _record("semantic", passed=False, detail=w)

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

    _record("python", passed=True, detail=f"Python {sys.version.split()[0]}")

    ok_count = sum(1 for r in results if r["passed"])
    fail_count = sum(1 for r in results if not r["passed"])

    if fmt == "json":
        data: dict[str, Any] = {
            "project": str(target),
            "layout": layout.name,
            "checks": results,
            "passed": ok_count,
            "failed": fail_count,
        }
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


# ── grimoire status ───────────────────────────────────────────────────────────

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
        raise typer.Exit(_EXIT_CONFIG) from None

    layout = detect_project_layout(target)
    dirs = [*layout.required_dirs]
    dir_status = {d: (target / d).is_dir() for d in dirs}

    if _get_fmt(ctx) == "json":
        data = {
            "project": {"name": cfg.project.name, "type": cfg.project.type, "stack": list(cfg.project.stack)},
            "user": {"name": cfg.user.name, "language": cfg.user.language, "skill_level": cfg.user.skill_level},
            "agents": {"archetype": cfg.agents.archetype, "custom_agents": list(cfg.agents.custom_agents)},
            "memory": {"backend": cfg.memory.backend},
            "layout": layout.name,
            "structure": dir_status,
            "version": __version__,
        }
        typer.echo(json.dumps(data, indent=2))
        return

    console.print(f"\n[bold]Grimoire Project:[/bold] {cfg.project.name}")
    console.print(f"grimoire-kit {__version__}\n")

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
    tbl.add_row("Layout", layout.name)
    console.print(tbl)

    console.print("\n[bold]Agents[/bold]")
    console.print(f"  Archetype: {cfg.agents.archetype}")
    if cfg.agents.custom_agents:
        console.print(f"  Custom: {', '.join(cfg.agents.custom_agents)}")

    console.print("\n[bold]Memory[/bold]")
    console.print(f"  Backend: {cfg.memory.backend}")

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


# ── grimoire add / remove ─────────────────────────────────────────────────────

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


# ── grimoire validate ─────────────────────────────────────────────────────────

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


# ── grimoire lint ─────────────────────────────────────────────────────────────

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


# ── grimoire update ───────────────────────────────────────────────────────────

@app.command("update", rich_help_panel="Info")
def update_cmd() -> None:
    """Update grimoire-kit to the latest version (alias for 'self update')."""
    self_update()


# ── grimoire up ───────────────────────────────────────────────────────────────

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
    status_obj = project.status()
    layout = project.layout
    fmt = _get_fmt(ctx)

    if fmt != "json":
        if dry_run:
            console.print("[bold]grimoire up --dry-run[/bold]\n")
        else:
            console.print("[bold]grimoire up[/bold]\n")

    actions: list[str] = []

    for d in layout.required_dirs:
        dp = target / d
        if not dp.is_dir():
            actions.append(f"Create directory: {d}/")
            if not dry_run:
                dp.mkdir(parents=True, exist_ok=True)

    if layout.name == "legacy":
        agents_dir = target / layout.grimoire_dir / "agents"
        if not agents_dir.is_dir():
            actions.append(f"Create directory: {layout.grimoire_dir}/agents/")
            if not dry_run:
                agents_dir.mkdir(parents=True, exist_ok=True)

    if fmt != "json":
        if actions:
            for a in actions:
                icon = "[cyan]plan[/cyan]" if dry_run else "[green]done[/green]"
                console.print(f"  {icon}  {a}")
        else:
            console.print("  [green]Everything up to date.[/green]")

    if fmt == "json":
        typer.echo(json.dumps({
            "ok": True, "project": cfg.project.name,
            "layout": layout.name,
            "actions": actions, "dry_run": dry_run,
            "agents_count": status_obj.agents_count,
            "directories_missing": list(status_obj.directories_missing),
        }, indent=2))
    else:
        console.print(f"\n[bold]Project:[/bold] {cfg.project.name}")
        console.print(f"  Layout: {layout.name}")
        console.print(f"  Archetype: {cfg.agents.archetype}")
        console.print(f"  Memory: {cfg.memory.backend}")
        console.print(f"  Agents: {status_obj.agents_count}")

        if status_obj.directories_missing:
            missing = ", ".join(status_obj.directories_missing)
            console.print(f"\n[yellow]Missing dirs (after up):[/yellow] {missing}")


# ── grimoire diff ─────────────────────────────────────────────────────────────

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
    short_term_backend, redis_url = _default_short_term_memory(str(archetype_name))
    defaults = yaml.load(_TEMPLATE_YAML.format(
        name="__DEFAULT__",
        archetype=archetype_name,
        backend="auto",
        short_term_backend=short_term_backend,
        redis_url=redis_url,
    ))

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


# ── grimoire schema ───────────────────────────────────────────────────────────

@app.command("schema", rich_help_panel="Validation")
def schema_cmd() -> None:
    """Export JSON Schema for ``project-context.yaml``."""
    from grimoire.core.schema import generate_schema

    typer.echo(json.dumps(generate_schema(), indent=2))


# ── grimoire check ────────────────────────────────────────────────────────────

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

    _phase_header("3/3 Structure")
    with _timed_phase("check/structure"):
        layout = detect_project_layout(target)
        missing = [d for d in layout.required_dirs if not (target / d).is_dir()]
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


# ── grimoire upgrade ──────────────────────────────────────────────────────────

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


# ── grimoire merge ────────────────────────────────────────────────────────────

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
            plan, result = run_merge(resolved_source, resolved_target, dry_run=dry_run, force=force)
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


# ── grimoire setup ────────────────────────────────────────────────────────────

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
    """Sync user config (name, language, skill) across all runtime config files."""
    from grimoire.cli.cmd_setup import SetupResult, apply, check, load_user_values

    use_json = json_out or _get_fmt(ctx) == "json"
    target = path.resolve()
    pcy = target / "project-context.yaml"

    if not pcy.is_file():
        console.print("[red]project-context.yaml not found[/red] — run [bold]grimoire init[/bold] first.")
        raise typer.Exit(1)

    current = load_user_values(pcy)

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

    conflicts: list[str] = []
    if env_vars.get("GRIMOIRE_DEBUG") and env_vars.get("GRIMOIRE_QUIET"):
        conflicts.append("GRIMOIRE_DEBUG and GRIMOIRE_QUIET are both set — debug output may be suppressed")

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
            "conflicts": conflicts,
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

    if conflicts:
        console.print("\n[bold yellow]Conflicts[/bold yellow]")
        for c in conflicts:
            console.print(f"  [yellow]⚠[/yellow] {c}")

    if project_info and project_info["name"] != "(unreadable)":
        console.print("\n[bold]Project[/bold]")
        console.print(f"  Root  {project_info['root']}")
        console.print(f"  Name  {project_info['name']}")
    elif project_info:
        console.print("\n[yellow]project-context.yaml found but unreadable[/yellow]")
    else:
        console.print("\n[dim]No project-context.yaml in current directory.[/dim]")

    console.print()


# ── Deprecation warnings ──────────────────────────────────────────────────────

_DEPRECATED_FLAGS: dict[str, tuple[str, str]] = {}


def _warn_deprecated() -> None:
    """Emit warnings for deprecated flags present in sys.argv."""
    for old_flag, (new_flag, version) in _DEPRECATED_FLAGS.items():
        if old_flag in sys.argv:
            console.print(
                f"[yellow]⚠ '{old_flag}' is deprecated and will be removed in v{version}. "
                f"Use '{new_flag}' instead.[/yellow]"
            )


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
    _phase_timings.clear()
    _expand_aliases()
    _warn_deprecated()
    _suggest_command()
    _start = time.perf_counter()
    _show_time = "--time" in sys.argv
    _show_profile = "--profile" in sys.argv
    _debug = "--debug" in sys.argv or "-D" in sys.argv or os.environ.get("GRIMOIRE_DEBUG", "").lower() in ("1", "true")
    try:
        app()
    except GrimoireError as exc:
        _format_error(exc)
        raise typer.Exit(1) from None
    except Exception as exc:
        if _debug:
            from rich.traceback import Traceback

            console.print(Traceback.from_exception(type(exc), exc, exc.__traceback__, width=120, show_locals=True))
            raise typer.Exit(2) from None
        console.print(f"[bold red]Unexpected error:[/bold red] {exc}")
        console.print("[dim]Use --debug or set GRIMOIRE_DEBUG=1 for full traceback.[/dim]")
        raise typer.Exit(2) from None
    finally:
        total = time.perf_counter() - _start
        if _show_profile and _phase_timings:
            _display_profile(total)
        elif _show_time:
            console.print(f"\n[dim]⏱  {total * 1000:.0f}ms[/dim]")
