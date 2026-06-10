"""CLI commands for the agentic standard bridge."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from grimoire.core.agentic_standard import (
    InstallPlan,
    StandardProviderDetection,
    StandardRemediationAction,
    StandardRemediationApplyResult,
    StandardRuntimeArtifact,
    StandardVerificationResult,
    apply_remediation_actions,
    audit_runtime_events,
    build_context_bundle,
    build_decision_trace,
    build_knowledge_graph,
    build_knowledge_index,
    calculate_compliance_score,
    check_evidence_gates,
    detect_standard_providers,
    list_profiles,
    list_standard_patterns,
    load_capability_map,
    load_needs_catalog,
    propose_remediation_actions,
    resolve_install_plan,
    setup_standard_profile,
    show_standard_pattern,
    simulate_standard_hooks,
    verify_knowledge_index,
    verify_standard_profile,
)

standard_app = typer.Typer(
    help="Apply and verify the agentic standard bridge.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
board_app = typer.Typer(help="Verify the governed standard task board.", no_args_is_help=True, rich_markup_mode="rich")
memory_app = typer.Typer(help="Verify memory governance artifacts.", no_args_is_help=True, rich_markup_mode="rich")
context_app = typer.Typer(help="Build and verify context bundles.", no_args_is_help=True, rich_markup_mode="rich")
decision_app = typer.Typer(help="Build and explain decision traces.", no_args_is_help=True, rich_markup_mode="rich")
rules_app = typer.Typer(help="Verify rule packs.", no_args_is_help=True, rich_markup_mode="rich")
hooks_app = typer.Typer(help="Verify and simulate hooks.", no_args_is_help=True, rich_markup_mode="rich")
gate_app = typer.Typer(help="Check evidence gates.", no_args_is_help=True, rich_markup_mode="rich")
events_app = typer.Typer(help="Audit the standard runtime journal.", no_args_is_help=True, rich_markup_mode="rich")
pattern_app = typer.Typer(help="List and inspect standard patterns.", no_args_is_help=True, rich_markup_mode="rich")
knowledge_app = typer.Typer(help="Build and verify standard knowledge indexes.", no_args_is_help=True, rich_markup_mode="rich")

standard_app.add_typer(board_app, name="board")
standard_app.add_typer(memory_app, name="memory")
standard_app.add_typer(context_app, name="context")
standard_app.add_typer(decision_app, name="decision")
standard_app.add_typer(rules_app, name="rules")
standard_app.add_typer(hooks_app, name="hooks")
standard_app.add_typer(gate_app, name="gate")
standard_app.add_typer(events_app, name="events")
standard_app.add_typer(pattern_app, name="pattern")
standard_app.add_typer(knowledge_app, name="knowledge")
# Human-readable Rich output goes to stderr so JSON emitted with typer.echo remains pipeable on stdout.
console = Console(stderr=True)


def _get_fmt(ctx: typer.Context) -> str:
    return str((ctx.obj or {}).get("output", "text"))


def _paths(paths: list[Path]) -> list[str]:
    return [str(path) for path in paths]


def _checks(result: StandardVerificationResult) -> list[dict[str, str | None]]:
    return [
        {
            "id": check.id,
            "severity": check.severity,
            "message": check.message,
            "path": str(check.path) if check.path else None,
        }
        for check in result.checks
    ]


def _artifact_json(artifact: StandardRuntimeArtifact) -> dict[str, object]:
    return {"path": str(artifact.path), "data": artifact.data}


def _remediation_json(actions: Sequence[StandardRemediationAction]) -> list[dict[str, str | None]]:
    return [
        {
            "check_id": action.check_id,
            "severity": action.severity,
            "action": action.action,
            "path": str(action.path) if action.path else None,
            "message": action.message,
        }
        for action in actions
    ]


def _remediation_apply_json(result: StandardRemediationApplyResult) -> dict[str, object]:
    return {
        "profile": result.profile,
        "project_root": str(result.project_root),
        "applied": True,
        "actions": _remediation_json(result.actions),
        "written": _paths(list(result.written)),
        "skipped": list(result.skipped),
        "audit_path": str(result.audit_path),
    }


_FILTERED_REQUIRED_PATHS: dict[str, tuple[Path, ...]] = {
    "board.": (Path("_grimoire/standard/task-board.yaml"),),
    "memory.": (Path("_grimoire/standard/memory-policy.yaml"),),
    "context.": (Path("_grimoire/standard/context-contract.yaml"),),
    "decision.": (Path("_grimoire/standard/decision-graph.yaml"),),
    "rules.": (Path("_grimoire/standard/rule-packs.yaml"),),
    "hooks.": (Path("_grimoire/standard/hook-registry.yaml"),),
}


def _filtered_result(result: StandardVerificationResult, prefixes: tuple[str, ...]) -> dict[str, Any]:
    checks = [
        check
        for check in result.checks
        if check.id.startswith(prefixes)
    ]
    required_paths = {
        path
        for prefix in prefixes
        for path in _FILTERED_REQUIRED_PATHS.get(prefix, ())
    }
    missing = [path for path in result.missing if path in required_paths]
    invalid_yaml = [path for path in result.invalid_yaml if path in required_paths]
    return {
        "ok": not missing and not invalid_yaml and not any(check.is_error for check in checks),
        "profile": result.profile,
        "project_root": str(result.project_root),
        "missing": _paths(missing),
        "invalid_yaml": _paths(invalid_yaml),
        "checks": [
            {
                "id": check.id,
                "severity": check.severity,
                "message": check.message,
                "path": str(check.path) if check.path else None,
            }
            for check in checks
        ],
        "error_count": len(missing) + len(invalid_yaml) + sum(1 for check in checks if check.is_error),
        "warning_count": sum(1 for check in checks if check.severity == "warning"),
    }


def _echo_filtered_verification(ctx: typer.Context, result: StandardVerificationResult, prefixes: tuple[str, ...], title: str) -> None:
    filtered = _filtered_result(result, prefixes)
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps(filtered, indent=2, ensure_ascii=False))
        raise typer.Exit(0 if filtered["ok"] else 1)
    status = "[green]OK[/green]" if filtered["ok"] else "[red]FAIL[/red]"
    console.print(f"{status} {title} ({filtered['error_count']} error(s), {filtered['warning_count']} warning(s))")
    for check in filtered["checks"]:
        color = "red" if check["severity"] == "error" else "yellow"
        path_text = f" ({check['path']})" if check.get("path") else ""
        console.print(f"  [{color}]![/{color}] {check['id']}{path_text}: {check['message']}")
    raise typer.Exit(0 if filtered["ok"] else 1)


def _provider_detection_json(providers: Sequence[StandardProviderDetection]) -> list[dict[str, object]]:
    return [
        {
            "id": provider.id,
            "available": provider.available,
            "signals": list(provider.signals),
            "note": provider.note,
        }
        for provider in providers
    ]


def _audit_markdown(result: StandardVerificationResult) -> str:
    status = "OK" if result.ok else "FAIL"
    lines = [
        f"# Agentic Standard Audit — {result.profile}",
        "",
        f"- Status: `{status}`",
        f"- Project root: `{result.project_root}`",
        f"- Errors: `{result.error_count}`",
        f"- Warnings: `{result.warning_count}`",
        "",
        "## Required artifacts",
        "",
    ]
    if result.present:
        lines.extend(f"- OK `{path}`" for path in result.present)
    if result.missing:
        lines.extend(f"- MISSING `{path}`" for path in result.missing)
    if result.invalid_yaml:
        lines.extend(f"- INVALID YAML `{path}`" for path in result.invalid_yaml)
    lines.extend(["", "## Checks", ""])
    if result.checks:
        lines.extend(
            f"- **{check.severity.upper()}** `{check.id}`"
            f"{f' in `{check.path}`' if check.path else ''}: {check.message}"
            for check in result.checks
        )
    else:
        lines.append("- No content issues detected.")
    return "\n".join(lines) + "\n"


@standard_app.command("profiles")
def profiles(ctx: typer.Context) -> None:
    """List available agentic standard profiles."""
    profiles_list = list_profiles()
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps([
            {
                "id": profile.id,
                "display_name": profile.display_name,
                "required_artifacts": list(profile.required_artifacts),
                "mapped_capabilities": list(profile.mapped_capabilities),
            }
            for profile in profiles_list
        ], indent=2, ensure_ascii=False))
        return

    table = Table(title="Agentic standard profiles")
    table.add_column("ID")
    table.add_column("Name")
    table.add_column("Artifacts")
    table.add_column("Capabilities")
    for profile in profiles_list:
        table.add_row(
            profile.id,
            profile.display_name,
            ", ".join(profile.required_artifacts),
            ", ".join(profile.mapped_capabilities),
        )
    console.print(table)


@standard_app.command("detect-providers")
def detect_providers(ctx: typer.Context) -> None:
    """Detect non-secret provider availability signals."""
    detections = detect_standard_providers()
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps(_provider_detection_json(detections), indent=2, ensure_ascii=False))
        return

    table = Table(title="Detected LLM provider signals")
    table.add_column("Provider")
    table.add_column("Available")
    table.add_column("Signals")
    table.add_column("Note")
    for provider in detections:
        table.add_row(
            provider.id,
            "yes" if provider.available else "no",
            ", ".join(provider.signals) if provider.signals else "-",
            provider.note,
        )
    console.print(table)


_EXTRA_IMPORT_PROBES: dict[str, tuple[str, ...]] = {
    "redis": ("redis",),
    "weaviate": ("weaviate", "sentence_transformers"),
    "neo4j": ("neo4j",),
    "qdrant": ("qdrant_client", "sentence_transformers"),
    "mempalace": ("chromadb",),
    "ollama": ("ollama",),
    "mcp": ("mcp",),
}


def _split_csv(values: list[str] | None) -> list[str]:
    collected: list[str] = []
    for value in values or []:
        for part in str(value).split(","):
            cleaned = part.strip()
            if cleaned and cleaned not in collected:
                collected.append(cleaned)
    return collected


_TIER_ORDER: tuple[str, ...] = ("essential", "advanced", "enterprise")
_TIER_HEADING: dict[str, str] = {
    "essential": "Essentials — start here",
    "advanced": "Advanced — add when needed",
    "enterprise": "Enterprise — full governance",
}


def _need_tier(need: dict[str, Any]) -> str:
    tier = str(need.get("tier", "")).strip().lower()
    return tier if tier in _TIER_ORDER else "advanced"


def _sorted_needs(needs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order needs essentials-first, recommended-first within a tier."""

    def sort_key(need: dict[str, Any]) -> tuple[int, int, str]:
        tier_rank = _TIER_ORDER.index(_need_tier(need))
        recommended_rank = 0 if need.get("recommended") else 1
        return (tier_rank, recommended_rank, str(need.get("id", "")))

    return sorted(needs, key=sort_key)


def _need_footprint(need_id: str) -> tuple[str, int, int]:
    """Return (profile, pattern_count, external_service_count) for a single need."""
    plan = resolve_install_plan(needs=[need_id])
    return (plan.profile, len(plan.patterns), len(plan.tech_extras))


def _plan_to_json(plan: InstallPlan) -> dict[str, object]:
    return {
        "profile": plan.profile,
        "needs": list(plan.needs),
        "patterns": list(plan.patterns),
        "memory_capabilities": list(plan.memory_capabilities),
        "artifacts": list(plan.artifacts),
        "extra_artifacts": list(plan.extra_artifacts),
        "tech_extras": list(plan.tech_extras),
        "pip_target": plan.pip_target,
        "pip_command": plan.pip_command,
        "warnings": list(plan.warnings),
    }


def _print_plan(plan: InstallPlan) -> None:
    console.print(f"[bold]Install plan[/bold] → profile [cyan]{plan.profile}[/cyan]")
    if plan.needs:
        console.print(f"  [bold]needs[/bold]: {', '.join(plan.needs)}")
    console.print(f"  [bold]patterns[/bold]: {', '.join(plan.patterns) or '-'}")
    console.print(
        f"  [bold]footprint[/bold]: {len(plan.patterns)} pattern(s) · "
        f"{len(plan.tech_extras)} external service(s)"
    )
    if plan.memory_capabilities:
        console.print(f"  [bold]memory[/bold]: {', '.join(plan.memory_capabilities)}")
    if plan.extra_artifacts:
        console.print(f"  [bold]extra artifacts[/bold]: {', '.join(plan.extra_artifacts)}")
    console.print(f"  [bold]tech extras[/bold]: {', '.join(plan.tech_extras) or 'none (core only)'}")
    console.print(f"  [bold]install[/bold]: [green]{plan.pip_command}[/green]")
    for warning in plan.warnings:
        console.print(f"  [yellow]![/yellow] {warning}")


def _install_manifest_text(plan: InstallPlan, project_name: str | None, task_id: str) -> str:
    def _flow(items: tuple[str, ...]) -> str:
        return "[" + ", ".join(items) + "]"

    return "\n".join([
        '$schema: "grimoire-agentic-standard-install-manifest/v1"',
        "metadata:",
        f"  project: {json.dumps(project_name or '')}",
        '  generated_by: "grimoire standard init"',
        f"  task_id: {json.dumps(task_id)}",
        f"  profile: {plan.profile}",
        "selection:",
        f"  needs: {_flow(plan.needs)}",
        f"  patterns: {_flow(plan.patterns)}",
        f"  memory_capabilities: {_flow(plan.memory_capabilities)}",
        "install:",
        f"  tech_extras: {_flow(plan.tech_extras)}",
        f"  pip_target: {json.dumps(plan.pip_target)}",
        f"  pip_command: {json.dumps(plan.pip_command)}",
        "",
    ])


def _interactive_install_plan(project_name: str | None) -> tuple[InstallPlan, str | None]:
    catalog = load_needs_catalog()
    needs = _sorted_needs([n for n in catalog.get("needs", []) if isinstance(n, dict) and "id" in n])
    console.print("[bold]Grimoire custom install[/bold] — start small, pick the needs that match your project.\n")
    table = Table(title="Available needs (Essentials first)")
    table.add_column("#")
    table.add_column(" ", justify="center")
    table.add_column("Tier")
    table.add_column("Need")
    table.add_column("Profile")
    table.add_column("Patterns", justify="right")
    last_tier: str | None = None
    default_choice = "1"
    for index, need in enumerate(needs, start=1):
        tier = _need_tier(need)
        profile, n_patterns, _services = _need_footprint(str(need["id"]))
        if need.get("recommended"):
            default_choice = str(index)
        table.add_row(
            str(index),
            "[green]▶[/green]" if need.get("recommended") else "",
            _TIER_HEADING[tier] if tier != last_tier else "",
            str(need.get("label", need["id"])),
            str(profile),
            str(n_patterns),
        )
        last_tier = tier
    console.print(table)

    if project_name is None:
        project_name = typer.prompt("Project name", default="").strip() or None
    raw = typer.prompt(
        "Select needs (comma-separated numbers or ids; Enter = recommended)",
        default=default_choice,
    )
    selected: list[str] = []
    for token in str(raw).replace(",", " ").split():
        token = token.strip()
        if token.isdigit():
            position = int(token)
            if 1 <= position <= len(needs):
                selected.append(str(needs[position - 1]["id"]))
        elif token:
            selected.append(token)
    plan = resolve_install_plan(needs=selected)
    return plan, project_name


@standard_app.command("needs")
def list_needs(
    ctx: typer.Context,
    explain: bool = typer.Option(False, "--explain", help="Also show the patterns each need activates."),
) -> None:
    """List user-facing project needs that drive a custom install (grouped by tier)."""
    catalog = load_needs_catalog()
    needs = _sorted_needs([n for n in catalog.get("needs", []) if isinstance(n, dict) and "id" in n])
    if _get_fmt(ctx) == "json":
        payload: list[dict[str, object]] = []
        for need in needs:
            profile, n_patterns, n_services = _need_footprint(str(need["id"]))
            payload.append({
                "id": need["id"],
                "label": need.get("label", need["id"]),
                "tier": _need_tier(need),
                "recommended": bool(need.get("recommended", False)),
                "recommended_profile": need.get("recommended_profile"),
                "patterns": list(need.get("patterns", [])),
                "memory_capabilities": list(need.get("memory_capabilities", [])),
                "footprint": {"profile": profile, "patterns": n_patterns, "services": n_services},
                "rationale": need.get("rationale", ""),
            })
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    table = Table(title="Agentic standard needs — pick by intent, grow later")
    table.add_column(" ", justify="center")
    table.add_column("Tier")
    table.add_column("ID")
    table.add_column("Need")
    table.add_column("Profile")
    table.add_column("Patterns", justify="right")
    table.add_column("Services", justify="right")
    last_tier: str | None = None
    for need in needs:
        tier = _need_tier(need)
        profile, n_patterns, n_services = _need_footprint(str(need["id"]))
        table.add_row(
            "[green]▶[/green]" if need.get("recommended") else "",
            _TIER_HEADING[tier] if tier != last_tier else "",
            str(need["id"]),
            str(need.get("label", need["id"])),
            str(profile),
            str(n_patterns),
            str(n_services) if n_services else "—",
        )
        last_tier = tier
    console.print(table)
    console.print(
        "[dim]▶ = recommended starting point. Preview a need with[/dim] "
        "[cyan]grimoire standard plan --needs <id>[/cyan]"
    )
    if explain:
        detail = Table(title="What each need activates")
        detail.add_column("Need")
        detail.add_column("Patterns")
        for need in needs:
            detail.add_row(
                str(need["id"]),
                ", ".join(str(p) for p in need.get("patterns", [])) or "—",
            )
        console.print(detail)
    else:
        console.print(
            "[dim]Add[/dim] [cyan]--explain[/cyan] [dim]to see the patterns behind each need.[/dim]"
        )


@standard_app.command("plan")
def plan_install(
    ctx: typer.Context,
    needs: list[str] | None = typer.Option(None, "--needs", "-n", help="Need id(s). Repeatable or comma-separated."),  # noqa: B008
    pattern: list[str] | None = typer.Option(None, "--pattern", help="Pattern id(s). Repeatable or comma-separated."),  # noqa: B008
    memory: list[str] | None = typer.Option(None, "--memory", help="Memory capability id(s): semantic-memory, graph-memory, hot-memory, legacy-migration."),  # noqa: B008
    profile: str | None = typer.Option(None, "--profile", "-p", help="Force a profile floor instead of the resolved one."),
) -> None:
    """Preview the resolved install plan (profile, patterns, tech extras) without writing files."""
    plan = resolve_install_plan(
        needs=_split_csv(needs),
        patterns=_split_csv(pattern),
        memory_capabilities=_split_csv(memory),
        profile=profile,
    )
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps(_plan_to_json(plan), indent=2, ensure_ascii=False))
        return
    _print_plan(plan)


@standard_app.command("doctor")
def doctor(
    ctx: typer.Context,
    needs: list[str] | None = typer.Option(None, "--needs", "-n", help="Only check extras implied by these needs."),  # noqa: B008
    pattern: list[str] | None = typer.Option(None, "--pattern", help="Only check extras implied by these patterns."),  # noqa: B008
) -> None:
    """Check whether selected technology extras are importable and report safe degradation."""
    import importlib.util

    capability_map = load_capability_map()
    tech_meta = capability_map.get("tech_extras", {})
    tech_meta = tech_meta if isinstance(tech_meta, dict) else {}

    if _split_csv(needs) or _split_csv(pattern):
        plan = resolve_install_plan(needs=_split_csv(needs), patterns=_split_csv(pattern))
        extras = list(plan.tech_extras)
    else:
        extras = list(tech_meta)

    rows: list[dict[str, object]] = []
    all_ok = True
    for extra in extras:
        probes = _EXTRA_IMPORT_PROBES.get(extra, (extra,))
        missing = [m for m in probes if importlib.util.find_spec(m) is None]
        available = not missing
        if not available:
            all_ok = False
        meta = tech_meta.get(extra, {}) if isinstance(tech_meta.get(extra), dict) else {}
        rows.append({
            "extra": extra,
            "available": available,
            "missing_modules": missing,
            "role": str(meta.get("role", "")),
            "degrades_to": str(meta.get("degrades_to", "")),
        })

    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps({"ok": all_ok, "extras": rows}, indent=2, ensure_ascii=False))
        return

    table = Table(title="Grimoire tech extras doctor")
    table.add_column("Extra")
    table.add_column("Available")
    table.add_column("Install")
    table.add_column("Degrades to")
    for row in rows:
        available = bool(row["available"])
        table.add_row(
            str(row["extra"]),
            "[green]yes[/green]" if available else "[yellow]no[/yellow]",
            "-" if available else f"pip install 'grimoire-kit[{row['extra']}]'",
            "-" if available else str(row["degrades_to"]),
        )
    console.print(table)
    if not all_ok:
        console.print("[yellow]Some extras are absent; affected patterns degrade safely (no durable data loss).[/yellow]")


@standard_app.command("init")
def init_profile(
    ctx: typer.Context,
    project_root: Path = typer.Argument(Path(), help="Target project root."),  # noqa: B008
    profile: str | None = typer.Option(None, "--profile", "-p", help="Profile to generate. Defaults to the minimal 'starter' profile, or the resolved profile when --needs/--pattern are used."),
    needs: list[str] | None = typer.Option(None, "--needs", "-n", help="Need id(s) for a custom install. Repeatable or comma-separated."),  # noqa: B008
    pattern: list[str] | None = typer.Option(None, "--pattern", help="Pattern id(s) to include. Repeatable or comma-separated."),  # noqa: B008
    memory: list[str] | None = typer.Option(None, "--memory", help="Memory capability id(s): semantic-memory, graph-memory, hot-memory, legacy-migration."),  # noqa: B008
    interactive: bool = typer.Option(False, "--interactive", "-i", help="Run the guided needs wizard."),
    install_extras: bool = typer.Option(False, "--install-extras", help="Run pip to install the resolved technology extras."),
    task_id: str = typer.Option("bootstrap", "--task-id", help="Evidence task id for generated task artifacts."),
    project_name: str | None = typer.Option(None, "--project-name", help="Project name written into generated artifacts."),
    provider: list[str] | None = typer.Option(None, "--provider", help="Provider to enable. Can be repeated."),  # noqa: B008
    providers: str | None = typer.Option(None, "--providers", help="Comma-separated providers to enable."),
    provider_policy: str = typer.Option("hosted-safe", "--provider-policy", help="hosted-safe | local-first | mixed."),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing standard artifacts."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show generated paths without writing files."),
) -> None:
    """Generate standard-aware artifacts for a project.

    With --needs/--pattern/--interactive, the profile, pattern set and technology
    extras are resolved from the needs catalog and only the relevant artifacts are
    scaffolded; an install-manifest.yaml records the selection.
    """
    provider_ids: list[str] = []
    if provider:
        provider_ids.extend(provider)
    if providers:
        provider_ids.append(providers)

    need_ids = _split_csv(needs)
    pattern_ids = _split_csv(pattern)
    memory_ids = _split_csv(memory)
    plan: InstallPlan | None = None

    if interactive:
        plan, project_name = _interactive_install_plan(project_name)
    elif need_ids or pattern_ids or memory_ids:
        plan = resolve_install_plan(
            needs=need_ids,
            patterns=pattern_ids,
            memory_capabilities=memory_ids,
            profile=profile,
        )

    if plan is not None:
        resolved_profile = plan.profile
        extra_artifacts = list(plan.extra_artifacts)
    else:
        resolved_profile = profile or "starter"
        extra_artifacts = []
        if profile is None and not dry_run and _get_fmt(ctx) != "json":
            console.print(
                "[dim]No --needs/--profile given → scaffolding the minimal[/dim] "
                "[cyan]starter[/cyan] [dim]profile. Run[/dim] "
                "[cyan]grimoire standard needs[/cyan] [dim]to pick by need, or[/dim] "
                "[cyan]grimoire standard init --interactive[/cyan][dim].[/dim]"
            )

    result = setup_standard_profile(
        project_root,
        profile_id=resolved_profile,
        task_id=task_id,
        project_name=project_name,
        provider_ids=provider_ids,
        provider_policy=provider_policy,
        extra_artifacts=extra_artifacts,
        force=force,
        dry_run=dry_run,
    )

    manifest_rel = Path("_grimoire/standard/install-manifest.yaml")
    if plan is not None and not dry_run:
        manifest_path = result.project_root / manifest_rel
        if manifest_path.exists() and not force:
            result.skipped.append(manifest_rel)
        else:
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(_install_manifest_text(plan, project_name, task_id), encoding="utf-8")
            result.written.append(manifest_rel)
    elif plan is not None and dry_run:
        result.written.append(manifest_rel)

    installed_extras_ok: bool | None = None
    if plan is not None and install_extras and plan.tech_extras and not dry_run:
        installed_extras_ok = _run_pip_install(plan.pip_target)

    if _get_fmt(ctx) == "json":
        payload: dict[str, object] = {
            "ok": True,
            "profile": result.profile,
            "project_root": str(result.project_root),
            "dry_run": result.dry_run,
            "providers": provider_ids,
            "provider_policy": provider_policy,
            "written": _paths(result.written),
            "skipped": _paths(result.skipped),
        }
        if plan is not None:
            payload["plan"] = _plan_to_json(plan)
            payload["install_extras_ran"] = installed_extras_ok
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    action = "Would write" if dry_run else "Written"
    console.print(f"[green]{action}[/green] profile [bold]{result.profile}[/bold] in {result.project_root}")
    for path in result.written:
        console.print(f"  [green]✓[/green] {path}")
    for path in result.skipped:
        console.print(f"  [yellow]↷[/yellow] {path} already exists; use --force to overwrite")
    if provider_ids:
        console.print(f"  [cyan]providers[/cyan] {', '.join(provider_ids)} ({provider_policy})")
    if plan is not None:
        console.print()
        _print_plan(plan)
        if plan.tech_extras:
            if installed_extras_ok is True:
                console.print("  [green]✓[/green] technology extras installed")
            elif installed_extras_ok is False:
                console.print("  [red]✗[/red] extra install failed; run the command above manually")
            else:
                console.print(f"  [cyan]next[/cyan] install technology extras: [green]{plan.pip_command}[/green]")


def _run_pip_install(pip_target: str) -> bool:
    import subprocess
    import sys

    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pip", "install", pip_target],
            check=False,
        )
    except OSError:
        return False
    return completed.returncode == 0


@standard_app.command("verify")
def verify_profile(
    ctx: typer.Context,
    project_root: Path = typer.Argument(Path(), help="Target project root."),  # noqa: B008
    profile: str | None = typer.Option(None, "--profile", "-p", help="Expected profile. Defaults to generated manifest."),
    task_id: str = typer.Option("bootstrap", "--task-id", help="Evidence task id to verify."),
) -> None:
    """Verify generated standard-aware artifacts."""
    result = verify_standard_profile(project_root, profile_id=profile, task_id=task_id)

    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps({
            "ok": result.ok,
            "profile": result.profile,
            "project_root": str(result.project_root),
            "present": _paths(result.present),
            "missing": _paths(result.missing),
            "invalid_yaml": _paths(result.invalid_yaml),
            "warnings": result.warnings,
            "checks": _checks(result),
            "error_count": result.error_count,
            "warning_count": result.warning_count,
        }, indent=2, ensure_ascii=False))
        raise typer.Exit(0 if result.ok else 1)

    status = "[green]OK[/green]" if result.ok else "[red]FAIL[/red]"
    console.print(
        f"{status} agentic standard profile [bold]{result.profile}[/bold] in {result.project_root} "
        f"([red]{result.error_count} error(s)[/red], [yellow]{result.warning_count} warning(s)[/yellow])"
    )
    for path in result.present:
        console.print(f"  [green]✓[/green] {path}")
    for path in result.missing:
        console.print(f"  [red]✗[/red] missing {path}")
    for path in result.invalid_yaml:
        console.print(f"  [red]✗[/red] invalid YAML {path}")
    for check in result.checks:
        color = "red" if check.severity == "error" else "yellow"
        path_text = f" ({check.path})" if check.path else ""
        console.print(f"  [{color}]![/{color}] {check.id}{path_text}: {check.message}")
    for warning in result.warnings:
        console.print(f"  [yellow]![/yellow] {warning}")
    raise typer.Exit(0 if result.ok else 1)


@standard_app.command("audit")
def audit_profile(
    ctx: typer.Context,
    project_root: Path = typer.Argument(Path(), help="Target project root."),  # noqa: B008
    profile: str | None = typer.Option(None, "--profile", "-p", help="Expected profile. Defaults to generated manifest."),
    task_id: str = typer.Option("bootstrap", "--task-id", help="Evidence task id to audit."),
    markdown: bool = typer.Option(False, "--markdown", help="Emit a markdown audit report."),
) -> None:
    """Audit generated standard artifacts and summarize remaining gaps."""
    result = verify_standard_profile(project_root, profile_id=profile, task_id=task_id)

    if markdown:
        typer.echo(_audit_markdown(result), nl=False)
        raise typer.Exit(0 if result.ok else 1)

    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps({
            "ok": result.ok,
            "profile": result.profile,
            "project_root": str(result.project_root),
            "error_count": result.error_count,
            "warning_count": result.warning_count,
            "present": _paths(result.present),
            "missing": _paths(result.missing),
            "invalid_yaml": _paths(result.invalid_yaml),
            "checks": _checks(result),
        }, indent=2, ensure_ascii=False))
        raise typer.Exit(0 if result.ok else 1)

    console.print(_audit_markdown(result))
    raise typer.Exit(0 if result.ok else 1)


@board_app.command("verify")
def board_verify(
    ctx: typer.Context,
    project_root: Path = typer.Argument(Path(), help="Target project root."),  # noqa: B008
    profile: str | None = typer.Option(None, "--profile", "-p", help="Expected profile. Defaults to generated manifest."),
    task_id: str = typer.Option("bootstrap", "--task-id", help="Evidence task id to verify."),
) -> None:
    """Verify the standard task board."""
    result = verify_standard_profile(project_root, profile_id=profile, task_id=task_id)
    _echo_filtered_verification(ctx, result, ("board.",), "standard task board")


@memory_app.command("verify")
def memory_verify(
    ctx: typer.Context,
    project_root: Path = typer.Argument(Path(), help="Target project root."),  # noqa: B008
    profile: str | None = typer.Option(None, "--profile", "-p", help="Expected profile. Defaults to generated manifest."),
    task_id: str = typer.Option("bootstrap", "--task-id", help="Evidence task id to verify."),
) -> None:
    """Verify the standard memory policy."""
    result = verify_standard_profile(project_root, profile_id=profile, task_id=task_id)
    _echo_filtered_verification(ctx, result, ("memory.",), "standard memory policy")


@context_app.command("build")
def context_build(
    ctx: typer.Context,
    project_root: Path = typer.Argument(Path(), help="Target project root."),  # noqa: B008
    task_id: str = typer.Option("bootstrap", "--task-id", help="Task id for the context bundle."),
    profile: str | None = typer.Option(None, "--profile", "-p", help="Expected profile. Defaults to generated manifest."),
) -> None:
    """Build a deterministic context bundle."""
    artifact = build_context_bundle(project_root, task_id=task_id, profile_id=profile)
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps(_artifact_json(artifact), indent=2, ensure_ascii=False))
        return
    console.print(f"[green]✓[/green] context bundle written to {artifact.path}")


@context_app.command("verify")
def context_verify(
    ctx: typer.Context,
    project_root: Path = typer.Argument(Path(), help="Target project root."),  # noqa: B008
    profile: str | None = typer.Option(None, "--profile", "-p", help="Expected profile. Defaults to generated manifest."),
    task_id: str = typer.Option("bootstrap", "--task-id", help="Evidence task id to verify."),
) -> None:
    """Verify the standard context contract."""
    result = verify_standard_profile(project_root, profile_id=profile, task_id=task_id)
    _echo_filtered_verification(ctx, result, ("context.",), "standard context contract")


@decision_app.command("trace")
def decision_trace(
    ctx: typer.Context,
    project_root: Path = typer.Argument(Path(), help="Target project root."),  # noqa: B008
    task_id: str = typer.Option("bootstrap", "--task-id", help="Task id for the decision trace."),
    profile: str | None = typer.Option(None, "--profile", "-p", help="Expected profile. Defaults to generated manifest."),
) -> None:
    """Build a decision trace for a task."""
    artifact = build_decision_trace(project_root, task_id=task_id, profile_id=profile)
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps(_artifact_json(artifact), indent=2, ensure_ascii=False))
        return
    console.print(f"[green]✓[/green] decision trace written to {artifact.path}")


@decision_app.command("explain")
def decision_explain(
    ctx: typer.Context,
    project_root: Path = typer.Argument(Path(), help="Target project root."),  # noqa: B008
    task_id: str = typer.Option("bootstrap", "--task-id", help="Task id for the decision trace."),
    profile: str | None = typer.Option(None, "--profile", "-p", help="Expected profile. Defaults to generated manifest."),
) -> None:
    """Explain decision types by building the trace skeleton."""
    artifact = build_decision_trace(project_root, task_id=task_id, profile_id=profile)
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps(_artifact_json(artifact), indent=2, ensure_ascii=False))
        return
    records = artifact.data.get("records", [])
    console.print(f"[green]✓[/green] {len(records) if isinstance(records, list) else 0} decision record(s) explained in {artifact.path}")


@rules_app.command("verify")
def rules_verify(
    ctx: typer.Context,
    project_root: Path = typer.Argument(Path(), help="Target project root."),  # noqa: B008
    profile: str | None = typer.Option(None, "--profile", "-p", help="Expected profile. Defaults to generated manifest."),
    task_id: str = typer.Option("bootstrap", "--task-id", help="Evidence task id to verify."),
) -> None:
    """Verify standard rule packs."""
    result = verify_standard_profile(project_root, profile_id=profile, task_id=task_id)
    _echo_filtered_verification(ctx, result, ("rules.",), "standard rule packs")


@hooks_app.command("verify")
def hooks_verify(
    ctx: typer.Context,
    project_root: Path = typer.Argument(Path(), help="Target project root."),  # noqa: B008
    profile: str | None = typer.Option(None, "--profile", "-p", help="Expected profile. Defaults to generated manifest."),
    task_id: str = typer.Option("bootstrap", "--task-id", help="Evidence task id to verify."),
) -> None:
    """Verify standard hook registry."""
    result = verify_standard_profile(project_root, profile_id=profile, task_id=task_id)
    _echo_filtered_verification(ctx, result, ("hooks.",), "standard hook registry")


@hooks_app.command("simulate")
def hooks_simulate(
    ctx: typer.Context,
    project_root: Path = typer.Argument(Path(), help="Target project root."),  # noqa: B008
    task_id: str = typer.Option("bootstrap", "--task-id", help="Task id for hook simulation."),
    phase: str | None = typer.Option(None, "--phase", help="Optional hook phase to simulate."),
    profile: str | None = typer.Option(None, "--profile", "-p", help="Expected profile. Defaults to generated manifest."),
) -> None:
    """Simulate hooks without executing external actions."""
    artifact = simulate_standard_hooks(project_root, task_id=task_id, phase=phase, profile_id=profile)
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps(_artifact_json(artifact), indent=2, ensure_ascii=False))
        return
    console.print(f"[green]✓[/green] hook simulation written to {artifact.path}")


@gate_app.command("check")
def gate_check(
    ctx: typer.Context,
    project_root: Path = typer.Argument(Path(), help="Target project root."),  # noqa: B008
    task_id: str = typer.Option("bootstrap", "--task-id", help="Task id to evaluate."),
    target_state: str | None = typer.Option(None, "--target-state", help="Optional target lifecycle state."),
    profile: str | None = typer.Option(None, "--profile", "-p", help="Expected profile. Defaults to generated manifest."),
    strict: bool = typer.Option(False, "--strict", help="Use exit code 2 when governed/production gates fail."),
) -> None:
    """Check standard evidence gates for a task."""
    result = check_evidence_gates(project_root, task_id=task_id, target_state=target_state, profile_id=profile)
    payload = {
        "ok": result.ok,
        "task_id": result.task_id,
        "profile": result.profile,
        "state": result.state,
        "missing": list(result.missing),
        "checks": [
            {
                "id": check.id,
                "severity": check.severity,
                "message": check.message,
                "path": str(check.path) if check.path else None,
            }
            for check in result.checks
        ],
        "strict": strict,
    }
    strict_failure = strict and result.profile in {"governed", "production"} and not result.ok
    exit_code = 2 if strict_failure else 0 if result.ok else 1
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        raise typer.Exit(exit_code)
    status = "[green]OK[/green]" if result.ok else "[red]FAIL[/red]"
    console.print(f"{status} evidence gates for task {result.task_id}")
    for missing in result.missing:
        console.print(f"  [red]✗[/red] missing {missing}")
    raise typer.Exit(exit_code)


@events_app.command("audit")
def events_audit(
    ctx: typer.Context,
    project_root: Path = typer.Argument(Path(), help="Target project root."),  # noqa: B008
) -> None:
    """Audit standard runtime journal events."""
    result = audit_runtime_events(project_root)
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
        raise typer.Exit(0 if result["ok"] else 1)
    status = "[green]OK[/green]" if result["ok"] else "[red]FAIL[/red]"
    console.print(f"{status} runtime journal {result['path']} ({result['event_count']} event(s))")
    raise typer.Exit(0 if result["ok"] else 1)


@pattern_app.command("list")
def pattern_list(
    ctx: typer.Context,
    project_root: Path = typer.Argument(Path(), help="Target project root."),  # noqa: B008
    category: str | None = typer.Option(None, "--category", "-c", help="Optional pattern category filter."),
) -> None:
    """List executable standard patterns."""
    patterns = list_standard_patterns(project_root, category=category)
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps(list(patterns), indent=2, ensure_ascii=False))
        return
    table = Table(title="Agentic standard patterns")
    table.add_column("ID")
    table.add_column("Category")
    table.add_column("Maturity")
    table.add_column("Intent")
    for pattern in patterns:
        table.add_row(
            str(pattern.get("id", "")),
            str(pattern.get("category", "")),
            str(pattern.get("maturity", "")),
            str(pattern.get("intent", "")),
        )
    console.print(table)


@pattern_app.command("show")
def pattern_show(
    ctx: typer.Context,
    pattern_id: str = typer.Argument(..., help="Pattern id to show."),
    project_root: Path = typer.Argument(Path(), help="Target project root."),  # noqa: B008
) -> None:
    """Show one executable standard pattern."""
    pattern = show_standard_pattern(project_root, pattern_id)
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps(pattern, indent=2, ensure_ascii=False))
        return
    console.print(json.dumps(pattern, indent=2, ensure_ascii=False))


@knowledge_app.command("index")
def knowledge_index(
    ctx: typer.Context,
    project_root: Path = typer.Argument(Path(), help="Target project root."),  # noqa: B008
    task_id: str = typer.Option("bootstrap", "--task-id", help="Task id for the knowledge index."),
    profile: str | None = typer.Option(None, "--profile", "-p", help="Expected profile. Defaults to generated manifest."),
) -> None:
    """Build a standard knowledge index manifest."""
    artifact = build_knowledge_index(project_root, task_id=task_id, profile_id=profile)
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps(_artifact_json(artifact), indent=2, ensure_ascii=False))
        return
    console.print(f"[green]✓[/green] knowledge index written to {artifact.path}")


@knowledge_app.command("graph")
def knowledge_graph(
    ctx: typer.Context,
    project_root: Path = typer.Argument(Path(), help="Target project root."),  # noqa: B008
    task_id: str = typer.Option("bootstrap", "--task-id", help="Task id for the knowledge graph."),
    profile: str | None = typer.Option(None, "--profile", "-p", help="Expected profile. Defaults to generated manifest."),
    source: list[str] | None = typer.Option(None, "--source", help="Enabled knowledge source id to include. Can be repeated."),  # noqa: B008
) -> None:
    """Build a local doc-to-graph knowledge graph."""
    artifact = build_knowledge_graph(project_root, task_id=task_id, profile_id=profile, source_ids=source or ())
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps(_artifact_json(artifact), indent=2, ensure_ascii=False))
        return
    nodes = artifact.data.get("nodes", [])
    node_count = len(nodes) if isinstance(nodes, list) else 0
    console.print(f"[green]✓[/green] knowledge graph with {node_count} node(s) written to {artifact.path}")


@knowledge_app.command("verify")
def knowledge_verify(
    ctx: typer.Context,
    project_root: Path = typer.Argument(Path(), help="Target project root."),  # noqa: B008
    task_id: str = typer.Option("bootstrap", "--task-id", help="Task id for the knowledge index."),
) -> None:
    """Verify a standard knowledge index manifest."""
    result = verify_knowledge_index(project_root, task_id=task_id)
    _echo_filtered_verification(ctx, result, ("knowledge_index.",), "standard knowledge index")


@standard_app.command("score")
def score(
    ctx: typer.Context,
    project_root: Path = typer.Argument(Path(), help="Target project root."),  # noqa: B008
    task_id: str = typer.Option("bootstrap", "--task-id", help="Task id to score."),
    profile: str | None = typer.Option(None, "--profile", "-p", help="Expected profile. Defaults to generated manifest."),
) -> None:
    """Calculate and persist a standard compliance score."""
    result = calculate_compliance_score(project_root, task_id=task_id, profile_id=profile)
    payload = {
        "ok": result.ok,
        "profile": result.profile,
        "score": result.score,
        "threshold": result.threshold,
        "warnings": result.warnings,
        "errors": result.errors,
        "dimensions": result.dimensions,
        "output_path": str(result.output_path),
    }
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        raise typer.Exit(0 if result.ok else 1)
    status = "[green]OK[/green]" if result.ok else "[red]FAIL[/red]"
    console.print(f"{status} compliance score {result.score}/{result.threshold} written to {result.output_path}")
    table = Table(title="Compliance dimensions")
    table.add_column("Dimension")
    table.add_column("Score")
    table.add_column("Weight")
    table.add_column("Issues")
    for dimension, values in result.dimensions.items():
        table.add_row(
            dimension,
            f"{values['percentage']}%",
            str(values["weight"]),
            f"{values['errors']} error(s), {values['warnings']} warning(s)",
        )
    console.print(table)
    raise typer.Exit(0 if result.ok else 1)


@standard_app.command("fix")
def fix(
    ctx: typer.Context,
    project_root: Path = typer.Argument(Path(), help="Target project root."),  # noqa: B008
    task_id: str = typer.Option("bootstrap", "--task-id", help="Task id to inspect."),
    profile: str | None = typer.Option(None, "--profile", "-p", help="Expected profile. Defaults to generated manifest."),
    dry_run: bool = typer.Option(True, "--dry-run/--apply", help="Plan remediations or apply safe fixes."),
) -> None:
    """Plan remediation actions or apply safe non-destructive fixes."""
    actions = propose_remediation_actions(project_root, task_id=task_id, profile_id=profile)
    apply_result: StandardRemediationApplyResult | None = None
    if not dry_run:
        apply_result = apply_remediation_actions(project_root, task_id=task_id, profile_id=profile)
    payload: dict[str, object] = {"dry_run": dry_run, "applied": False, "actions": _remediation_json(actions)}
    if apply_result is not None:
        payload = {"dry_run": False, **_remediation_apply_json(apply_result)}
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    action_label = "Would apply" if dry_run else "Applied safe subset of"
    console.print(f"[cyan]{action_label}[/cyan] {len(actions)} remediation action(s)")
    if apply_result is not None:
        for path in apply_result.written:
            console.print(f"  [green]✓[/green] wrote {path}")
        for skipped in apply_result.skipped:
            console.print(f"  [yellow]↷[/yellow] skipped {skipped}")
    for action in actions:
        path_text = f" ({action.path})" if action.path else ""
        console.print(f"  [yellow]![/yellow] {action.action}: {action.check_id}{path_text}")
