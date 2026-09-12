"""Tests for grimoire.cli.cmd_upgrade — v2 → v3 migration."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.cli.cmd_upgrade import (
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
    (tmp_path / "_grimoire/_memory").mkdir(parents=True)
    (tmp_path / "_grimoire/_memory/shared-context.md").write_text("# Context\n")
    return tmp_path


@pytest.fixture()
def v3_project(tmp_path: Path) -> Path:
    """A minimal v3 project structure."""
    (tmp_path / "project-context.yaml").write_text(
        "grimoire:\n  version: '3.0'\nproject:\n  name: test\n"
    )
    (tmp_path / "_grimoire/_config/agents").mkdir(parents=True)
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
        assert "grimoire" not in text or "version" not in text

    def test_execute_creates_v3_section(self, v2_project: Path) -> None:
        plan = plan_upgrade(v2_project)
        execute_upgrade(v2_project, plan, dry_run=False)
        # Now should detect as v3
        from grimoire.tools._common import load_yaml
        data = load_yaml(v2_project / "project-context.yaml")
        assert "grimoire" in data
        assert data["grimoire"]["version"] == "3.0"

    def test_execute_creates_directories(self, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text("project: test\n")
        plan = plan_upgrade(tmp_path)
        execute_upgrade(tmp_path, plan, dry_run=False)
        assert (tmp_path / "_grimoire").is_dir()
        assert (tmp_path / "_grimoire-output").is_dir()

    def test_preserves_memory(self, v2_project: Path) -> None:
        plan = plan_upgrade(v2_project)
        execute_upgrade(v2_project, plan, dry_run=False)
        # Memory file should still exist
        assert (v2_project / "_grimoire/_memory/shared-context.md").exists()
        assert (v2_project / "_grimoire/_memory/shared-context.md").read_text() == "# Context\n"

    def test_migrated_passes_detect_v3(self, v2_project: Path) -> None:
        plan = plan_upgrade(v2_project)
        execute_upgrade(v2_project, plan, dry_run=False)
        assert detect_version(v2_project) == "v3"

    def test_empty_plan(self, v3_project: Path) -> None:
        plan = plan_upgrade(v3_project)
        completed = execute_upgrade(v3_project, plan, dry_run=False)
        assert completed == []


# ── Comment/formatting preservation (grimoire-kit#430) ─────────────────────────
#
# `grimoire upgrade` round-trips project-context.yaml through
# tools/_common.py's load_yaml()/save_yaml(). Before the fix, load_yaml()
# parsed in ruamel "safe" mode (a bare dict, no comment metadata) and
# save_yaml() then wrote it back in round-trip mode with nothing left to
# round-trip — every comment in the file was silently discarded, even ones
# on keys the migration never touches.

# Two realistic v2 shapes for the `project` key: a bare, commented scalar
# (the shape used by this file's own `v2_project` fixture) and a mapping
# that already has its own comments (a hand-edited v2 file). Both must
# survive the scalar/mapping merge in `_merge_v3_section` without losing or
# misplacing comments that belong to *other* keys.
_RICH_V2_FIXTURES = {
    "project_is_a_commented_scalar": (
        "# En-tête du projet v2 — commentaire de bloc\n"
        'project: "MonProjet"  # nom affiché à l\'utilisateur\n'
        "\n"
        "# Langue de communication\n"
        'communication_language: "français"\n'
        "\n"
        "# Niveau de compétence\n"
        'skill_level: "expert"  # beginner | intermediate | expert\n'
        "\n"
        "tags: [alpha, beta, gamma]  # liste inline à préserver\n"
        "\n"
        "notes: |\n"
        "  Ceci est une note\n"
        "  multi-lignes.\n"
    ),
    "project_is_already_a_commented_mapping": (
        "project:\n"
        '  name: "MonProjet"  # nom affiché à l\'utilisateur\n'
        "  type: app\n"
        "\n"
        "# Langue de communication\n"
        'communication_language: "français"\n'
        "\n"
        "# Niveau de compétence\n"
        'skill_level: "expert"  # beginner | intermediate | expert\n'
        "\n"
        "tags: [alpha, beta, gamma]  # liste inline à préserver\n"
        "\n"
        "notes: |\n"
        "  Ceci est une note\n"
        "  multi-lignes.\n"
    ),
}

# Lines that belong to keys the v2→v3 migration never touches — these must
# come out of the migrated file exactly as they went in, byte for byte.
_UNTOUCHED_LINES = (
    "# Langue de communication",
    'communication_language: "français"',
    "# Niveau de compétence",
    'skill_level: "expert"  # beginner | intermediate | expert',
    "tags: [alpha, beta, gamma]  # liste inline à préserver",
    "notes: |",
    "  Ceci est une note",
    "  multi-lignes.",
)


class TestExecuteUpgradePreservesComments:
    @pytest.mark.parametrize("fixture_name", sorted(_RICH_V2_FIXTURES))
    def test_untouched_lines_are_byte_identical(self, tmp_path: Path, fixture_name: str) -> None:
        pctx = tmp_path / "project-context.yaml"
        pctx.write_text(_RICH_V2_FIXTURES[fixture_name], encoding="utf-8")

        plan = plan_upgrade(tmp_path)
        execute_upgrade(tmp_path, plan, dry_run=False)

        after_lines = pctx.read_text(encoding="utf-8").splitlines()
        for line in _UNTOUCHED_LINES:
            assert line in after_lines, f"{line!r} missing or altered after upgrade ({fixture_name})"

    @pytest.mark.parametrize("fixture_name", sorted(_RICH_V2_FIXTURES))
    def test_inline_comment_on_a_migrated_key_is_kept(self, tmp_path: Path, fixture_name: str) -> None:
        pctx = tmp_path / "project-context.yaml"
        pctx.write_text(_RICH_V2_FIXTURES[fixture_name], encoding="utf-8")

        plan = plan_upgrade(tmp_path)
        execute_upgrade(tmp_path, plan, dry_run=False)

        text = pctx.read_text(encoding="utf-8")
        assert "# nom affiché à l'utilisateur" in text

    @pytest.mark.parametrize("fixture_name", sorted(_RICH_V2_FIXTURES))
    def test_v3_section_is_still_generated(self, tmp_path: Path, fixture_name: str) -> None:
        pctx = tmp_path / "project-context.yaml"
        pctx.write_text(_RICH_V2_FIXTURES[fixture_name], encoding="utf-8")

        plan = plan_upgrade(tmp_path)
        execute_upgrade(tmp_path, plan, dry_run=False)

        from grimoire.tools._common import load_yaml

        data = load_yaml(pctx)
        assert data["grimoire"]["version"] == "3.0"
        assert data["project"]["name"] == "MonProjet"
        assert data["agents"]["archetype"] == "minimal"
        assert data["memory"]["backend"] == "auto"

    def test_second_upgrade_of_an_already_v3_file_is_a_no_op(self, tmp_path: Path) -> None:
        """Running the migration twice (e.g. an interrupted `up`) must not
        further disturb the file: once the project is v3, plan_upgrade()
        reports no actions."""
        pctx = tmp_path / "project-context.yaml"
        pctx.write_text(_RICH_V2_FIXTURES["project_is_a_commented_scalar"], encoding="utf-8")
        execute_upgrade(tmp_path, plan_upgrade(tmp_path), dry_run=False)

        once = pctx.read_text(encoding="utf-8")
        plan_again = plan_upgrade(tmp_path)
        execute_upgrade(tmp_path, plan_again, dry_run=False)

        assert pctx.read_text(encoding="utf-8") == once


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
