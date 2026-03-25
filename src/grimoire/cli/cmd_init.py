"""Enhanced ``grimoire init`` — interactive wizard + full scaffolding.

Replaces the minimal init command with a complete project bootstrapping
experience: stack detection, archetype resolution, agent deployment,
framework installation, and a rich summary report.
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from grimoire.__version__ import __version__
from grimoire.core.archetype_resolver import ArchetypeResolver, ResolvedArchetype
from grimoire.core.scaffold import ProjectScaffolder, ScaffoldPlan, ScaffoldResult
from grimoire.core.scanner import ScanResult, StackScanner

console = Console(stderr=True)

# Valid values — keep in sync with config.py
KNOWN_ARCHETYPES = frozenset({
    "minimal", "web-app", "creative-studio", "fix-loop",
    "infra-ops", "meta", "stack", "features", "platform-engineering",
})

KNOWN_BACKENDS = frozenset({"auto", "local", "qdrant-local", "qdrant-server", "ollama"})

# Archetype human descriptions for the wizard
_ARCHETYPE_INFO: dict[str, tuple[str, str]] = {
    "minimal": ("Minimal", "Core meta-agents only — lightweight starting point"),
    "web-app": ("Web Application", "Frontend + backend agents for full-stack projects"),
    "infra-ops": ("Infrastructure & DevOps", "Terraform, K8s, Ansible, monitoring, security specialists"),
    "creative-studio": ("Creative Studio", "Brand design, illustration, content creation agents"),
    "fix-loop": ("Fix Loop", "9-phase automated bug correction orchestrator"),
    "platform-engineering": ("Platform Engineering", "Microservices, deploy, reliability agents"),
}


# ── Memory backend detection ─────────────────────────────────────────────────


def detect_memory_backend() -> str:
    """Probe localhost for Qdrant or Ollama, return best backend."""
    # Qdrant
    try:
        req = urllib.request.Request("http://localhost:6333/healthz", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:  # noqa: S310
            if resp.status == 200:
                return "qdrant-local"
    except (OSError, urllib.error.URLError):
        pass

    # Ollama
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:  # noqa: S310
            if resp.status == 200:
                return "ollama"
    except (OSError, urllib.error.URLError):
        pass

    return "local"


def _git_user_name() -> str:
    """Try to get git user.name, return empty on failure."""
    try:
        result = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


# ── Interactive wizard ───────────────────────────────────────────────────────


def _run_wizard(
    target: Path,
    scan: ScanResult | None,
    resolved: ResolvedArchetype,
    backend: str,
) -> dict[str, Any]:
    """Interactive wizard — asks 3-4 questions, returns config dict."""
    console.print()
    console.print(Panel.fit(
        f"[bold]Grimoire Kit v{__version__}[/bold] — Project Setup Wizard",
        border_style="cyan",
    ))
    console.print()

    # Show detected stacks
    if scan and scan.stacks:
        console.print("  [bold]Stack detected:[/bold]")
        for det in scan.stacks:
            conf_pct = f"{det.confidence:.0%}"
            evidence = ", ".join(det.evidence[:3])
            console.print(f"    [green]✓[/green] {det.name} ({conf_pct}) — {evidence}")
        console.print()

    # Q1 — Project name
    default_name = target.name
    project_name = Prompt.ask(
        "  [bold]Project name[/bold]",
        default=default_name,
    )

    # Q2 — User name
    git_name = _git_user_name()
    user_name = Prompt.ask(
        "  [bold]Your name[/bold]",
        default=git_name or "Developer",
    )

    # Q3 — Archetype selection
    console.print()
    console.print("  [bold]Available archetypes:[/bold]")
    arch_choices: list[str] = []
    idx = 1
    for key, (label, desc) in _ARCHETYPE_INFO.items():
        marker = " [cyan]← auto-detected[/cyan]" if key == resolved.archetype else ""
        console.print(f"    [bold]{idx}[/bold]) {label}{marker}")
        console.print(f"       [dim]{desc}[/dim]")
        arch_choices.append(key)
        idx += 1
    console.print(f"    [bold]{idx}[/bold]) I'm not sure — use minimal")
    console.print()

    default_idx = str(arch_choices.index(resolved.archetype) + 1) if resolved.archetype in arch_choices else "1"
    arch_input = Prompt.ask(
        "  [bold]Choose archetype[/bold]",
        default=default_idx,
        choices=[str(i) for i in range(1, len(arch_choices) + 2)],
    )
    arch_idx = int(arch_input)
    archetype = "minimal" if arch_idx > len(arch_choices) else arch_choices[arch_idx - 1]

    # Q4 — Confirm
    console.print()
    console.print("  [bold]Summary:[/bold]")
    console.print(f"    Project:   {project_name}")
    console.print(f"    User:      {user_name}")
    console.print(f"    Archetype: {archetype}")
    console.print(f"    Backend:   {backend}")
    console.print()

    if not Confirm.ask("  [bold]Proceed with installation?[/bold]", default=True):
        raise typer.Abort

    return {
        "project_name": project_name,
        "user_name": user_name,
        "archetype": archetype,
        "backend": backend,
    }


# ── Rich summary report ─────────────────────────────────────────────────────


def _display_report(
    target: Path,
    result: ScaffoldResult,
    resolved: ResolvedArchetype,
    scan: ScanResult | None,
    backend: str,
    project_name: str,
) -> None:
    """Display a rich post-install report."""
    console.print()

    # Stack detection
    if scan and scan.stacks:
        stacks_str = " · ".join(
            f"[bold]{d.name}[/bold]" for d in scan.stacks
        )
        console.print(f"  [cyan]📦 Stack:[/cyan] {stacks_str}")

    # Archetype
    info = _ARCHETYPE_INFO.get(resolved.archetype, (resolved.archetype, ""))
    console.print(f"  [cyan]🧬 Archetype:[/cyan] {info[0]} ({resolved.reason})")
    console.print(f"  [cyan]🧠 Memory:[/cyan] {backend}")
    console.print()

    # Agents deployed
    agents_table = Table(show_header=False, box=None, padding=(0, 2))
    for label in result.copied_files:
        if "/" in label:
            category, name = label.split("/", 1)
            agents_table.add_row("[green]✓[/green]", f"[bold]{name}[/bold]", f"[dim]{category}[/dim]")
    if agents_table.row_count:
        console.print("  [cyan]🤖 Agents deployed:[/cyan]")
        console.print(agents_table)
        console.print()

    # Summary counts
    console.print(f"  [dim]{len(result.created_dirs)} dirs · {len(result.copied_files)} files · {len(result.rendered_files)} configs[/dim]")
    console.print()

    # Next steps
    console.print(Panel(
        "[bold]Your project is alive![/bold]\n\n"
        f"  Open VS Code:  [cyan]code {target.name}[/cyan]\n"
        "  Health check:  [cyan]grimoire doctor[/cyan]\n"
        "  See agents:    [cyan]grimoire status[/cyan]\n"
        "  Get started:   [cyan]Ask @grimoire in Copilot Chat[/cyan]",
        title="[bold green]Next Steps[/bold green]",
        border_style="green",
    ))


def _display_dry_run(
    plan: ScaffoldPlan,
    target: Path,
    project_name: str,
    archetype: str,
) -> None:
    """Display what would happen in dry-run mode."""
    console.print("[bold]grimoire init --dry-run[/bold]")
    console.print(f"[dim]Scaffold plan for [bold]{project_name}[/bold] (archetype: {archetype})[/dim]\n")

    if plan.directories:
        console.print("[bold]Directories:[/bold]")
        for d in plan.directories:
            console.print(f"  [cyan]mkdir[/cyan]  {d.relative_to(target)}/")

    if plan.copies:
        console.print("\n[bold]File copies:[/bold]")
        for fc in plan.copies:
            console.print(f"  [cyan]copy[/cyan]   {fc.label}")

    if plan.templates:
        console.print("\n[bold]Generated files:[/bold]")
        for tr in plan.templates:
            console.print(f"  [cyan]write[/cyan]  {tr.label}")

    console.print(f"\n[dim]Total: {plan.total_operations} operations[/dim]")


def _display_json(
    target: Path,
    result: ScaffoldResult,
    resolved: ResolvedArchetype,
    scan: ScanResult | None,
    backend: str,
    project_name: str,
) -> None:
    """Output JSON result for scripting."""
    data = {
        "ok": True,
        "project": project_name,
        "path": str(target),
        "archetype": resolved.archetype,
        "backend": backend,
        "stacks": [d.name for d in scan.stacks] if scan else [],
        "agents": result.copied_files,
        "dirs_created": len(result.created_dirs),
        "files_copied": len(result.copied_files),
        "configs_generated": len(result.rendered_files),
    }
    typer.echo(json.dumps(data, indent=2))


# ── Main entry point ─────────────────────────────────────────────────────────


def run_init(
    ctx: typer.Context,
    target: Path,
    *,
    name: str = "",
    archetype: str = "",
    backend: str = "auto",
    force: bool = False,
    dry_run: bool = False,
) -> None:
    """Execute the enhanced init flow: scan → resolve → wizard → scaffold → report."""
    target = target.resolve()
    fmt = (ctx.obj or {}).get("output", "text")
    yes = (ctx.obj or {}).get("yes", False)

    # Check existing project
    config_file = target / "project-context.yaml"
    if config_file.exists() and not force:
        if fmt == "json":
            typer.echo(json.dumps({"ok": False, "error": "project-context.yaml already exists"}, indent=2))
        else:
            console.print(f"[yellow]project-context.yaml already exists at {target}[/yellow]")
            console.print("Use [bold]--force[/bold] to overwrite.")
        raise typer.Exit(1)

    target.mkdir(parents=True, exist_ok=True)

    # Phase 1: Scan
    scanner = StackScanner(target)
    scan = scanner.scan()

    # Phase 2: Resolve backend
    if backend == "auto":
        backend = detect_memory_backend()

    # Phase 3: Resolve archetype
    resolver = ArchetypeResolver()
    resolved = resolver.resolve(
        scan,
        backend=backend,
        archetype_override=archetype or None,
    )

    # Phase 4: Interactive wizard or express mode
    project_name = name or target.name
    user_name = _git_user_name() or "Developer"
    language = "Français"
    skill_level = "intermediate"

    is_interactive = sys.stdin.isatty() and not yes and fmt != "json"

    if is_interactive and not dry_run:
        wizard_result = _run_wizard(target, scan, resolved, backend)
        project_name = wizard_result["project_name"]
        user_name = wizard_result["user_name"]
        # Re-resolve if user changed archetype or backend
        new_arch = wizard_result["archetype"]
        new_backend = wizard_result["backend"]
        if new_arch != resolved.archetype or new_backend != backend:
            resolved = resolver.resolve(
                scan,
                backend=new_backend,
                archetype_override=new_arch,
            )
        backend = new_backend

    # Phase 5: Plan
    scaffolder = ProjectScaffolder(
        target,
        project_name=project_name,
        user_name=user_name,
        language=language,
        skill_level=skill_level,
        scan=scan,
        resolved=resolved,
        backend=backend,
    )
    plan = scaffolder.plan()

    # Dry-run — show plan and exit
    if dry_run:
        if fmt == "json":
            typer.echo(json.dumps({
                "dry_run": True,
                "directories": len(plan.directories),
                "copies": len(plan.copies),
                "templates": len(plan.templates),
                "archetype": resolved.archetype,
                "backend": backend,
                "stacks": [d.name for d in scan.stacks],
                "stack_agents": list(resolved.stack_agents),
                "feature_agents": list(resolved.feature_agents),
            }, indent=2))
        else:
            _display_dry_run(plan, target, project_name, resolved.archetype)
        return

    # Phase 6: Execute
    result = scaffolder.execute(plan)

    # Phase 7: Report
    if fmt == "json":
        _display_json(target, result, resolved, scan, backend, project_name)
    else:
        _display_report(target, result, resolved, scan, backend, project_name)
