"""CLI commands for the agentic standard bridge."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from grimoire.core.agentic_standard import (
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


@standard_app.command("init")
def init_profile(
    ctx: typer.Context,
    project_root: Path = typer.Argument(Path(), help="Target project root."),
    profile: str = typer.Option("orchestrated", "--profile", "-p", help="Profile to generate."),
    task_id: str = typer.Option("bootstrap", "--task-id", help="Evidence task id for generated task artifacts."),
    project_name: str | None = typer.Option(None, "--project-name", help="Project name written into generated artifacts."),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing standard artifacts."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show generated paths without writing files."),
) -> None:
    """Generate standard-aware artifacts for a project."""
    result = setup_standard_profile(
        project_root,
        profile_id=profile,
        task_id=task_id,
        project_name=project_name,
        force=force,
        dry_run=dry_run,
    )

    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps({
            "ok": True,
            "profile": result.profile,
            "project_root": str(result.project_root),
            "dry_run": result.dry_run,
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


@standard_app.command("verify")
def verify_profile(
    ctx: typer.Context,
    project_root: Path = typer.Argument(Path(), help="Target project root."),
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
        }, indent=2, ensure_ascii=False))
        raise typer.Exit(0 if result.ok else 1)

    status = "[green]OK[/green]" if result.ok else "[red]FAIL[/red]"
    console.print(f"{status} agentic standard profile [bold]{result.profile}[/bold] in {result.project_root}")
    for path in result.present:
        console.print(f"  [green]✓[/green] {path}")
    for path in result.missing:
        console.print(f"  [red]✗[/red] missing {path}")
    for path in result.invalid_yaml:
        console.print(f"  [red]✗[/red] invalid YAML {path}")
    for warning in result.warnings:
        console.print(f"  [yellow]![/yellow] {warning}")
    raise typer.Exit(0 if result.ok else 1)
