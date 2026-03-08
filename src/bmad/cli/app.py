"""BMAD CLI entry point — ``bmad [command]``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from bmad.__version__ import __version__
from bmad.core.config import BmadConfig
from bmad.core.exceptions import BmadConfigError, BmadProjectError

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


# ── bmad validate ─────────────────────────────────────────────────────────────

_validate_path_arg = typer.Argument(Path("."), help="Project root to validate.")


@app.command("validate")
def validate(
    path: Path = _validate_path_arg,
) -> None:
    """Validate project-context.yaml against the BMAD schema."""
    from bmad.core.validator import validate_config
    from bmad.tools._common import load_yaml

    target = path.resolve()
    config_path = target / "project-context.yaml"

    if not config_path.is_file():
        console.print("[red]No project-context.yaml found.[/red]")
        raise typer.Exit(1)

    data = load_yaml(config_path)
    errors = validate_config(data, project_root=target)

    if not errors:
        console.print("[green]project-context.yaml is valid.[/green]")
        raise typer.Exit(0)

    console.print(f"[red]Found {len(errors)} validation error(s):[/red]\n")
    for err in errors:
        console.print(f"  [red]•[/red] {err}")
    raise typer.Exit(1)


# ── bmad up ───────────────────────────────────────────────────────────────────

_up_path_arg = typer.Argument(Path("."), help="Project root.")
_up_dry_run_opt = typer.Option(False, "--dry-run", help="Show plan without applying.")


@app.command("up")
def up(
    path: Path = _up_path_arg,
    dry_run: bool = _up_dry_run_opt,
) -> None:
    """Reconcile the project state with project-context.yaml."""
    from bmad.core.project import BmadProject

    target = path.resolve()
    try:
        project = BmadProject(target)
    except BmadProjectError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None

    cfg = project.config
    status = project.status()

    if dry_run:
        console.print("[bold]bmad up --dry-run[/bold]\n")
    else:
        console.print("[bold]bmad up[/bold]\n")

    actions: list[str] = []

    # Ensure standard directories exist
    for d in ("_bmad", "_bmad/_memory", "_bmad-output"):
        dp = target / d
        if not dp.is_dir():
            actions.append(f"Create directory: {d}/")
            if not dry_run:
                dp.mkdir(parents=True, exist_ok=True)

    # Ensure agents dir exists
    agents_dir = target / "_bmad" / "agents"
    if not agents_dir.is_dir():
        actions.append("Create directory: _bmad/agents/")
        if not dry_run:
            agents_dir.mkdir(parents=True, exist_ok=True)

    # Summary
    if actions:
        for a in actions:
            icon = "[cyan]plan[/cyan]" if dry_run else "[green]done[/green]"
            console.print(f"  {icon}  {a}")
    else:
        console.print("  [green]Everything up to date.[/green]")

    # Health summary
    console.print(f"\n[bold]Project:[/bold] {cfg.project.name}")
    console.print(f"  Archetype: {cfg.agents.archetype}")
    console.print(f"  Memory: {cfg.memory.backend}")
    console.print(f"  Agents: {status.agents_count}")

    if status.directories_missing:
        missing = ", ".join(status.directories_missing)
        console.print(f"\n[yellow]Missing dirs (after up):[/yellow] {missing}")


# ── bmad registry ─────────────────────────────────────────────────────────────

registry_app = typer.Typer(help="Browse the agent registry.")
app.add_typer(registry_app, name="registry")

_reg_query_arg = typer.Argument(None, help="Search query.")


@registry_app.command("list")
def registry_list() -> None:
    """List all available archetypes and agents."""
    from bmad.registry.local import LocalRegistry
    from bmad.tools._common import find_project_root

    try:
        root = find_project_root()
    except FileNotFoundError:
        console.print("[red]Not in a BMAD project — cannot locate kit root.[/red]")
        raise typer.Exit(1) from None

    reg = LocalRegistry(root)
    archs = reg.list_archetypes()
    if not archs:
        console.print("[yellow]No archetypes found.[/yellow]")
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
    query: str = _reg_query_arg,  # type: ignore[assignment]
) -> None:
    """Search agents by keyword."""
    from bmad.registry.local import LocalRegistry
    from bmad.tools._common import find_project_root

    if not query:
        console.print("[red]Please provide a search query.[/red]")
        raise typer.Exit(1)

    try:
        root = find_project_root()
    except FileNotFoundError:
        console.print("[red]Not in a BMAD project.[/red]")
        raise typer.Exit(1) from None

    reg = LocalRegistry(root)
    results = reg.search(query)

    if not results:
        console.print(f"[yellow]No agents matching '{query}'.[/yellow]")
        return

    tbl = Table(title=f"Search: {query}")
    tbl.add_column("Agent", style="bold")
    tbl.add_column("Archetype")
    tbl.add_column("Description")

    for item in results:
        tbl.add_row(item.id, item.archetype, item.description or "—")

    console.print(tbl)


# ── bmad upgrade ──────────────────────────────────────────────────────────────

_upgrade_path_arg = typer.Argument(Path("."), help="Path to the v2 project.")
_upgrade_dry_run_opt = typer.Option(False, "--dry-run", "-n", help="Show plan without applying.")


@app.command("upgrade")
def upgrade(
    path: Path = _upgrade_path_arg,
    dry_run: bool = _upgrade_dry_run_opt,
) -> None:
    """Migrate a v2 project to v3 structure."""
    from bmad.cli.cmd_upgrade import (
        detect_version,
        execute_upgrade,
        plan_upgrade,
    )

    target = path.resolve()
    version = detect_version(target)

    if version == "v3":
        console.print("[green]Project is already v3 — nothing to do.[/green]")
        return

    if version == "unknown":
        console.print("[red]No v2 project-context.yaml found at this path.[/red]")
        raise typer.Exit(1)

    plan = plan_upgrade(target)

    if dry_run:
        console.print("[bold]bmad upgrade --dry-run[/bold]\n")
    else:
        console.print("[bold]bmad upgrade[/bold]\n")

    if plan.warnings:
        for w in plan.warnings:
            console.print(f"  [yellow]⚠ {w}[/yellow]")

    completed = execute_upgrade(target, plan, dry_run=dry_run)
    for desc in completed:
        icon = "[cyan]plan[/cyan]" if dry_run else "[green]done[/green]"
        console.print(f"  {icon}  {desc}")

    if not completed and not plan.warnings:
        console.print("  [green]Nothing to do.[/green]")

    console.print(f"\n[bold]Migration {'planned' if dry_run else 'complete'}.[/bold]")


# ── bmad merge ────────────────────────────────────────────────────────────────

_merge_from_arg = typer.Argument(..., help="Source directory to merge from.")
_merge_target_opt = typer.Option(Path("."), "--target", "-t", help="Target project directory.")
_merge_dry_run_opt = typer.Option(False, "--dry-run", "-n", help="Show plan without merging.")
_merge_force_opt = typer.Option(False, "--force", "-f", help="Overwrite conflicting files.")


@app.command("merge")
def merge(
    source: Path = _merge_from_arg,
    target: Path = _merge_target_opt,
    dry_run: bool = _merge_dry_run_opt,
    force: bool = _merge_force_opt,
    undo: bool = typer.Option(False, "--undo", help="Undo the last merge in the target."),
) -> None:
    """Merge BMAD files from a source into a project."""
    from bmad.cli.cmd_merge import run_merge, run_undo
    from bmad.core.exceptions import BmadMergeError

    resolved_target = target.resolve()

    if undo:
        try:
            deleted = run_undo(resolved_target)
        except BmadMergeError as exc:
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
        plan, result = run_merge(
            resolved_source, resolved_target, dry_run=dry_run, force=force,
        )
    except BmadMergeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None

    label = "bmad merge --dry-run" if dry_run else "bmad merge"
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
    console.print(f"\n[bold]{total} file(s) processed.[/bold]")

