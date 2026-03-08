"""``grimoire upgrade`` — migrate a v2 project to v3 structure.

Detects a v2 project by the presence of ``project-context.yaml`` without
v3 markers. Generates the v3 config from the v2 config,
ensures the v3 directory layout, and preserves all memory files.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grimoire.tools._common import load_yaml, save_yaml

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

    # 1. Generate bmad section in project-context.yaml
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
                # Merge v3 section into existing file
                existing = load_yaml(pctx) if pctx.exists() else {}
                if not isinstance(existing, dict):
                    existing = {}
                existing.update(v3_section)
                save_yaml(existing, pctx)
            completed.append(action.description)

    return completed
