"""CLI commands for the agentic standard bridge."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from grimoire.core.agentic_standard import (
    StandardVerificationResult,
    detect_standard_providers,
    list_profiles,
    setup_standard_profile,
    verify_standard_profile,
)

standard_app = typer.Typer(
    help="Apply and verify the agentic standard bridge.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
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


def _provider_detection_json() -> list[dict[str, object]]:
    return [
        {
            "id": provider.id,
            "available": provider.available,
            "signals": list(provider.signals),
            "note": provider.note,
        }
        for provider in detect_standard_providers()
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
        typer.echo(json.dumps(_provider_detection_json(), indent=2, ensure_ascii=False))
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
