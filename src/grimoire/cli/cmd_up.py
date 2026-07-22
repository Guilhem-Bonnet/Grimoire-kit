"""``grimoire up`` — one-command project bring-up.

Chains the full onboarding journey behind a single, idempotent command:

    init (express) -> identity propagation -> standard init -> doctor summary

Also hosts the fast environment checks (uv, docker, qdrant, ollama, .mcp.json,
venv) shared with ``grimoire doctor``, plus the artifact repair helper used by
``grimoire doctor --fix`` (agent wrappers + .mcp.json regeneration).

Every probe is designed to be fast and non-blocking: socket connects use a
0.5 s timeout, subprocesses a 2 s timeout, and a missing tool never crashes.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import typer
from rich.console import Console
from rich.table import Table

from grimoire.cli import cmd_setup
from grimoire.cli.cmd_init import KNOWN_ARCHETYPES, KNOWN_BACKENDS, run_init
from grimoire.core.config import GrimoireConfig
from grimoire.core.exceptions import GrimoireConfigError, GrimoireError

console = Console(stderr=True)

# Fast-probe budgets — never blocking.
_SOCKET_TIMEOUT = 0.5
_SUBPROCESS_TIMEOUT = 2.0

_QDRANT_DEFAULT_URL = "http://localhost:6333"
_OLLAMA_DEFAULT_URL = "http://localhost:11434"
_QDRANT_COMPOSE_FILE = "docker-compose.memory.yml"
_QDRANT_DOCKER_RUN = (
    "docker run -d --name qdrant -p 6333:6333 -v qdrant_storage:/qdrant/storage qdrant/qdrant"
)

# Directories reconciled by ``up`` (mirrors app.py doctor requirements).
_RECONCILE_DIRS = ("_grimoire", "_grimoire-output", "_grimoire/_memory")

_STANDARD_PROFILE_MARKER = Path("_grimoire/standard/standard-profile.yaml")


# ── Environment checks (shared with grimoire doctor) ─────────────────────────


@dataclass
class EnvCheck:
    """Outcome of one environment probe."""

    name: str
    passed: bool
    level: str  # "ok" | "info" | "warn" | "fail"
    detail: str
    remedy: str = ""
    optional: bool = True


def _tcp_reachable(url: str, default_port: int, *, timeout: float = _SOCKET_TIMEOUT) -> bool:
    """True when a TCP connect to *url* succeeds within *timeout* seconds."""
    raw = url if "//" in url else f"//{url}"
    try:
        parsed = urlparse(raw)
        host = parsed.hostname or "localhost"
        port = parsed.port or default_port
    except ValueError:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def check_uv() -> EnvCheck:
    """Optional: is the ``uv`` package manager on PATH?"""
    path = shutil.which("uv")
    if path:
        return EnvCheck("env_uv", passed=True, level="ok", detail=f"uv available ({path})")
    return EnvCheck(
        "env_uv",
        passed=True,
        level="warn",
        detail="uv not found on PATH (optional, speeds up Python installs)",
        remedy="curl -LsSf https://astral.sh/uv/install.sh | sh",
    )


def check_docker() -> EnvCheck:
    """Optional: is docker installed and its daemon reachable?"""
    if shutil.which("docker") is None:
        return EnvCheck(
            "env_docker",
            passed=True,
            level="warn",
            detail="docker not found on PATH (optional, needed for Qdrant/Weaviate services)",
            remedy="install Docker: https://docs.docker.com/get-docker/",
        )
    try:
        proc = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        proc = None
    if proc is not None and proc.returncode == 0:
        version = proc.stdout.strip() or "unknown"
        return EnvCheck("env_docker", passed=True, level="ok", detail=f"docker daemon reachable (server {version})")
    return EnvCheck(
        "env_docker",
        passed=True,
        level="warn",
        detail="docker CLI present but the daemon is not reachable",
        remedy="start the daemon: sudo systemctl start docker",
    )


def check_qdrant(target: Path | None = None) -> EnvCheck:
    """Optional: is Qdrant reachable (GRIMOIRE_QDRANT_URL or localhost:6333)?"""
    url = os.environ.get("GRIMOIRE_QDRANT_URL", "").strip() or _QDRANT_DEFAULT_URL
    if _tcp_reachable(url, 6333):
        return EnvCheck("env_qdrant", passed=True, level="ok", detail=f"Qdrant reachable at {url}")
    remedy = _QDRANT_DOCKER_RUN
    if target is not None and (target / _QDRANT_COMPOSE_FILE).is_file():
        remedy = f"docker compose -f {_QDRANT_COMPOSE_FILE} up -d"
    return EnvCheck(
        "env_qdrant",
        passed=True,
        level="warn",
        detail=f"Qdrant not reachable at {url} (optional, semantic memory)",
        remedy=remedy,
    )


def check_ollama() -> EnvCheck:
    """Optional: is Ollama reachable (GRIMOIRE_OLLAMA_URL or localhost:11434)?"""
    url = os.environ.get("GRIMOIRE_OLLAMA_URL", "").strip() or _OLLAMA_DEFAULT_URL
    if _tcp_reachable(url, 11434):
        return EnvCheck("env_ollama", passed=True, level="ok", detail=f"Ollama reachable at {url}")
    return EnvCheck(
        "env_ollama",
        passed=True,
        level="warn",
        detail=f"Ollama not reachable at {url} (optional, local embeddings)",
        remedy="ollama serve (install: https://ollama.com/download)",
    )


def check_venv() -> EnvCheck:
    """Info: is the interpreter running inside a virtualenv?"""
    in_venv = sys.prefix != sys.base_prefix
    if in_venv:
        return EnvCheck("env_venv", passed=True, level="ok", detail=f"virtualenv active ({sys.prefix})")
    return EnvCheck(
        "env_venv",
        passed=True,
        level="info",
        detail="not running inside a virtualenv (sys.prefix == sys.base_prefix)",
    )


def _looks_like_path(value: str) -> bool:
    """Heuristic: does an .mcp.json argument look like a filesystem path?"""
    if not value or value.startswith("-"):
        return False
    return value.startswith(("/", "./", "../", "~")) or (os.sep in value and Path(value).suffix != "")


def _resolve_ref(target: Path, value: str) -> bool:
    """True when *value* resolves to an existing file/dir (absolute or project-relative)."""
    p = Path(value).expanduser()
    if p.is_absolute():
        return p.exists()
    return (target / p).exists()


def check_mcp_json(target: Path) -> list[EnvCheck]:
    """Verify that commands/paths referenced by the project ``.mcp.json`` exist.

    Missing ``.mcp.json`` yields no check (``doctor --fix`` can create it);
    a present file with broken references yields a hard FAIL.
    """
    mcp_path = target / ".mcp.json"
    if not mcp_path.is_file():
        return []
    try:
        data = json.loads(mcp_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [EnvCheck(
            "mcp_json",
            passed=False,
            level="fail",
            detail=f".mcp.json is not valid JSON: {exc}",
            remedy="fix the syntax or regenerate it: grimoire doctor . --fix (after removing the broken file)",
            optional=False,
        )]

    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or not servers:
        return [EnvCheck(
            "mcp_json",
            passed=True,
            level="info",
            detail=".mcp.json present but declares no mcpServers",
        )]

    checks: list[EnvCheck] = []
    for server_name, spec in servers.items():
        if not isinstance(spec, dict):
            checks.append(EnvCheck(
                f"mcp_{server_name}",
                passed=False,
                level="fail",
                detail=f".mcp.json server '{server_name}' entry is not an object",
                remedy="fix .mcp.json or regenerate it: grimoire doctor . --fix (after removing the broken entry)",
                optional=False,
            ))
            continue
        broken: list[str] = []
        command = str(spec.get("command", "")).strip()
        if command:
            command_ok = shutil.which(command) is not None or _resolve_ref(target, command)
            if not command_ok:
                broken.append(f"command '{command}' not found on PATH")
        args = spec.get("args", [])
        if isinstance(args, list):
            for arg in args:
                arg_str = str(arg)
                if _looks_like_path(arg_str) and not _resolve_ref(target, arg_str):
                    broken.append(f"path '{arg_str}' does not exist")
        if broken:
            checks.append(EnvCheck(
                f"mcp_{server_name}",
                passed=False,
                level="fail",
                detail=f".mcp.json server '{server_name}': {'; '.join(broken)}",
                remedy=(
                    f"install the missing tool (e.g. pip install grimoire-kit for grimoire-mcp) "
                    f"or fix the '{server_name}' entry in .mcp.json"
                ),
                optional=False,
            ))
        else:
            checks.append(EnvCheck(
                f"mcp_{server_name}",
                passed=True,
                level="ok",
                detail=f".mcp.json server '{server_name}' resolves ({command or 'no command'})",
            ))
    return checks


def run_env_checks(target: Path) -> list[EnvCheck]:
    """Run every fast environment probe for *target*. Never raises."""
    return [
        check_venv(),
        check_uv(),
        check_docker(),
        check_qdrant(target),
        check_ollama(),
        *check_mcp_json(target),
    ]


# ── Artifact repair (used by grimoire doctor --fix) ──────────────────────────


def repair_project_artifacts(target: Path) -> list[str]:
    """Regenerate missing VS Code agent wrappers and ``.mcp.json``.

    Reuses :class:`ProjectScaffolder` planning logic so the regenerated files
    are byte-identical to what ``grimoire init`` produces. Idempotent: existing
    wrappers and an existing ``.mcp.json`` are never overwritten.

    Returns the list of regenerated labels (relative paths).
    """
    from grimoire.core.archetype_resolver import ResolvedArchetype
    from grimoire.core.scaffold import FileCopy, ProjectScaffolder, ScaffoldPlan

    target = target.resolve()
    cfg = _load_config_quiet(target)
    resolved = ResolvedArchetype(
        archetype=(cfg.agents.archetype if cfg else "") or "minimal",
        stack_agents=(),
        feature_agents=(),
        reason="doctor --fix",
        archetypes=(),
    )
    scaffolder = ProjectScaffolder(
        target,
        project_name=(cfg.project.name if cfg else "") or target.name,
        user_name=(cfg.user.name if cfg else "") or "Developer",
        language=(cfg.user.language if cfg else "") or "Français",
        skill_level=(cfg.user.skill_level if cfg else "") or "intermediate",
        scan=None,
        resolved=resolved,
        backend=(cfg.memory.backend if cfg else "") or "local",
    )

    plan = ScaffoldPlan()
    agents_dir = target / "_grimoire" / "_config" / "custom" / "agents"
    if agents_dir.is_dir():
        for agent_file in sorted(agents_dir.glob("*.md")):
            if agent_file.name.endswith(".tpl.md"):
                continue
            # Feed deployed agents to the wrapper planner as pseudo-copies.
            plan.copies.append(FileCopy(src=agent_file, dst=agent_file, label=agent_file.stem))
    # Intentional reuse of ProjectScaffolder's planning internals so the
    # regenerated artifacts stay identical to what `grimoire init` produces.
    scaffolder._plan_agent_wrappers(plan)
    scaffolder._plan_mcp_config(plan)

    written: list[str] = []
    for template in plan.templates:
        template.dst.parent.mkdir(parents=True, exist_ok=True)
        template.dst.write_text(template.content, encoding="utf-8")
        written.append(template.label or str(template.dst.relative_to(target)))
    return written


def _load_config_quiet(target: Path) -> GrimoireConfig | None:
    """Parse project-context.yaml, returning None on any error."""
    config_path = target / "project-context.yaml"
    if not config_path.is_file():
        return None
    try:
        return GrimoireConfig.from_yaml(config_path)
    except (GrimoireConfigError, GrimoireError, OSError):
        return None


# ── grimoire up ───────────────────────────────────────────────────────────────


@dataclass
class StepResult:
    """Outcome of one ``up`` step."""

    step: str
    status: str  # "done" | "skipped" | "failed" | "planned"
    detail: str = ""


@dataclass
class _UpState:
    """Mutable context threaded through the up pipeline."""

    steps: list[StepResult] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return any(s.status == "failed" for s in self.steps)


def _set_config_user_name(config_path: Path, user_name: str) -> bool:
    """Update ``user.name`` in project-context.yaml (round-trip safe)."""
    from ruamel.yaml import YAML

    yaml = YAML()
    yaml.preserve_quotes = True
    with open(config_path, encoding="utf-8") as fh:
        data = yaml.load(fh)
    if not isinstance(data, dict):
        return False
    user = data.get("user") or {}
    if str(user.get("name", "")) == user_name:
        return False
    user["name"] = user_name
    data["user"] = user
    with open(config_path, "w", encoding="utf-8") as fh:
        yaml.dump(data, fh)
    return True


def _split_csv(values: list[str] | None) -> list[str]:
    collected: list[str] = []
    for value in values or []:
        for part in str(value).split(","):
            cleaned = part.strip()
            if cleaned and cleaned not in collected:
                collected.append(cleaned)
    return collected


def _step_init(
    ctx: typer.Context,
    state: _UpState,
    target: Path,
    *,
    name: str,
    archetypes: list[str],
    backend: str,
    interactive: bool,
    dry_run: bool,
) -> bool:
    """Run (or skip) express init. Returns True when a config is available after the step."""
    config_path = target / "project-context.yaml"
    if config_path.is_file():
        state.steps.append(StepResult("init", "skipped", "project already initialized (project-context.yaml present)"))
        return True
    if dry_run:
        state.steps.append(StepResult("init", "planned", "express init (no project-context.yaml found)"))
        return False

    ctx.obj = ctx.obj or {}
    prev_yes = ctx.obj.get("yes", False)
    prev_output = ctx.obj.get("output", "text")
    try:
        # Express by default; --interactive re-enables the init wizard (TTY only).
        ctx.obj["yes"] = prev_yes or not interactive
        # Keep stdout a single JSON document in -o json mode: init reports go
        # to stderr in text mode.
        ctx.obj["output"] = "text"
        run_init(
            ctx,
            target,
            name=name,
            archetype=",".join(archetypes),
            backend=backend,
            force=False,
            dry_run=False,
        )
    except typer.Abort:
        state.steps.append(StepResult("init", "failed", "init cancelled by user"))
        return config_path.is_file()
    except typer.Exit as exc:
        code = getattr(exc, "exit_code", 1)
        if code not in (0, None):
            state.steps.append(StepResult("init", "failed", f"init exited with code {code}"))
            return config_path.is_file()
    except GrimoireError as exc:
        state.steps.append(StepResult("init", "failed", f"init error: {exc}"))
        return config_path.is_file()
    finally:
        ctx.obj["yes"] = prev_yes
        ctx.obj["output"] = prev_output

    state.steps.append(StepResult("init", "done", "project initialized (express)"))
    state.actions.append("Initialized project (express init)")
    return True


def _step_structure(state: _UpState, target: Path, *, dry_run: bool) -> None:
    """Reconcile required directories (legacy ``up`` behavior, kept for compat)."""
    for rel in _RECONCILE_DIRS:
        dir_path = target / rel
        if dir_path.is_dir():
            continue
        state.actions.append(f"Create directory: {rel}/")
        if not dry_run:
            dir_path.mkdir(parents=True, exist_ok=True)


def _step_identity(state: _UpState, target: Path, *, user: str, dry_run: bool, blocked: bool) -> None:
    """Propagate identity values from project-context.yaml (cmd_setup logic)."""
    if blocked:
        state.steps.append(StepResult("identity", "skipped", "blocked: no project configuration"))
        return
    config_path = target / "project-context.yaml"
    if not config_path.is_file():
        state.steps.append(StepResult("identity", "skipped", "blocked: no project configuration"))
        return

    try:
        if user and not dry_run and _set_config_user_name(config_path, user):
            state.actions.append(f"Set user.name = {user}")
        vals = cmd_setup.load_user_values(config_path)
        if user:
            vals.user_name = user
        if dry_run:
            audit = cmd_setup.check(target, vals)
            if audit.is_synced:
                state.steps.append(StepResult("identity", "skipped", "identity already in sync"))
            else:
                state.steps.append(StepResult("identity", "planned", f"{len(audit.diffs)} value(s) to propagate"))
            return
        result = cmd_setup.apply(target, vals)
        if result.updated_files:
            state.steps.append(StepResult("identity", "done", f"updated {', '.join(result.updated_files)}"))
            state.actions.append(f"Propagated identity to {len(result.updated_files)} file(s)")
        elif result.skipped_files and not result.diffs:
            state.steps.append(StepResult("identity", "skipped", "no propagation target (.github/copilot-instructions.md absent)"))
        else:
            state.steps.append(StepResult("identity", "skipped", "identity already in sync"))
    except (OSError, GrimoireError) as exc:
        state.steps.append(StepResult("identity", "failed", f"identity propagation error: {exc}"))


def _print_needs_suggestions(target: Path) -> None:
    """B3 — suggérer des needs adaptés au projet détecté (informatif).

    Best-effort : la suggestion ne bloque jamais l'install (le défaut
    ``starter`` s'applique) ; elle montre comment obtenir une install sur
    mesure avec ``--needs``.
    """
    try:
        from grimoire.core.agentic_standard import load_needs_catalog
        from grimoire.core.needs_suggest import suggest_needs
        from grimoire.core.scanner import StackScanner

        scan = StackScanner(target).scan()
        suggestions = suggest_needs(scan, load_needs_catalog())
    except (OSError, ValueError, KeyError, ImportError):
        return
    if not suggestions:
        return
    console.print("[dim]Needs suggérés pour ce projet :[/dim]")
    for s in suggestions:
        console.print(f"  [cyan]--needs {s.need_id}[/cyan]  [dim]{s.reason}[/dim]")
    flags = " ".join(f"--needs {s.need_id}" for s in suggestions)
    console.print(
        f"[dim]Install sur mesure : [/dim][cyan]grimoire up . {flags}[/cyan]"
    )


def _step_standard(
    state: _UpState,
    target: Path,
    *,
    no_standard: bool,
    needs: list[str],
    project_name: str,
    dry_run: bool,
    blocked: bool,
) -> None:
    """Initialise the governed agentic standard (cmd_standard logic, defaults)."""
    if no_standard:
        state.steps.append(StepResult("standard", "skipped", "disabled by --no-standard"))
        return
    if blocked:
        state.steps.append(StepResult("standard", "skipped", "blocked: no project configuration"))
        return
    if (target / _STANDARD_PROFILE_MARKER).is_file():
        state.steps.append(StepResult("standard", "skipped", "standard profile already initialized"))
        return
    if dry_run:
        label = f"needs: {', '.join(needs)}" if needs else "profile: starter"
        state.steps.append(StepResult("standard", "planned", f"standard init ({label})"))
        return

    try:
        from grimoire.core.agentic_standard import resolve_install_plan, setup_standard_profile

        plan = None
        if needs:
            plan = resolve_install_plan(needs=needs)
            profile_id = plan.profile
            extra_artifacts = list(plan.extra_artifacts)
        else:
            profile_id = "starter"
            extra_artifacts = []
            _print_needs_suggestions(target)

        result = setup_standard_profile(
            target,
            profile_id=profile_id,
            task_id="bootstrap",
            project_name=project_name,
            extra_artifacts=extra_artifacts,
            force=False,
        )
        if plan is not None:
            from grimoire.cli.cmd_standard import _install_manifest_text

            manifest_path = target / "_grimoire" / "standard" / "install-manifest.yaml"
            if not manifest_path.exists():
                manifest_path.parent.mkdir(parents=True, exist_ok=True)
                manifest_path.write_text(
                    _install_manifest_text(plan, project_name, "bootstrap"), encoding="utf-8",
                )
        state.steps.append(StepResult(
            "standard", "done",
            f"profile '{result.profile}' — {len(result.written)} artifact(s) written",
        ))
        state.actions.append(f"Initialized agentic standard (profile {result.profile})")
    except (GrimoireError, ValueError, KeyError, OSError) as exc:
        state.steps.append(StepResult("standard", "failed", f"standard init error: {exc}"))


def _step_doctor_summary(state: _UpState, target: Path, *, dry_run: bool, blocked: bool) -> list[EnvCheck]:
    """Short doctor summary reusing the shared environment checks."""
    if dry_run or blocked:
        state.steps.append(StepResult("doctor", "skipped", "run `grimoire doctor` for the full report"))
        return []
    try:
        checks = run_env_checks(target)
    except Exception as exc:  # defensive: env probes must never break `up`
        state.steps.append(StepResult("doctor", "failed", f"environment checks error: {exc}"))
        return []
    failures = [c for c in checks if not c.passed]
    warnings = [c for c in checks if c.passed and c.level == "warn"]
    if failures:
        state.steps.append(StepResult(
            "doctor", "failed",
            f"{len(failures)} check(s) failing — {failures[0].detail}",
        ))
    else:
        state.steps.append(StepResult(
            "doctor", "done",
            f"{len(checks) - len(warnings)}/{len(checks)} env checks OK, {len(warnings)} warning(s)",
        ))
    return checks


_STATUS_STYLE = {
    "done": "[green]done[/green]",
    "skipped": "[yellow]skipped[/yellow]",
    "failed": "[red]failed[/red]",
    "planned": "[cyan]planned[/cyan]",
}


def _render_report(
    state: _UpState,
    checks: list[EnvCheck],
    *,
    project_name: str,
    dry_run: bool,
) -> None:
    """Text report: step table, project summary, next actions only on failure."""
    table = Table(title="grimoire up — summary", show_header=True, padding=(0, 2))
    table.add_column("Step", style="bold")
    table.add_column("Status")
    table.add_column("Detail")
    for step in state.steps:
        table.add_row(step.step, _STATUS_STYLE.get(step.status, step.status), step.detail)
    console.print(table)
    console.print(f"\n[bold]Project:[/bold] {project_name}")

    if dry_run:
        console.print("[dim]dry-run: nothing was written.[/dim]")

    failed_steps = [s for s in state.steps if s.status == "failed"]
    if failed_steps:
        console.print("\n[bold]Next actions:[/bold]")
        for step in failed_steps:
            console.print(f"  [red]FAIL[/red] {step.step}: {step.detail}")
        for chk in checks:
            if not chk.passed and chk.remedy:
                console.print(f"       remedy: [cyan]{chk.remedy}[/cyan]")
        console.print("  Run [cyan]grimoire doctor .[/cyan] for the full diagnosis.")


_up_path_arg = typer.Argument(Path(), help="Project root.")
_up_interactive_opt = typer.Option(False, "--interactive", "-i", help="Run the full init wizard instead of express mode.")
_up_name_opt = typer.Option("", "--name", help="Project name (default: directory name).")
_up_user_opt = typer.Option("", "--user", help="User name written to project-context.yaml and propagated.")
_up_archetype_opt = typer.Option(None, "--archetype", "-a", help="Archetype id (repeatable or comma-separated).")
_up_backend_opt = typer.Option("auto", "--backend", "-b", help="Memory backend (auto, local, lexical, qdrant-local, qdrant-server, weaviate-server, mempalace, ollama).")
_up_no_standard_opt = typer.Option(False, "--no-standard", help="Skip the agentic standard initialization.")
_up_needs_opt = typer.Option(None, "--needs", help="Need id(s) for standard init (repeatable or comma-separated).")
_up_dry_run_opt = typer.Option(False, "--dry-run", help="Show the plan without applying.")


def up(
    ctx: typer.Context,
    path: Path = _up_path_arg,
    interactive: bool = _up_interactive_opt,
    name: str = _up_name_opt,
    user: str = _up_user_opt,
    archetype: list[str] | None = _up_archetype_opt,
    backend: str = _up_backend_opt,
    no_standard: bool = _up_no_standard_opt,
    needs: list[str] | None = _up_needs_opt,
    dry_run: bool = _up_dry_run_opt,
) -> None:
    """Bring a project fully up in one command — init, identity, standard, doctor.

    Express mode by default (equivalent to [cyan]grimoire init -y[/cyan]); each
    step is idempotent and reports 'skipped' when already in place.

    [dim]Examples:[/dim]
      [cyan]grimoire up[/cyan]                         Full bring-up of the current directory
      [cyan]grimoire up . --interactive[/cyan]         Run the init wizard first
      [cyan]grimoire up . -a web-app -b local[/cyan]   Explicit archetype and backend
      [cyan]grimoire up . --needs collab-review[/cyan] Standard init from a need profile
      [cyan]grimoire up . --no-standard[/cyan]         Skip the agentic standard step
    """
    target = path.resolve()
    fmt = (ctx.obj or {}).get("output", "text")

    archetypes = _split_csv(archetype)
    invalid = [a for a in archetypes if a not in KNOWN_ARCHETYPES]
    if invalid:
        console.print(f"[red]Unknown archetype(s):[/red] {', '.join(invalid)}")
        console.print(f"Available: {', '.join(sorted(KNOWN_ARCHETYPES))}")
        raise typer.Exit(1)
    if backend not in KNOWN_BACKENDS:
        console.print(f"[red]Unknown backend:[/red] {backend}")
        console.print(f"Available: {', '.join(sorted(KNOWN_BACKENDS))}")
        raise typer.Exit(1)

    state = _UpState()
    target.mkdir(parents=True, exist_ok=True)

    # 1. Init (express unless --interactive) — skipped when already initialized.
    has_config = _step_init(
        ctx, state, target,
        name=name, archetypes=archetypes, backend=backend,
        interactive=interactive, dry_run=dry_run,
    )
    blocked = not has_config

    # 2. Structure reconciliation (idempotent).
    if not blocked:
        _step_structure(state, target, dry_run=dry_run)

    # 3. Identity propagation (cmd_setup logic, non-interactive).
    _step_identity(state, target, user=user, dry_run=dry_run, blocked=blocked)

    # 4. Agentic standard bootstrap.
    cfg = _load_config_quiet(target)
    project_name = (cfg.project.name if cfg else "") or name or target.name
    _step_standard(
        state, target,
        no_standard=no_standard, needs=_split_csv(needs),
        project_name=project_name, dry_run=dry_run, blocked=blocked,
    )

    # 5. Short doctor summary.
    checks = _step_doctor_summary(state, target, dry_run=dry_run, blocked=blocked)

    # Legacy status info (kept for JSON consumers of the previous `up`).
    agents_count = 0
    directories_missing: list[str] = []
    try:
        from grimoire.core.project import GrimoireProject

        project_status = GrimoireProject(target).status()
        agents_count = project_status.agents_count
        directories_missing = list(project_status.directories_missing)
    except GrimoireError:
        pass

    if fmt == "json":
        payload: dict[str, Any] = {
            "ok": not state.failed,
            "project": project_name,
            "dry_run": dry_run,
            "actions": state.actions,
            "steps": [{"step": s.step, "status": s.status, "detail": s.detail} for s in state.steps],
            "agents_count": agents_count,
            "directories_missing": directories_missing,
            "env": [
                {"name": c.name, "passed": c.passed, "level": c.level, "detail": c.detail, "remedy": c.remedy}
                for c in checks
            ],
        }
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _render_report(state, checks, project_name=project_name, dry_run=dry_run)

    if state.failed:
        raise typer.Exit(1)
