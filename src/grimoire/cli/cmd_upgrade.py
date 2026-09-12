"""``grimoire upgrade`` — migrate a v2 project to v3 structure.

Detects a v2 project by the presence of ``project-context.yaml`` without
v3 markers. Generates the v3 config from the v2 config,
ensures the v3 directory layout, and preserves all memory files.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from ruamel.yaml.comments import CommentedMap

from grimoire.cli._shared import _log_operation, _status_spinner
from grimoire.tools._common import load_yaml, load_yaml_roundtrip, save_yaml

console = Console(stderr=True)

# ── Data Models ───────────────────────────────────────────────────────────────

@dataclass(slots=True)
class UpgradeAction:
    """A single planned migration action."""

    kind: str  # "create-dir", "generate-file", "move-dir", "skip"
    description: str
    target: str


@dataclass(slots=True)
class UpgradePlan:
    """All planned actions for the v2 → v3 migration."""

    source_version: str = "v2"
    target_version: str = "v3"
    actions: list[UpgradeAction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    already_v3: bool = False


# ── Detection ─────────────────────────────────────────────────────────────────

def detect_version(project_root: Path) -> str:
    """Return ``"v3"`` if ``project-context.yaml`` contains v3 markers,
    ``"v2"`` if it exists but is v2-style, or ``"unknown"``."""
    pctx = project_root / "project-context.yaml"
    if not pctx.exists():
        return "unknown"

    try:
        data = load_yaml(pctx)
    except OSError:
        return "unknown"

    if not isinstance(data, dict):
        return "unknown"

    # v3 has top-level 'grimoire' key with 'version'
    if "grimoire" in data and isinstance(data["grimoire"], dict):
        return "v3"

    # v2 has top-level keys like 'project', 'communication_language', etc.
    if "project" in data or "communication_language" in data:
        return "v2"

    return "unknown"


# ── Planning ──────────────────────────────────────────────────────────────────

def _extract_v2_config(project_root: Path) -> dict[str, Any]:
    """Extract usable fields from a v2 project-context.yaml."""
    data = load_yaml(project_root / "project-context.yaml")
    if not isinstance(data, dict):
        return {}
    return data


def plan_upgrade(project_root: Path) -> UpgradePlan:
    """Analyze the project and plan the migration."""
    plan = UpgradePlan()
    version = detect_version(project_root)

    if version == "v3":
        plan.already_v3 = True
        return plan

    if version == "unknown":
        plan.warnings.append(
            "No project-context.yaml found or unrecognizable format."
        )
        return plan

    # v2 → v3 migration plan
    _extract_v2_config(project_root)  # validate readable

    # 1. Generate grimoire section in project-context.yaml
    plan.actions.append(UpgradeAction(
        kind="generate-file",
        description="Add 'grimoire' section to project-context.yaml (v3 config)",
        target="project-context.yaml",
    ))

    # 2. Ensure v3 directories
    for d in ("_grimoire", "_grimoire/_memory", "_grimoire-output",
              "_grimoire/_config", "_grimoire/_config/agents"):
        dp = project_root / d
        if not dp.is_dir():
            plan.actions.append(UpgradeAction(
                kind="create-dir",
                description=f"Create directory: {d}/",
                target=d,
            ))

    # 3. Warn about orphan files
    old_dirs = ["agents", "tasks", "workflows"]
    for od in old_dirs:
        odp = project_root / od
        if odp.is_dir():
            plan.warnings.append(
                f"Top-level '{od}/' directory exists — may need manual review."
            )

    return plan


# ── Execution ─────────────────────────────────────────────────────────────────

def _generate_v3_section(v2_data: dict[str, Any]) -> dict[str, Any]:
    """Build the 'grimoire' section from v2 config data."""
    project_name = v2_data.get("project", "unnamed")
    if isinstance(project_name, dict):
        project_name = project_name.get("name", "unnamed")

    return {
        "grimoire": {
            "version": "3.0",
            "migrated_from": "v2",
        },
        "project": {
            "name": project_name,
        },
        "agents": {
            "archetype": "minimal",
        },
        "memory": {
            "backend": "auto",
        },
    }


def _merge_v3_section(existing: CommentedMap, v3_section: dict[str, Any]) -> None:
    """Merge the generated v3 section into *existing*, preserving every
    comment already in the file — only the migrated keys' values change.

    Two situations need care beyond a plain ``dict.update``:

    - the existing key already holds a mapping (e.g. a v2 ``agents:`` block
      with its own inline comments): merge the new sub-keys into it *in
      place* instead of replacing the whole node. A wholesale replace has
      nothing to carry over — plain dicts have no comment metadata — so it
      would silently drop every comment attached inside that node.
    - the existing key holds a scalar being replaced by a mapping (e.g. v2
      ``project: "name"  # display name``): the scalar's trailing comment
      (its own end-of-line comment, plus every blank line/comment up to the
      next key) is reattached to the last key of the new mapping instead of
      being dropped. This also fixes ruamel's emitter otherwise printing
      that comment *before* the new multi-line value instead of after it.

    See grimoire-kit#430.
    """
    for key, new_value in v3_section.items():
        old_value = existing.get(key)
        if isinstance(old_value, CommentedMap) and isinstance(new_value, dict):
            old_value.update(new_value)
            continue

        old_entry = existing.ca.items.pop(key, None)
        if isinstance(new_value, dict) and old_entry is not None and old_entry[2] is not None:
            new_value = CommentedMap(new_value)
            sub_keys = list(new_value.keys())
            if sub_keys:
                new_value.ca.items[sub_keys[-1]] = [None, None, old_entry[2], None]
                old_entry = None
        existing[key] = new_value
        if old_entry is not None:
            # Nowhere safer to carry it over (new_value isn't a mapping, or
            # the key had no comment to begin with): keep it on the key
            # itself, exactly like the untouched dict.update() path did.
            existing.ca.items[key] = old_entry


def execute_upgrade(project_root: Path, plan: UpgradePlan,
                    dry_run: bool = False) -> list[str]:
    """Execute the upgrade plan. Returns list of completed action descriptions."""
    completed: list[str] = []

    for action in plan.actions:
        if action.kind == "create-dir":
            dp = project_root / action.target
            if not dry_run:
                dp.mkdir(parents=True, exist_ok=True)
            completed.append(action.description)

        elif action.kind == "generate-file" and action.target == "project-context.yaml":
            pctx = project_root / "project-context.yaml"
            v2_data = _extract_v2_config(project_root)
            v3_section = _generate_v3_section(v2_data)

            if not dry_run:
                # Round-trip load so save_yaml() preserves every existing
                # comment, quote style and inline collection — only the
                # migrated keys (grimoire/project/agents/memory) change
                # (grimoire-kit#430).
                existing = load_yaml_roundtrip(pctx) if pctx.exists() else CommentedMap()
                if not isinstance(existing, CommentedMap):
                    existing = CommentedMap()
                _merge_v3_section(existing, v3_section)
                save_yaml(existing, pctx)
            completed.append(action.description)

    return completed


# ── CLI ──────────────────────────────────────────────────────────────────────
_upgrade_path_arg = typer.Argument(Path(), help="Path to the v2 project.")
_upgrade_dry_run_opt = typer.Option(False, "--dry-run", "-n", help="Show plan without applying.")


def upgrade_command(
    ctx: typer.Context,
    path: Path = _upgrade_path_arg,
    dry_run: bool = _upgrade_dry_run_opt,
) -> None:
    """Migrate a v2 project to v3 structure.

    [dim]Examples:[/dim]
      [cyan]grimoire upgrade . --dry-run[/cyan]  Preview migration
      [cyan]grimoire upgrade -o json .[/cyan]    JSON output for CI
    """
    fmt = (ctx.obj or {}).get("output", "text")
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
            console.print(f"  [yellow][!] {w}[/yellow]")

    for desc in completed:
        icon = "[cyan]plan[/cyan]" if dry_run else "[green]done[/green]"
        console.print(f"  {icon}  {desc}")

    if not completed and not plan.warnings:
        console.print("  [green]Nothing to do.[/green]")

    console.print(f"\n[bold]Migration {'planned' if dry_run else 'complete'}.[/bold]")
