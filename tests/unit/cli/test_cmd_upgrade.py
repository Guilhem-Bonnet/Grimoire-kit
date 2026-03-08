"""Tests for bmad.cli.cmd_upgrade — v2 → v3 migration."""

from __future__ import annotations

from pathlib import Path

import pytest

from bmad.cli.cmd_upgrade import (
    UpgradeAction,
    UpgradePlan,
    detect_version,
    execute_upgrade,
    plan_upgrade,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def v2_project(tmp_path: Path) -> Path:
    """A minimal v2 project structure."""
    (tmp_path / "project-context.yaml").write_text(
        "project: my-project\ncommunication_language: Français\n"
    )
    (tmp_path / "_bmad/_memory").mkdir(parents=True)
    (tmp_path / "_bmad/_memory/shared-context.md").write_text("# Context\n")
    return tmp_path


@pytest.fixture()
def v3_project(tmp_path: Path) -> Path:
    """A minimal v3 project structure."""
    (tmp_path / "project-context.yaml").write_text(
        "bmad:\n  version: '3.0'\nproject:\n  name: test\n"
    )
    (tmp_path / "_bmad/_config/agents").mkdir(parents=True)
    return tmp_path


# ── detect_version ────────────────────────────────────────────────────────────


class TestDetectVersion:
    def test_v2_detected(self, v2_project: Path) -> None:
        assert detect_version(v2_project) == "v2"

    def test_v3_detected(self, v3_project: Path) -> None:
        assert detect_version(v3_project) == "v3"

    def test_unknown_no_file(self, tmp_path: Path) -> None:
        assert detect_version(tmp_path) == "unknown"

    def test_unknown_empty_yaml(self, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text("")
        assert detect_version(tmp_path) == "unknown"

    def test_unknown_invalid_yaml(self, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text("- [invalid: {yaml")
        assert detect_version(tmp_path) == "unknown"


# ── plan_upgrade ──────────────────────────────────────────────────────────────


class TestPlanUpgrade:
    def test_v2_has_actions(self, v2_project: Path) -> None:
        plan = plan_upgrade(v2_project)
        assert not plan.already_v3
        assert len(plan.actions) > 0
        kinds = {a.kind for a in plan.actions}
        assert "generate-file" in kinds

    def test_v3_already(self, v3_project: Path) -> None:
        plan = plan_upgrade(v3_project)
        assert plan.already_v3
        assert len(plan.actions) == 0

    def test_unknown_has_warnings(self, tmp_path: Path) -> None:
        plan = plan_upgrade(tmp_path)
        assert len(plan.warnings) > 0

    def test_creates_missing_dirs(self, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text("project: x\n")
        plan = plan_upgrade(tmp_path)
        dir_actions = [a for a in plan.actions if a.kind == "create-dir"]
        assert len(dir_actions) > 0

    def test_warns_about_top_level_dirs(self, v2_project: Path) -> None:
        (v2_project / "agents").mkdir()
        plan = plan_upgrade(v2_project)
        assert any("agents" in w for w in plan.warnings)


# ── execute_upgrade ───────────────────────────────────────────────────────────


class TestExecuteUpgrade:
    def test_dry_run_no_changes(self, v2_project: Path) -> None:
        plan = plan_upgrade(v2_project)
        completed = execute_upgrade(v2_project, plan, dry_run=True)
        assert len(completed) > 0
        # v2 config should NOT have been modified
        text = (v2_project / "project-context.yaml").read_text()
        assert "bmad" not in text or "version" not in text

    def test_execute_creates_v3_section(self, v2_project: Path) -> None:
        plan = plan_upgrade(v2_project)
        execute_upgrade(v2_project, plan, dry_run=False)
        # Now should detect as v3
        from bmad.tools._common import load_yaml
        data = load_yaml(v2_project / "project-context.yaml")
        assert "bmad" in data
        assert data["bmad"]["version"] == "3.0"

    def test_execute_creates_directories(self, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text("project: test\n")
        plan = plan_upgrade(tmp_path)
        execute_upgrade(tmp_path, plan, dry_run=False)
        assert (tmp_path / "_bmad").is_dir()
        assert (tmp_path / "_bmad-output").is_dir()

    def test_preserves_memory(self, v2_project: Path) -> None:
        plan = plan_upgrade(v2_project)
        execute_upgrade(v2_project, plan, dry_run=False)
        # Memory file should still exist
        assert (v2_project / "_bmad/_memory/shared-context.md").exists()
        assert (v2_project / "_bmad/_memory/shared-context.md").read_text() == "# Context\n"

    def test_migrated_passes_detect_v3(self, v2_project: Path) -> None:
        plan = plan_upgrade(v2_project)
        execute_upgrade(v2_project, plan, dry_run=False)
        assert detect_version(v2_project) == "v3"

    def test_empty_plan(self, v3_project: Path) -> None:
        plan = plan_upgrade(v3_project)
        completed = execute_upgrade(v3_project, plan, dry_run=False)
        assert completed == []


# ── UpgradePlan model ─────────────────────────────────────────────────────────


class TestUpgradePlan:
    def test_defaults(self) -> None:
        plan = UpgradePlan()
        assert plan.source_version == "v2"
        assert plan.target_version == "v3"
        assert not plan.already_v3

    def test_upgrade_action(self) -> None:
        a = UpgradeAction(kind="create-dir", description="test", target="x/")
        assert a.kind == "create-dir"
