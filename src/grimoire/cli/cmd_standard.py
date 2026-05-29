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
    propose_remediation_actions,
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


def _filtered_result(result: StandardVerificationResult, prefixes: tuple[str, ...]) -> dict[str, Any]:
    checks = [
        check
        for check in result.checks
        if check.id.startswith(prefixes)
    ]
    return {
        "ok": result.ok and not any(check.is_error for check in checks),
        "profile": result.profile,
        "project_root": str(result.project_root),
        "checks": [
            {
                "id": check.id,
                "severity": check.severity,
                "message": check.message,
                "path": str(check.path) if check.path else None,
            }
            for check in checks
        ],
        "error_count": sum(1 for check in checks if check.is_error),
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


@standard_app.command("init")
def init_profile(
    ctx: typer.Context,
    project_root: Path = typer.Argument(Path(), help="Target project root."),  # noqa: B008
    profile: str = typer.Option("orchestrated", "--profile", "-p", help="Profile to generate."),
    task_id: str = typer.Option("bootstrap", "--task-id", help="Evidence task id for generated task artifacts."),
    project_name: str | None = typer.Option(None, "--project-name", help="Project name written into generated artifacts."),
    provider: list[str] | None = typer.Option(None, "--provider", help="Provider to enable. Can be repeated."),  # noqa: B008
    providers: str | None = typer.Option(None, "--providers", help="Comma-separated providers to enable."),
    provider_policy: str = typer.Option("hosted-safe", "--provider-policy", help="hosted-safe | local-first | mixed."),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing standard artifacts."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show generated paths without writing files."),
) -> None:
    """Generate standard-aware artifacts for a project."""
    provider_ids: list[str] = []
    if provider:
        provider_ids.extend(provider)
    if providers:
        provider_ids.append(providers)
    result = setup_standard_profile(
        project_root,
        profile_id=profile,
        task_id=task_id,
        project_name=project_name,
        provider_ids=provider_ids,
        provider_policy=provider_policy,
        force=force,
        dry_run=dry_run,
    )

    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps({
            "ok": True,
            "profile": result.profile,
            "project_root": str(result.project_root),
            "dry_run": result.dry_run,
            "providers": provider_ids,
            "provider_policy": provider_policy,
            "written": _paths(result.written),
            "skipped": _paths(result.skipped),
        }, indent=2, ensure_ascii=False))
        return

    action = "Would write" if dry_run else "Written"
    console.print(f"[green]{action}[/green] profile [bold]{result.profile}[/bold] in {result.project_root}")
    for path in result.written:
        console.print(f"  [green]✓[/green] {path}")
    for path in result.skipped:
        console.print(f"  [yellow]↷[/yellow] {path} already exists; use --force to overwrite")
    if provider_ids:
        console.print(f"  [cyan]providers[/cyan] {', '.join(provider_ids)} ({provider_policy})")


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
