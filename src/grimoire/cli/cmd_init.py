"""Enhanced ``grimoire init`` — interactive wizard + full scaffolding.

Replaces the minimal init command with a complete project bootstrapping
experience: stack detection, archetype resolution, agent deployment,
framework installation, and a rich summary report.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from grimoire.__version__ import __version__
from grimoire.core.archetype_resolver import ArchetypeResolver, ResolvedArchetype
from grimoire.core.scaffold import ProjectScaffolder, ScaffoldPlan, ScaffoldResult
from grimoire.core.scanner import ScanResult, StackScanner

console = Console(stderr=True)

# Valid values — keep in sync with config.py
KNOWN_ARCHETYPES = frozenset({
    "minimal", "web-app", "creative-studio", "fix-loop",
    "infra-ops", "meta", "stack", "features", "platform-engineering", "agentic-standard",
})

KNOWN_BACKENDS = frozenset({"auto", "local", "lexical", "qdrant-local", "qdrant-server", "weaviate-server", "mempalace", "ollama"})

# Archetype human descriptions for the wizard (order = display order)
_ARCHETYPE_INFO: dict[str, tuple[str, str, str]] = {
    "web-app": ("Web App", "2 agents", "TDD, type-safety, API-first"),
    "infra-ops": (" Infra & DevOps", "7 agents", "IaC, security-first, observability"),
    "platform-engineering": (" Platform Eng.", "4 agents", "architecture-first, contract-driven"),
    "agentic-standard": (" Agentic Standard", "3 meta-agents", "normative traceability, evidence gates"),
    "creative-studio": ("Creative Studio", "5 agents", "visual-consistency, brand-voice"),
    "fix-loop": ("Fix Loop", "1 agent", "proof-of-execution, severity-adaptive"),
}
# Minimal is always base — not shown in multi-select
_ARCHETYPE_KEYS = list(_ARCHETYPE_INFO.keys())

_QDRANT_DEFAULT_URL = "http://localhost:6333"
_WEAVIATE_DEFAULT_URL = "http://localhost:8080"
_QDRANT_COMPOSE_FILE = "docker-compose.memory.yml"


# ── Memory backend detection ─────────────────────────────────────────────────


def _http_ok(url: str, *, timeout: float = 2.0) -> bool:
    """Return True when a local HTTP probe responds successfully."""
    try:
        req = urllib.request.Request(url, method="GET")  # noqa: S310
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return 200 <= int(resp.status) < 300
    except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError):
        return False


def _is_qdrant_reachable(qdrant_url: str = _QDRANT_DEFAULT_URL) -> bool:
    """Probe Qdrant's local HTTP API."""
    base = qdrant_url.rstrip("/")
    return any(_http_ok(f"{base}{endpoint}") for endpoint in ("/readyz", "/collections", "/healthz"))


def _is_weaviate_reachable(weaviate_url: str = _WEAVIATE_DEFAULT_URL) -> bool:
    """Probe Weaviate's local HTTP API."""
    base = weaviate_url.rstrip("/")
    return any(_http_ok(f"{base}{endpoint}") for endpoint in ("/v1/.well-known/ready", "/v1/meta"))


def detect_memory_backend() -> str:
    """Probe localhost for Memory OS services and return the best local backend."""
    if _is_weaviate_reachable():
        return "weaviate-server"

    if _is_qdrant_reachable():
        return "qdrant-local"

    # Ollama
    if _http_ok("http://localhost:11434/api/tags"):
        return "ollama"

    return "local"


def _wait_for_qdrant(qdrant_url: str = _QDRANT_DEFAULT_URL) -> bool:
    """Wait briefly for a freshly started Qdrant service to answer."""
    for _ in range(10):
        if _is_qdrant_reachable(qdrant_url):
            return True
        time.sleep(0.5)
    return False


def _start_qdrant_docker(target: Path) -> tuple[bool, str]:
    """Start the generated Qdrant Docker Compose service."""
    compose_file = target / _QDRANT_COMPOSE_FILE
    if not compose_file.is_file():
        return False, f"{_QDRANT_COMPOSE_FILE} introuvable dans le projet généré."

    try:
        result = subprocess.run(
            ["docker", "compose", "-f", compose_file.name, "up", "-d"],
            cwd=target,
            capture_output=True,
            text=True,
            timeout=90,
        )
    except FileNotFoundError:
        return False, "Docker CLI introuvable. Lance `docker compose -f docker-compose.memory.yml up -d` après installation."
    except subprocess.TimeoutExpired:
        return False, "Docker Compose n'a pas répondu. Relance `docker compose -f docker-compose.memory.yml up -d`."

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "erreur inconnue"
        return False, f"Docker Compose a échoué: {detail}"

    if _wait_for_qdrant():
        return True, "Qdrant est démarré sur http://localhost:6333."

    return False, "Docker Compose est lancé, mais Qdrant ne répond pas encore sur http://localhost:6333."


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
    *,
    offer_qdrant_docker: bool = False,
) -> dict[str, Any]:
    """Interactive wizard — multi-select archetypes, returns config dict."""
    console.print()
    console.print(Panel.fit(
        f"[bold]Grimoire Kit v{__version__}[/bold] — Project Setup Wizard",
        border_style="cyan",
    ))

    # ── Step 1/4 · Identity ───────────────────────────────────────────
    console.print()
    console.print("  [dim]\\[■□□□] 1/4 · Identity[/dim]")

    # Show detected stacks
    if scan and scan.stacks:
        console.print("  [bold]Stack detected:[/bold]")
        for det in scan.stacks:
            conf_pct = f"{det.confidence:.0%}"
            evidence = ", ".join(det.evidence[:3])
            console.print(f"    [green][OK][/green] {det.name} ({conf_pct}) — {evidence}")
        console.print()

    default_name = target.name
    project_name = Prompt.ask(
        "  [bold]Project name[/bold]",
        default=default_name,
    )

    git_name = _git_user_name()
    user_name = Prompt.ask(
        "  [bold]Your name[/bold]",
        default=git_name or "Developer",
    )

    # ── Step 2/4 · Preferences ────────────────────────────────────────
    console.print()
    console.print("  [dim]\\[■■□□] 2/4 · Preferences[/dim]")

    _lang_choices = {"1": "Français", "2": "English"}
    console.print("  [bold]Language:[/bold]  1) Français  2) English")
    lang_input = Prompt.ask(
        "  [bold]Choose[/bold]",
        default="1",
        choices=["1", "2"],
    )
    language = _lang_choices[lang_input]

    _skill_choices = {"1": "beginner", "2": "intermediate", "3": "expert"}
    console.print("  [bold]Skill:[/bold]    1) Débutant  2) Intermédiaire  3) Expert")
    skill_input = Prompt.ask(
        "  [bold]Choose[/bold]",
        default="2",
        choices=["1", "2", "3"],
    )
    skill_level = _skill_choices[skill_input]

    qdrant_docker = False
    if offer_qdrant_docker:
        console.print()
        console.print("  [bold]Memory:[/bold] Qdrant n'est pas encore disponible sur localhost:6333.")
        qdrant_docker = Confirm.ask(
            "  [bold]Initialiser Qdrant via Docker pour la mémoire sémantique ?[/bold]",
            default=True,
        )
        if qdrant_docker:
            backend = "qdrant-server"

    # ── Step 3/4 · Archetypes (multi-select) ──────────────────────────
    console.print()
    console.print("  [dim]\\[■■■□] 3/4 · Archetypes[/dim]")
    console.print()
    console.print("  [bold]minimal[/bold] is always included — it's the base.")
    console.print("  Choose specializations to add:\n")

    # Compute auto-detected defaults from resolver
    auto_suggested = list(resolved.archetypes) if resolved.archetypes else [resolved.archetype]
    auto_indices: list[str] = []
    for idx, key in enumerate(_ARCHETYPE_KEYS, 1):
        label, agent_count, traits = _ARCHETYPE_INFO[key]
        marker = " [cyan]← detected[/cyan]" if key in auto_suggested and key != "minimal" else ""
        console.print(f"    [bold]{idx}[/bold]) {label:<22} {agent_count:<10} {traits}{marker}")
        if key in auto_suggested and key != "minimal":
            auto_indices.append(str(idx))

    console.print()
    console.print("    [bold]0[/bold]) Not sure — help me choose")
    console.print()

    default_input = ",".join(auto_indices) if auto_indices else ""
    arch_input = Prompt.ask(
        "  [bold]Choice (ex: 1,3,5 or all)[/bold]",
        default=default_input or "none",
    )

    # Parse selection
    selected_archetypes = _parse_archetype_selection(arch_input, scan)

    # Show composition preview
    _display_composition_preview(selected_archetypes)

    # Allow adjustment
    adjust = Prompt.ask(
        "  [bold]Adjust? (new numbers, or Enter to confirm)[/bold]",
        default="",
    )
    if adjust.strip():
        selected_archetypes = _parse_archetype_selection(adjust, scan)
        _display_composition_preview(selected_archetypes)

    # ── Step 4/4 · Confirm ────────────────────────────────────────────
    console.print()
    console.print("  [dim]\\[■■■■] 4/4 · Confirmation[/dim]")
    arch_display = ", ".join(selected_archetypes) if selected_archetypes else "minimal"
    console.print()
    console.print("  [bold]Summary:[/bold]")
    console.print(f"    Project:     {project_name}")
    console.print(f"    User:        {user_name}")
    console.print(f"    Language:    {language}")
    console.print(f"    Skill level: {skill_level}")
    console.print(f"    Archetypes:  {arch_display}")
    console.print(f"    Backend:     {backend}")
    if qdrant_docker:
        console.print("    Qdrant:      Docker local auto-start")
    console.print()

    if not Confirm.ask("  [bold]Proceed with installation?[/bold]", default=True):
        raise typer.Abort

    return {
        "project_name": project_name,
        "user_name": user_name,
        "language": language,
        "skill_level": skill_level,
        "archetypes": selected_archetypes,
        "archetype": selected_archetypes[0] if selected_archetypes else "minimal",
        "backend": backend,
        "qdrant_docker": qdrant_docker,
    }


def _parse_archetype_selection(
    raw: str,
    scan: ScanResult | None = None,
) -> list[str]:
    """Parse user input like '1,3,5' or 'all' or '0' into archetype list."""
    raw = raw.strip().lower()

    if raw == "all":
        return list(_ARCHETYPE_KEYS)

    if raw in ("none", ""):
        return ["minimal"]

    if raw == "0":
        return _guided_discovery(scan)

    # Parse comma/space separated numbers
    parts = raw.replace(" ", ",").split(",")
    selected: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        try:
            idx = int(p)
        except ValueError:
            # Try as archetype name directly
            if p in _ARCHETYPE_KEYS and p not in selected:
                selected.append(p)
            continue
        if 1 <= idx <= len(_ARCHETYPE_KEYS):
            key = _ARCHETYPE_KEYS[idx - 1]
            if key not in selected:
                selected.append(key)

    return selected or ["minimal"]


def _guided_discovery(scan: ScanResult | None) -> list[str]:
    """3-question guided flow for users who don't know which archetypes to pick."""
    console.print()
    console.print("  [bold]── Guided Discovery ──[/bold]\n")

    # Detect defaults from scan
    detected = {d.name for d in scan.stacks} if scan and scan.stacks else set()
    has_frontend = bool(detected & {"react", "vue", "angular", "javascript", "typescript"})
    has_infra = bool(detected & {"terraform", "kubernetes", "ansible", "docker"})

    q1 = Confirm.ask(
        "  Does your project have a [bold]web frontend[/bold] (React, Vue, Angular)?",
        default=has_frontend,
    )
    q2 = Confirm.ask(
        "  Do you manage [bold]infrastructure[/bold] (K8s, Terraform, CI/CD)?",
        default=has_infra,
    )
    q3 = Confirm.ask(
        "  Do you need a [bold]certified fix loop[/bold] (TDD proofs, incident response)?",
        default=False,
    )

    result: list[str] = []
    if q1:
        result.append("web-app")
    if q2:
        result.append("infra-ops")
    if q3:
        result.append("fix-loop")

    # If nothing selected, check for platform patterns
    if not result and detected & {"python", "go", "fastapi", "django"}:
        result.append("platform-engineering")

    if not result:
        console.print("  [dim]No specialization selected — using minimal base.[/dim]")
        return ["minimal"]

    names = ", ".join(result)
    console.print(f"\n  [bold]Recommended:[/bold] {names}")
    return result


def _display_composition_preview(archetypes: list[str]) -> None:
    """Show what the selected composition includes."""
    console.print()
    console.print("  [bold]── Composition ──[/bold]")
    console.print("  Base : [bold]minimal[/bold] (3 meta-agents)")
    total_agents = 3  # meta-agents
    for key in archetypes:
        if key == "minimal":
            continue
        info = _ARCHETYPE_INFO.get(key)
        if info:
            label, agent_count, traits = info
            console.print(f"  {label:<22} → +{agent_count} · {traits}")
            # Parse agent count
            with contextlib.suppress(ValueError, IndexError):
                total_agents += int(agent_count.split()[0])
    console.print(f"\n  [dim]Total: ~{total_agents} agents[/dim]")


# ── Rich summary report ─────────────────────────────────────────────────────


def _display_report(
    target: Path,
    result: ScaffoldResult,
    resolved: ResolvedArchetype,
    scan: ScanResult | None,
    backend: str,
    project_name: str,
    *,
    qdrant_docker_started: bool = False,
    qdrant_docker_message: str = "",
) -> None:
    """Display a rich post-install report."""
    console.print()

    # Stack detection
    if scan and scan.stacks:
        stacks_str = " · ".join(
            f"[bold]{d.name}[/bold]" for d in scan.stacks
        )
        console.print(f"  [cyan]Stack:[/cyan] {stacks_str}")

    # Archetypes
    if resolved.is_composite:
        names = []
        for a in resolved.archetypes:
            ai = _ARCHETYPE_INFO.get(a)
            names.append(ai[0] if ai else a)
        console.print(f"  [cyan]Archetypes:[/cyan] {' + '.join(names)}")
    else:
        info = _ARCHETYPE_INFO.get(resolved.archetype, (resolved.archetype, "", ""))
        console.print(f"  [cyan]Archetype:[/cyan] {info[0]} ({resolved.reason})")
    console.print(f"  [cyan]Memory:[/cyan] {backend}")
    _backend_tips = {
        "local": "Mémoire fichier locale — aucune dépendance requise",
        "qdrant-local": "Qdrant détecté sur localhost:6333 — recherche sémantique activée",
        "qdrant-server": "Qdrant distant configuré — vérifier avec grimoire doctor",
        "weaviate-server": "Weaviate + Neo4j configurés — vérifier avec grimoire memory status et memory migrate verify",
        "ollama": "Ollama détecté — embeddings locaux activés",
    }
    _tip = _backend_tips.get(backend)
    if _tip:
        console.print(f"           [dim]{_tip}[/dim]")
    if qdrant_docker_message:
        status = "[green]OK[/green]" if qdrant_docker_started else "[yellow]WARN[/yellow]"
        console.print(f"           {status} [dim]{qdrant_docker_message}[/dim]")
    console.print()

    # Agents deployed (categorized)
    agents_by_cat: dict[str, list[str]] = {}
    for label in result.copied_files:
        if "/" in label:
            cat, name = label.split("/", 1)
            agents_by_cat.setdefault(cat, []).append(name)
    _cat_icons = {"meta": "", "stack": "", "feature": ""}
    if agents_by_cat:
        console.print("  [cyan]Agents deployed:[/cyan]")
        for cat, agents in agents_by_cat.items():
            icon = _cat_icons.get(cat, "")
            agent_names = ", ".join(f"[bold]{a}[/bold]" for a in agents)
            console.print(f"    {icon} [dim]{cat}:[/dim] {agent_names}")
        console.print()

    # Summary counts
    console.print(f"  [dim]{len(result.created_dirs)} dirs · {len(result.copied_files)} files · {len(result.rendered_files)} configs[/dim]")
    console.print()

    # Next steps
    console.print(Panel(
        "[bold]Your project is alive![/bold]\n\n"
        f"  [bold cyan]Step 1:[/bold cyan] Open your project in VS Code\n"
        f"           [cyan]code {target.name}[/cyan]\n\n"
        "  [bold cyan]Step 2:[/bold cyan] Talk to your AI concierge\n"
        "           Open Copilot Chat → type [cyan]@concierge[/cyan]\n"
        "           Marcel will guide you through your first session.\n\n"
        "  [dim]Optional:[/dim]\n"
        "  Health check:   [cyan]grimoire doctor[/cyan]\n"
        "  Agent registry: [cyan]grimoire status[/cyan]",
        title="[bold green]Next Steps[/bold green]",
        border_style="green",
    ))


def _display_dry_run(
    plan: ScaffoldPlan,
    target: Path,
    project_name: str,
    archetype: str,
    resolved: ResolvedArchetype | None = None,
) -> None:
    """Display what would happen in dry-run mode."""
    console.print("[bold]grimoire init --dry-run[/bold]")
    arch_display = archetype
    if resolved and resolved.is_composite:
        arch_display = ", ".join(resolved.archetypes)
    console.print(f"[dim]Scaffold plan for [bold]{project_name}[/bold] (archetypes: {arch_display})[/dim]\n")

    # Agent deployment breakdown
    agents_by_cat: dict[str, list[str]] = {}
    for fc in plan.copies:
        if fc.label and "/" in fc.label and fc.dst.suffix == ".md" and "/agents/" in str(fc.dst):
            cat, name = fc.label.split("/", 1)
            agents_by_cat.setdefault(cat, []).append(name)
    if agents_by_cat:
        console.print("[bold]Agents to deploy:[/bold]")
        _cat_icons = {"meta": "", "stack": "", "feature": ""}
        for cat, agents in agents_by_cat.items():
            icon = _cat_icons.get(cat, "")
            console.print(f"  {icon} [cyan]{cat}[/cyan]: {', '.join(agents)}")
        console.print()

    # DNA traits preview
    dna_copies = [fc for fc in plan.copies if "archetype.dna.yaml" in fc.label]
    if dna_copies:
        console.print(f"[bold]Archetype DNA:[/bold] {archetype}")
        try:
            dna_text = dna_copies[0].src.read_text(encoding="utf-8")
            for key in ("traits:", "constraints:", "values:"):
                if key in dna_text:
                    console.print(f"  [dim]{key}[/dim]")
                    in_section = False
                    for line in dna_text.splitlines():
                        if line.strip().startswith(key):
                            in_section = True
                            continue
                        if in_section:
                            if line.startswith(("  - ", "  - ")):
                                val = line.strip().lstrip("- ").split(":")[0]
                                console.print(f"    [green][OK][/green] {val}")
                            elif not line.startswith(" "):
                                break
        except OSError:
            pass
        console.print()

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

    # Gitignore preview
    gi_tpls = [t for t in plan.templates if ".gitignore" in (t.label or "")]
    if gi_tpls:
        console.print("\n[bold].gitignore patterns added:[/bold]")
        for line in gi_tpls[0].content.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                console.print(f"  [dim]{stripped}[/dim]")

    console.print(f"\n[dim]Total: {plan.total_operations} operations[/dim]")


def _display_json(
    target: Path,
    result: ScaffoldResult,
    resolved: ResolvedArchetype,
    scan: ScanResult | None,
    backend: str,
    project_name: str,
    *,
    qdrant_docker: dict[str, Any] | None = None,
) -> None:
    """Output JSON result for scripting."""
    data: dict[str, Any] = {
        "ok": True,
        "project": project_name,
        "path": str(target),
        "archetype": resolved.archetype,
        "archetypes": list(resolved.archetypes) if resolved.archetypes else [resolved.archetype],
        "backend": backend,
        "stacks": [d.name for d in scan.stacks] if scan else [],
        "agents": {
            "total": len(result.copied_files),
            "by_category": {},
            "list": result.copied_files,
        },
        "dirs_created": len(result.created_dirs),
        "files_copied": len(result.copied_files),
        "configs_generated": len(result.rendered_files),
    }
    if qdrant_docker is not None:
        data["qdrant_docker"] = qdrant_docker
    for label in result.copied_files:
        if "/" in label:
            cat = label.split("/")[0]
            data["agents"]["by_category"].setdefault(cat, []).append(label.split("/", 1)[1])
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
    qdrant_docker: bool = False,
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
    requested_backend = backend
    qdrant_docker_requested = qdrant_docker
    if qdrant_docker_requested:
        backend = "qdrant-server"
    elif backend == "auto":
        backend = detect_memory_backend()

    # Phase 3: Resolve archetype
    resolver = ArchetypeResolver()
    # Parse comma-separated archetypes from CLI
    archetypes_override: list[str] | None = None
    if archetype:
        archetypes_override = [a.strip() for a in archetype.split(",") if a.strip()]
    resolved = resolver.resolve(
        scan,
        backend=backend,
        archetypes_override=archetypes_override,
    )

    # Phase 4: Interactive wizard or express mode
    project_name = name or target.name
    user_name = _git_user_name() or "Developer"
    language = "Français"
    skill_level = "intermediate"

    is_interactive = sys.stdin.isatty() and not yes and fmt != "json"

    offer_qdrant_docker = requested_backend == "auto" and backend == "local"

    if is_interactive and not dry_run:
        wizard_result = _run_wizard(
            target,
            scan,
            resolved,
            backend,
            offer_qdrant_docker=offer_qdrant_docker,
        )
        project_name = wizard_result["project_name"]
        user_name = wizard_result["user_name"]
        language = wizard_result["language"]
        skill_level = wizard_result["skill_level"]
        qdrant_docker_requested = qdrant_docker_requested or bool(wizard_result.get("qdrant_docker", False))
        # Re-resolve if user changed archetypes or backend
        new_archetypes = wizard_result.get("archetypes", [wizard_result.get("archetype", "minimal")])
        new_backend = wizard_result["backend"]
        current_archs = list(resolved.archetypes) if resolved.archetypes else [resolved.archetype]
        if set(new_archetypes) != set(current_archs) or new_backend != backend:
            resolved = resolver.resolve(
                scan,
                backend=new_backend,
                archetypes_override=new_archetypes,
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
                "archetypes": list(resolved.archetypes) if resolved.archetypes else [resolved.archetype],
                "backend": backend,
                "stacks": [d.name for d in scan.stacks],
                "stack_agents": list(resolved.stack_agents),
                "feature_agents": list(resolved.feature_agents),
                "qdrant_docker": qdrant_docker_requested,
            }, indent=2))
        else:
            _display_dry_run(plan, target, project_name, resolved.archetype, resolved)
        return

    # Phase 6: Execute
    result = scaffolder.execute(plan)

    qdrant_docker_started = False
    qdrant_docker_message = ""
    if qdrant_docker_requested:
        if _is_qdrant_reachable():
            qdrant_docker_started = True
            qdrant_docker_message = "Qdrant est déjà disponible sur http://localhost:6333."
        else:
            qdrant_docker_started, qdrant_docker_message = _start_qdrant_docker(target)

    # Phase 7: Report
    if fmt == "json":
        docker_status = None
        if qdrant_docker_requested:
            docker_status = {
                "requested": True,
                "started": qdrant_docker_started,
                "message": qdrant_docker_message,
            }
        _display_json(target, result, resolved, scan, backend, project_name, qdrant_docker=docker_status)
    else:
        _display_report(
            target,
            result,
            resolved,
            scan,
            backend,
            project_name,
            qdrant_docker_started=qdrant_docker_started,
            qdrant_docker_message=qdrant_docker_message,
        )
