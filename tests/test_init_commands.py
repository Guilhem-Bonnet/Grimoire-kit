"""
Tests for grimoire-init.sh new commands: reset, uninstall, quick-update.

These tests create temporary project structures and verify the commands work
correctly without touching real project data.
"""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

# ── Constants ─────────────────────────────────────────────────────────────────

KIT_DIR = Path(__file__).resolve().parent.parent
INIT_SCRIPT = KIT_DIR / "grimoire-init.sh"
GRIMOIRE_SH = KIT_DIR / "grimoire.sh"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _run(args: list[str], cwd: str | Path, *, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run a command, return CompletedProcess."""
    run_env = {**os.environ, **(env or {})}
    return subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=30,
        env=run_env,
    )


def _create_fake_grimoire(base: Path) -> Path:
    """Create a minimal _grimoire/ structure for testing."""
    grimoire = base / "_grimoire"
    (grimoire / "_config" / "custom" / "agents").mkdir(parents=True)
    (grimoire / "_config" / "custom" / "prompt-templates").mkdir(parents=True)
    (grimoire / "_config" / "custom" / "workflows").mkdir(parents=True)
    (grimoire / "_memory" / "agent-learnings").mkdir(parents=True)
    (grimoire / "_memory" / "session-summaries").mkdir(parents=True)
    (grimoire / "_memory" / "archives").mkdir(parents=True)
    (grimoire / "_memory" / "backends").mkdir(parents=True)

    # Framework files
    (grimoire / "_config" / "custom" / "agent-base.md").write_text("# old agent-base")
    (grimoire / "_config" / "custom" / "cc-verify.sh").write_text("#!/bin/bash\n# old cc")
    (grimoire / "_config" / "custom" / "sil-collect.sh").write_text("#!/bin/bash\n# old sil")

    # Memory files
    (grimoire / "_memory" / "maintenance.py").write_text("# old maintenance")
    (grimoire / "_memory" / "mem0-bridge.py").write_text("# old bridge")
    (grimoire / "_memory" / "session-save.py").write_text("# old save")
    (grimoire / "_memory" / "shared-context.md").write_text("# My context")
    (grimoire / "_memory" / "decisions-log.md").write_text("# Decisions")
    (grimoire / "_memory" / "memories.json").write_text("[]")

    # Agent files
    (grimoire / "_config" / "custom" / "agents" / "atlas.md").write_text("# Atlas")
    (grimoire / "_config" / "custom" / "agents" / "my-custom-agent.md").write_text("# Custom")

    # Agent learnings
    (grimoire / "_memory" / "agent-learnings" / "atlas.md").write_text("# Learnings")

    return grimoire


def _create_fake_output(base: Path) -> Path:
    """Create _grimoire-output/ for testing."""
    output = base / "_grimoire-output"
    (output / ".runs" / "main").mkdir(parents=True)
    (output / "team-vision").mkdir(parents=True)
    (output / ".runs" / "main" / "branch.json").write_text('{"branch": "main"}')
    return output


def _create_project_context(base: Path, version: str = "3.1.0") -> Path:
    """Create project-context.yaml."""
    ctx = base / "project-context.yaml"
    ctx.write_text(textwrap.dedent(f"""\
        project:
          name: "test-project"
        user:
          name: "TestUser"
        grimoire_kit_version: "{version}"
    """))
    return ctx


def _create_copilot_instructions(base: Path) -> Path:
    """Create .github/copilot-instructions.md with Grimoire marker."""
    ci = base / ".github" / "copilot-instructions.md"
    ci.parent.mkdir(parents=True, exist_ok=True)
    ci.write_text("# Copilot Instructions\n> Auto-généré par Grimoire Custom Kit v3.1.0\n")
    return ci


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def project_dir(tmp_path):
    """Create a complete fake Grimoire project."""
    _create_fake_grimoire(tmp_path)
    _create_fake_output(tmp_path)
    _create_project_context(tmp_path)
    _create_copilot_instructions(tmp_path)
    return tmp_path


@pytest.fixture
def empty_dir(tmp_path):
    """An empty directory with no Grimoire installation."""
    return tmp_path


# ══════════════════════════════════════════════════════════════════════════════
# cmd_reset tests
# ══════════════════════════════════════════════════════════════════════════════


class TestCmdReset:
    """Tests for the reset command."""

    def test_reset_help(self, project_dir):
        """--help prints usage and exits 0."""
        result = _run(["bash", str(INIT_SCRIPT), "reset", "--help"], cwd=project_dir)
        assert result.returncode == 0
        assert "Remet l'installation" in result.stdout

    def test_reset_no_grimoire(self, empty_dir):
        """Reset on a directory without _grimoire/ fails."""
        result = _run(["bash", str(INIT_SCRIPT), "reset", "--yes"], cwd=empty_dir)
        assert result.returncode != 0
        assert "Pas de projet Grimoire" in result.stderr

    def test_soft_reset_dry_run(self, project_dir):
        """Dry-run soft reset doesn't modify files."""
        # Record original content
        ab = (project_dir / "_grimoire" / "_config" / "custom" / "agent-base.md").read_text()
        assert ab == "# old agent-base"

        result = _run(["bash", str(INIT_SCRIPT), "reset", "--dry-run"], cwd=project_dir)
        assert result.returncode == 0
        assert "Dry-run" in result.stdout

        # File should be unchanged
        assert (project_dir / "_grimoire" / "_config" / "custom" / "agent-base.md").read_text() == ab

    def test_soft_reset_preserves_memory(self, project_dir):
        """Soft reset preserves _memory/ content."""
        sc = project_dir / "_grimoire" / "_memory" / "shared-context.md"
        sc.write_text("# Important context I wrote")

        result = _run(["bash", str(INIT_SCRIPT), "reset", "--yes"], cwd=project_dir)
        assert result.returncode == 0

        # Memory should be preserved
        assert sc.exists()
        assert sc.read_text() == "# Important context I wrote"

    def test_soft_reset_preserves_custom_agents(self, project_dir):
        """Soft reset preserves custom (non-meta) agents."""
        custom = project_dir / "_grimoire" / "_config" / "custom" / "agents" / "my-custom-agent.md"
        assert custom.exists()

        result = _run(["bash", str(INIT_SCRIPT), "reset", "--yes"], cwd=project_dir)
        assert result.returncode == 0

        assert custom.exists()
        assert custom.read_text() == "# Custom"

    def test_hard_reset_dry_run(self, project_dir):
        """Hard reset dry-run doesn't delete anything."""
        result = _run(["bash", str(INIT_SCRIPT), "reset", "--hard", "--dry-run"], cwd=project_dir)
        assert result.returncode == 0
        assert "Dry-run" in result.stdout

        # _grimoire/ should still exist
        assert (project_dir / "_grimoire").exists()

    def test_hard_reset_removes_and_recreates(self, project_dir):
        """Hard reset removes _grimoire/ and recreates the skeleton."""
        custom = project_dir / "_grimoire" / "_memory" / "shared-context.md"
        assert custom.exists()

        result = _run(["bash", str(INIT_SCRIPT), "reset", "--hard", "--yes"], cwd=project_dir)
        assert result.returncode == 0

        # Structure should be recreated
        assert (project_dir / "_grimoire" / "_config" / "custom" / "agents").is_dir()
        assert (project_dir / "_grimoire" / "_memory").is_dir()

        # Old memory content should be gone
        assert not custom.exists() or custom.read_text() != "# My context"

    def test_reset_unknown_option_fails(self, project_dir):
        """Unknown option causes error."""
        result = _run(["bash", str(INIT_SCRIPT), "reset", "--nonexistent"], cwd=project_dir)
        assert result.returncode != 0


# ══════════════════════════════════════════════════════════════════════════════
# cmd_uninstall tests
# ══════════════════════════════════════════════════════════════════════════════


class TestCmdUninstall:
    """Tests for the uninstall command."""

    def test_uninstall_help(self, project_dir):
        """--help prints usage and exits 0."""
        result = _run(["bash", str(INIT_SCRIPT), "uninstall", "--help"], cwd=project_dir)
        assert result.returncode == 0
        assert "Supprime complètement" in result.stdout

    def test_uninstall_no_grimoire(self, empty_dir):
        """Uninstall on an empty dir fails."""
        result = _run(["bash", str(INIT_SCRIPT), "uninstall", "--yes"], cwd=empty_dir)
        assert result.returncode != 0

    def test_uninstall_removes_grimoire(self, project_dir):
        """Uninstall --yes removes _grimoire/ and _grimoire-output/."""
        assert (project_dir / "_grimoire").exists()
        assert (project_dir / "_grimoire-output").exists()

        result = _run(["bash", str(INIT_SCRIPT), "uninstall", "--yes"], cwd=project_dir)
        assert result.returncode == 0

        assert not (project_dir / "_grimoire").exists()
        assert not (project_dir / "_grimoire-output").exists()

    def test_uninstall_removes_copilot_instructions(self, project_dir):
        """Uninstall removes .github/copilot-instructions.md if generated by Grimoire."""
        ci = project_dir / ".github" / "copilot-instructions.md"
        assert ci.exists()

        result = _run(["bash", str(INIT_SCRIPT), "uninstall", "--yes"], cwd=project_dir)
        assert result.returncode == 0

        assert not ci.exists()

    def test_uninstall_removes_project_context(self, project_dir):
        """Uninstall removes project-context.yaml by default."""
        ctx = project_dir / "project-context.yaml"
        assert ctx.exists()

        result = _run(["bash", str(INIT_SCRIPT), "uninstall", "--yes"], cwd=project_dir)
        assert result.returncode == 0

        assert not ctx.exists()

    def test_uninstall_keep_config(self, project_dir):
        """--keep-config preserves project-context.yaml."""
        ctx = project_dir / "project-context.yaml"
        assert ctx.exists()

        result = _run(["bash", str(INIT_SCRIPT), "uninstall", "--yes", "--keep-config"], cwd=project_dir)
        assert result.returncode == 0

        assert ctx.exists()
        assert not (project_dir / "_grimoire").exists()

    def test_uninstall_success_message(self, project_dir):
        """Success message is displayed."""
        result = _run(["bash", str(INIT_SCRIPT), "uninstall", "--yes"], cwd=project_dir)
        assert result.returncode == 0
        assert "désinstallé avec succès" in result.stdout


# ══════════════════════════════════════════════════════════════════════════════
# cmd_quickupdate tests
# ══════════════════════════════════════════════════════════════════════════════


class TestCmdQuickUpdate:
    """Tests for the quick-update command."""

    def test_quickupdate_help(self, project_dir):
        """--help prints usage and exits 0."""
        result = _run(["bash", str(INIT_SCRIPT), "quick-update", "--help"], cwd=project_dir)
        assert result.returncode == 0
        assert "Mise à jour rapide" in result.stdout

    def test_quickupdate_no_grimoire(self, empty_dir):
        """Quick-update on an empty dir fails."""
        result = _run(["bash", str(INIT_SCRIPT), "quick-update"], cwd=empty_dir)
        assert result.returncode != 0

    def test_quickupdate_dry_run(self, project_dir):
        """Dry-run doesn't modify files."""
        ab = project_dir / "_grimoire" / "_config" / "custom" / "agent-base.md"
        original = ab.read_text()

        result = _run(["bash", str(INIT_SCRIPT), "quick-update", "--dry-run"], cwd=project_dir)
        assert result.returncode == 0
        assert "Dry-run" in result.stdout

        assert ab.read_text() == original

    def test_quickupdate_preserves_custom_agents(self, project_dir):
        """Quick-update doesn't touch custom agents."""
        custom = project_dir / "_grimoire" / "_config" / "custom" / "agents" / "my-custom-agent.md"
        original = custom.read_text()

        result = _run(["bash", str(INIT_SCRIPT), "quick-update"], cwd=project_dir)
        assert result.returncode == 0

        assert custom.read_text() == original

    def test_quickupdate_preserves_memory(self, project_dir):
        """Quick-update doesn't touch memory."""
        sc = project_dir / "_grimoire" / "_memory" / "shared-context.md"
        sc.write_text("# My precious context")

        result = _run(["bash", str(INIT_SCRIPT), "quick-update"], cwd=project_dir)
        assert result.returncode == 0

        assert sc.read_text() == "# My precious context"

    def test_quickupdate_reports_counts(self, project_dir):
        """Output mentions update counts."""
        result = _run(["bash", str(INIT_SCRIPT), "quick-update"], cwd=project_dir)
        assert result.returncode == 0
        assert "mis à jour" in result.stdout

    def test_quickupdate_unknown_option_fails(self, project_dir):
        """Unknown option causes error."""
        result = _run(["bash", str(INIT_SCRIPT), "quick-update", "--nonexistent"], cwd=project_dir)
        assert result.returncode != 0


# ══════════════════════════════════════════════════════════════════════════════
# grimoire.sh routing tests
# ══════════════════════════════════════════════════════════════════════════════


class TestGrimoireShRouting:
    """Tests that grimoire.sh correctly routes to new commands."""

    def test_help_shows_reset(self):
        """grimoire help lists the reset command."""
        result = _run(["bash", str(GRIMOIRE_SH), "help"], cwd=KIT_DIR)
        assert result.returncode == 0
        assert "reset" in result.stdout

    def test_help_shows_uninstall(self):
        """grimoire help lists the uninstall command."""
        result = _run(["bash", str(GRIMOIRE_SH), "help"], cwd=KIT_DIR)
        assert result.returncode == 0
        assert "uninstall" in result.stdout

    def test_help_shows_quick_update(self):
        """grimoire help lists the quick-update command."""
        result = _run(["bash", str(GRIMOIRE_SH), "help"], cwd=KIT_DIR)
        assert result.returncode == 0
        assert "quick-update" in result.stdout


# ══════════════════════════════════════════════════════════════════════════════
# install.sh bootstrap tests
# ══════════════════════════════════════════════════════════════════════════════

INSTALL_SH = KIT_DIR / "install.sh"


class TestInstallSh:
    """Tests for the bootstrap install.sh."""

    def test_install_sh_exists(self):
        """install.sh exists."""
        assert INSTALL_SH.exists()

    def test_install_sh_help(self):
        """--help prints usage."""
        result = _run(["bash", str(INSTALL_SH), "--help"], cwd=KIT_DIR)
        assert result.returncode == 0
        assert "Bootstrap Installer" in result.stdout

    def test_install_sh_has_shebang(self):
        """install.sh has proper shebang."""
        first_line = INSTALL_SH.read_text().split("\n")[0]
        assert first_line == "#!/usr/bin/env bash"
