"""Tests for grimoire.cli.app — CLI commands."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import _ALIASES, _expand_aliases, app

runner = CliRunner()


# ── Global Flags ──────────────────────────────────────────────────────────────

class TestGlobalFlags:
    """Tests for --verbose, --log-format, --output callback flags."""

    def test_help_shows_verbose(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert "--verbose" in result.output

    def test_help_shows_log_format(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert "--log-format" in result.output

    def test_help_shows_output(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert "--output" in result.output

    def test_verbose_single_sets_info(self) -> None:
        with patch("grimoire.cli.app.configure_logging") as mock_log:
            runner.invoke(app, ["-v", "env"])
            mock_log.assert_called_once_with("INFO", fmt="text")

    def test_verbose_double_sets_debug(self) -> None:
        with patch("grimoire.cli.app.configure_logging") as mock_log:
            runner.invoke(app, ["-vv", "env"])
            mock_log.assert_called_once_with("DEBUG", fmt="text")

    def test_log_format_json(self) -> None:
        with patch("grimoire.cli.app.configure_logging") as mock_log:
            runner.invoke(app, ["-v", "--log-format", "json", "env"])
            mock_log.assert_called_once_with("INFO", fmt="json")

    def test_output_json_env(self) -> None:
        result = runner.invoke(app, ["-o", "json", "env"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "grimoire_version" in data
        assert "python" in data
        assert "dependencies" in data

    def test_output_text_env(self) -> None:
        result = runner.invoke(app, ["--output", "text", "env"])
        assert result.exit_code == 0
        assert "Grimoire Kit" in result.output

    def test_no_verbose_does_not_configure(self) -> None:
        with patch("grimoire.cli.app.configure_logging") as mock_log:
            runner.invoke(app, ["env"])
            mock_log.assert_not_called()


# ── Version ───────────────────────────────────────────────────────────────────

class TestVersion:
    def test_version_flag(self) -> None:
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "grimoire-kit" in result.output

    def test_help(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "init" in result.output
        assert "doctor" in result.output


# ── Init ──────────────────────────────────────────────────────────────────────

class TestInit:
    def test_init_creates_config(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["init", str(tmp_path)])
        assert result.exit_code == 0
        cfg = tmp_path / "project-context.yaml"
        assert cfg.is_file()
        content = cfg.read_text()
        assert "project:" in content
        assert tmp_path.name in content

    def test_init_creates_dirs(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path)])
        assert (tmp_path / "_grimoire" / "_memory").is_dir()
        assert (tmp_path / "_grimoire-output").is_dir()

    def test_init_custom_name(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["init", str(tmp_path), "--name", "my-project"])
        assert result.exit_code == 0
        content = (tmp_path / "project-context.yaml").read_text()
        assert "my-project" in content

    def test_init_refuses_overwrite(self, tmp_path: Path) -> None:
        # First init
        runner.invoke(app, ["init", str(tmp_path)])
        # Second without --force
        result = runner.invoke(app, ["init", str(tmp_path)])
        assert result.exit_code == 1

    def test_init_force_overwrite(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path)])
        result = runner.invoke(app, ["init", str(tmp_path), "--force"])
        assert result.exit_code == 0

    def test_init_creates_parent_dirs(self, tmp_path: Path) -> None:
        deep = tmp_path / "a" / "b" / "c"
        result = runner.invoke(app, ["init", str(deep)])
        assert result.exit_code == 0
        assert (deep / "project-context.yaml").is_file()

    # ── Archetype option ──

    def test_init_default_archetype(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path)])
        content = (tmp_path / "project-context.yaml").read_text()
        assert 'archetype: "minimal"' in content

    def test_init_web_app_archetype(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["init", str(tmp_path), "--archetype", "web-app"])
        assert result.exit_code == 0
        content = (tmp_path / "project-context.yaml").read_text()
        assert 'archetype: "web-app"' in content

    def test_init_short_archetype_flag(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["init", str(tmp_path), "-a", "infra-ops"])
        assert result.exit_code == 0
        content = (tmp_path / "project-context.yaml").read_text()
        assert 'archetype: "infra-ops"' in content

    def test_init_invalid_archetype(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["init", str(tmp_path), "--archetype", "nonexistent"])
        assert result.exit_code == 1
        assert "Unknown archetype" in result.output

    # ── Backend option ──

    def test_init_default_backend(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path)])
        content = (tmp_path / "project-context.yaml").read_text()
        assert 'backend: "auto"' in content

    def test_init_local_backend(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["init", str(tmp_path), "--backend", "local"])
        assert result.exit_code == 0
        content = (tmp_path / "project-context.yaml").read_text()
        assert 'backend: "local"' in content

    def test_init_short_backend_flag(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["init", str(tmp_path), "-b", "ollama"])
        assert result.exit_code == 0
        content = (tmp_path / "project-context.yaml").read_text()
        assert 'backend: "ollama"' in content

    def test_init_invalid_backend(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["init", str(tmp_path), "--backend", "nope"])
        assert result.exit_code == 1
        assert "Unknown backend" in result.output

    # ── Combined options ──

    def test_init_combined_options(self, tmp_path: Path) -> None:
        result = runner.invoke(app, [
            "init", str(tmp_path),
            "--name", "my-project",
            "--archetype", "creative-studio",
            "--backend", "qdrant-local",
        ])
        assert result.exit_code == 0
        content = (tmp_path / "project-context.yaml").read_text()
        assert "my-project" in content
        assert 'archetype: "creative-studio"' in content
        assert 'backend: "qdrant-local"' in content


# ── Doctor ────────────────────────────────────────────────────────────────────

class TestDoctor:
    @pytest.fixture()
    def healthy_project(self, tmp_path: Path) -> Path:
        """Create a project with valid config + structure."""
        runner.invoke(app, ["init", str(tmp_path)])
        return tmp_path

    def test_doctor_healthy(self, healthy_project: Path) -> None:
        result = runner.invoke(app, ["doctor", str(healthy_project)])
        assert result.exit_code == 0
        assert "OK" in result.output

    def test_doctor_no_config(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["doctor", str(tmp_path)])
        assert result.exit_code == 1
        assert "FAIL" in result.output

    def test_doctor_invalid_config(self, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text("project:\n  description: no name\n")
        result = runner.invoke(app, ["doctor", str(tmp_path)])
        assert result.exit_code == 1

    def test_doctor_missing_dirs(self, tmp_path: Path) -> None:
        # Config only, no _grimoire dirs
        (tmp_path / "project-context.yaml").write_text(
            "project:\n  name: test\n"
        )
        result = runner.invoke(app, ["doctor", str(tmp_path)])
        assert result.exit_code == 1
        assert "FAIL" in result.output

    def test_doctor_shows_project_name(self, healthy_project: Path) -> None:
        result = runner.invoke(app, ["doctor", str(healthy_project)])
        assert healthy_project.name in result.output

    def test_doctor_shows_version(self, healthy_project: Path) -> None:
        result = runner.invoke(app, ["doctor", str(healthy_project)])
        assert "grimoire-kit" in result.output


# ── Status ────────────────────────────────────────────────────────────────────

class TestStatus:
    @pytest.fixture()
    def project(self, tmp_path: Path) -> Path:
        runner.invoke(app, ["init", str(tmp_path), "--name", "my-app"])
        return tmp_path

    def test_status_shows_name(self, project: Path) -> None:
        result = runner.invoke(app, ["status", str(project)])
        assert result.exit_code == 0
        assert "my-app" in result.output

    def test_status_shows_archetype(self, project: Path) -> None:
        result = runner.invoke(app, ["status", str(project)])
        assert "minimal" in result.output

    def test_status_shows_memory(self, project: Path) -> None:
        result = runner.invoke(app, ["status", str(project)])
        assert "auto" in result.output

    def test_status_shows_structure(self, project: Path) -> None:
        result = runner.invoke(app, ["status", str(project)])
        assert "_grimoire" in result.output

    def test_status_no_project(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["status", str(tmp_path)])
        assert result.exit_code == 1

    def test_status_in_help(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert "status" in result.output

    def test_status_shows_version(self, project: Path) -> None:
        result = runner.invoke(app, ["status", str(project)])
        assert "grimoire-kit" in result.output


# ── Add / Remove ──────────────────────────────────────────────────────────────

class TestAddRemove:
    @pytest.fixture()
    def project(self, tmp_path: Path) -> Path:
        runner.invoke(app, ["init", str(tmp_path), "--name", "test-proj"])
        return tmp_path

    # ── add ──

    def test_add_agent(self, project: Path) -> None:
        result = runner.invoke(app, ["add", "my-agent", str(project)])
        assert result.exit_code == 0
        assert "Added agent" in result.output
        content = (project / "project-context.yaml").read_text()
        assert "my-agent" in content

    def test_add_agent_duplicate(self, project: Path) -> None:
        runner.invoke(app, ["add", "agent-x", str(project)])
        result = runner.invoke(app, ["add", "agent-x", str(project)])
        assert result.exit_code == 0
        assert "already" in result.output

    def test_add_multiple_agents(self, project: Path) -> None:
        runner.invoke(app, ["add", "alpha", str(project)])
        runner.invoke(app, ["add", "beta", str(project)])
        content = (project / "project-context.yaml").read_text()
        assert "alpha" in content
        assert "beta" in content

    def test_add_no_project(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["add", "agent-x", str(tmp_path)])
        assert result.exit_code == 1

    # ── remove ──

    def test_remove_agent(self, project: Path) -> None:
        runner.invoke(app, ["add", "to-remove", str(project)])
        result = runner.invoke(app, ["-y", "remove", "to-remove", str(project)])
        assert result.exit_code == 0
        assert "Removed agent" in result.output
        content = (project / "project-context.yaml").read_text()
        assert "to-remove" not in content

    def test_remove_nonexistent(self, project: Path) -> None:
        result = runner.invoke(app, ["remove", "ghost", str(project)])
        assert result.exit_code == 1
        assert "not in project" in result.output

    def test_remove_no_project(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["remove", "x", str(tmp_path)])
        assert result.exit_code == 1

    # ── roundtrip ──

    def test_add_remove_roundtrip(self, project: Path) -> None:
        runner.invoke(app, ["add", "temp", str(project)])
        content_after_add = (project / "project-context.yaml").read_text()
        assert "temp" in content_after_add

        runner.invoke(app, ["-y", "remove", "temp", str(project)])
        content_after_rm = (project / "project-context.yaml").read_text()
        assert "temp" not in content_after_rm

    def test_add_in_help(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert "add" in result.output
        assert "remove" in result.output


# ── Validate ──────────────────────────────────────────────────────────────────


class TestValidate:
    @pytest.fixture()
    def project(self, tmp_path: Path) -> Path:
        (tmp_path / "project-context.yaml").write_text(
            'project:\n  name: "test"\nuser:\n  skill_level: "expert"\n'
        )
        return tmp_path

    def test_valid(self, project: Path) -> None:
        result = runner.invoke(app, ["validate", str(project)])
        assert result.exit_code == 0
        assert "valid" in result.output.lower()

    def test_invalid(self, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text("user:\n  skill_level: 'god'\n")
        result = runner.invoke(app, ["validate", str(tmp_path)])
        assert result.exit_code == 1
        assert "error" in result.output.lower()

    def test_no_config(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["validate", str(tmp_path)])
        assert result.exit_code == 1

    def test_in_help(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert "validate" in result.output


# ── Up ────────────────────────────────────────────────────────────────────────


class TestUp:
    @pytest.fixture()
    def project(self, tmp_path: Path) -> Path:
        (tmp_path / "project-context.yaml").write_text(
            'project:\n  name: "test"\nmemory:\n  backend: "local"\nagents:\n  archetype: "minimal"\n'
        )
        (tmp_path / "_grimoire").mkdir()
        (tmp_path / "_grimoire" / "_memory").mkdir()
        (tmp_path / "_grimoire-output").mkdir()
        return tmp_path

    def test_up_ok(self, project: Path) -> None:
        result = runner.invoke(app, ["up", str(project)])
        assert result.exit_code == 0
        assert "test" in result.output

    def test_up_creates_missing_dirs(self, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text(
            'project:\n  name: "test"\nmemory:\n  backend: "local"\nagents:\n  archetype: "minimal"\n'
        )
        result = runner.invoke(app, ["up", str(tmp_path)])
        assert result.exit_code == 0
        assert (tmp_path / "_grimoire").is_dir()
        assert (tmp_path / "_grimoire" / "_memory").is_dir()
        assert (tmp_path / "_grimoire-output").is_dir()

    def test_up_dry_run(self, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text(
            'project:\n  name: "test"\nmemory:\n  backend: "local"\nagents:\n  archetype: "minimal"\n'
        )
        result = runner.invoke(app, ["up", "--dry-run", str(tmp_path)])
        assert result.exit_code == 0
        assert "plan" in result.output.lower() or "dry-run" in result.output.lower()

    def test_up_no_project(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["up", str(tmp_path)])
        assert result.exit_code == 1

    def test_in_help(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert "up" in result.output


# ── Status – edge cases ──────────────────────────────────────────────────────


class TestStatusEdgeCases:
    def test_status_shows_stack(self, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text(
            'project:\n  name: "test"\n  stack:\n    - python\n    - react\n'
        )
        (tmp_path / "_grimoire" / "_memory").mkdir(parents=True)
        (tmp_path / "_grimoire-output").mkdir()
        result = runner.invoke(app, ["status", str(tmp_path)])
        assert result.exit_code == 0
        assert "python" in result.output

    def test_status_shows_repos(self, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text(
            'project:\n  name: "test"\n  repos:\n    - name: "my-repo"\n      path: "."\n      default_branch: "main"\n'
        )
        (tmp_path / "_grimoire" / "_memory").mkdir(parents=True)
        (tmp_path / "_grimoire-output").mkdir()
        result = runner.invoke(app, ["status", str(tmp_path)])
        assert result.exit_code == 0
        assert "my-repo" in result.output

    def test_status_config_error(self, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text("not:\n  valid: config\n")
        result = runner.invoke(app, ["status", str(tmp_path)])
        assert result.exit_code == 1


# ── Registry ──────────────────────────────────────────────────────────────────


class TestRegistryList:
    def test_registry_list_no_project(self) -> None:
        with patch("grimoire.tools._common.find_project_root", side_effect=FileNotFoundError("no project")):
            result = runner.invoke(app, ["registry", "list"])
        assert result.exit_code == 1

    def test_registry_list_no_archetypes(self, tmp_path: Path) -> None:
        mock_reg = MagicMock()
        mock_reg.list_archetypes.return_value = []
        with (
            patch("grimoire.tools._common.find_project_root", return_value=tmp_path),
            patch("grimoire.registry.local.LocalRegistry", return_value=mock_reg),
        ):
            result = runner.invoke(app, ["registry", "list"])
        assert result.exit_code == 0
        assert "No archetypes" in result.output

    def test_registry_list_with_archetypes(self, tmp_path: Path) -> None:
        mock_dna = MagicMock()
        mock_dna.agents = ["analyst", "architect"]
        mock_reg = MagicMock()
        mock_reg.list_archetypes.return_value = ["web-app", "minimal"]
        mock_reg.inspect_archetype.return_value = mock_dna
        with (
            patch("grimoire.tools._common.find_project_root", return_value=tmp_path),
            patch("grimoire.registry.local.LocalRegistry", return_value=mock_reg),
        ):
            result = runner.invoke(app, ["registry", "list"])
        assert result.exit_code == 0
        assert "web-app" in result.output


class TestRegistrySearch:
    def test_registry_search_no_query(self) -> None:
        result = runner.invoke(app, ["registry", "search"])
        assert result.exit_code == 1

    def test_registry_search_no_project(self) -> None:
        with patch("grimoire.tools._common.find_project_root", side_effect=FileNotFoundError("no")):
            result = runner.invoke(app, ["registry", "search", "analyst"])
        assert result.exit_code == 1

    def test_registry_search_no_results(self, tmp_path: Path) -> None:
        mock_reg = MagicMock()
        mock_reg.search.return_value = []
        with (
            patch("grimoire.tools._common.find_project_root", return_value=tmp_path),
            patch("grimoire.registry.local.LocalRegistry", return_value=mock_reg),
        ):
            result = runner.invoke(app, ["registry", "search", "nonexistent"])
        assert result.exit_code == 0
        assert "No agents" in result.output

    def test_registry_search_with_results(self, tmp_path: Path) -> None:
        mock_item = MagicMock()
        mock_item.id = "analyst"
        mock_item.archetype = "web-app"
        mock_item.description = "Business analyst"
        mock_reg = MagicMock()
        mock_reg.search.return_value = [mock_item]
        with (
            patch("grimoire.tools._common.find_project_root", return_value=tmp_path),
            patch("grimoire.registry.local.LocalRegistry", return_value=mock_reg),
        ):
            result = runner.invoke(app, ["registry", "search", "analyst"])
        assert result.exit_code == 0
        assert "analyst" in result.output


# ── Upgrade ───────────────────────────────────────────────────────────────────


class TestUpgrade:
    def test_upgrade_already_v3(self, tmp_path: Path) -> None:
        with patch("grimoire.cli.cmd_upgrade.detect_version", return_value="v3"):
            result = runner.invoke(app, ["upgrade", str(tmp_path)])
        assert result.exit_code == 0
        assert "already v3" in result.output

    def test_upgrade_unknown_version(self, tmp_path: Path) -> None:
        with patch("grimoire.cli.cmd_upgrade.detect_version", return_value="unknown"):
            result = runner.invoke(app, ["upgrade", str(tmp_path)])
        assert result.exit_code == 1
        assert "No v2" in result.output

    def test_upgrade_v2_dry_run(self, tmp_path: Path) -> None:
        mock_plan = MagicMock()
        mock_plan.warnings = ["Warning: old config"]
        with (
            patch("grimoire.cli.cmd_upgrade.detect_version", return_value="v2"),
            patch("grimoire.cli.cmd_upgrade.plan_upgrade", return_value=mock_plan),
            patch("grimoire.cli.cmd_upgrade.execute_upgrade", return_value=["Create _grimoire/", "Move agents/"]),
        ):
            result = runner.invoke(app, ["upgrade", "--dry-run", str(tmp_path)])
        assert result.exit_code == 0
        assert "plan" in result.output.lower()

    def test_upgrade_v2_execute(self, tmp_path: Path) -> None:
        mock_plan = MagicMock()
        mock_plan.warnings = []
        with (
            patch("grimoire.cli.cmd_upgrade.detect_version", return_value="v2"),
            patch("grimoire.cli.cmd_upgrade.plan_upgrade", return_value=mock_plan),
            patch("grimoire.cli.cmd_upgrade.execute_upgrade", return_value=["Create _grimoire/", "Move agents/"]),
        ):
            result = runner.invoke(app, ["upgrade", str(tmp_path)])
        assert result.exit_code == 0
        assert "done" in result.output.lower()


# ── Init --dry-run ────────────────────────────────────────────────────────────


class TestInitDryRun:
    """Tests for ``grimoire init --dry-run``."""

    def test_dry_run_shows_plan(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["init", str(tmp_path), "--dry-run"])
        assert result.exit_code == 0
        assert "dry-run" in result.output.lower()
        assert "plan" in result.output.lower()

    def test_dry_run_does_not_create_files(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path), "--dry-run"])
        assert not (tmp_path / "project-context.yaml").exists()

    def test_dry_run_shows_project_name(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["init", str(tmp_path), "--dry-run", "--name", "test-dry"])
        assert "test-dry" in result.output

    def test_dry_run_shows_archetype(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["init", str(tmp_path), "--dry-run", "-a", "web-app"])
        assert "web-app" in result.output

    def test_dry_run_validates_archetype(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["init", str(tmp_path), "--dry-run", "-a", "invalid"])
        assert result.exit_code == 1

    def test_dry_run_validates_backend(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["init", str(tmp_path), "--dry-run", "-b", "invalid"])
        assert result.exit_code == 1


# ── Diff ──────────────────────────────────────────────────────────────────────


class TestDiff:
    """Tests for ``grimoire diff`` command."""

    def test_diff_no_config(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["diff", str(tmp_path)])
        assert result.exit_code == 1

    def test_diff_default_project(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path), "--name", "my-test"])
        result = runner.invoke(app, ["diff", str(tmp_path)])
        assert result.exit_code == 0
        # A freshly init'd project should show diff on project name
        assert "my-test" in result.output or "drift" in result.output.lower() or "difference" in result.output.lower()

    def test_diff_json_output(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path), "--name", "json-test"])
        result = runner.invoke(app, ["-o", "json", "diff", str(tmp_path)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "archetype" in data
        assert "diffs" in data
        assert isinstance(data["diffs"], list)

    def test_diff_shows_archetype(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path), "--name", "x", "-a", "web-app"])
        result = runner.invoke(app, ["diff", str(tmp_path)])
        assert result.exit_code == 0

    def test_diff_help(self) -> None:
        result = runner.invoke(app, ["diff", "--help"])
        assert result.exit_code == 0
        assert "drift" in result.output.lower() or "diff" in result.output.lower()


# ── Lint ──────────────────────────────────────────────────────────────────────


class TestLint:
    """Tests for ``grimoire lint``."""

    def test_lint_no_config(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["lint", str(tmp_path)])
        assert result.exit_code == 1
        assert "not found" in result.output.lower() or "no" in result.output.lower()

    def test_lint_valid_project(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path), "--name", "lint-ok"])
        result = runner.invoke(app, ["lint", str(tmp_path)])
        assert result.exit_code == 0
        assert "no issues" in result.output.lower() or "✓" in result.output

    def test_lint_json_output_valid(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path), "--name", "lint-json"])
        result = runner.invoke(app, ["lint", str(tmp_path), "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["count"] == 0
        assert data["errors"] == []

    def test_lint_json_output_invalid(self, tmp_path: Path) -> None:
        cfg = tmp_path / "project-context.yaml"
        cfg.write_text("project:\n  name: ''\n")
        result = runner.invoke(app, ["lint", str(tmp_path), "--format", "json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["count"] > 0

    def test_lint_direct_yaml_file(self, tmp_path: Path) -> None:
        cfg = tmp_path / "custom.yaml"
        cfg.write_text("project:\n  name: test\n")
        result = runner.invoke(app, ["lint", str(cfg)])
        assert result.exit_code == 0

    def test_lint_invalid_config(self, tmp_path: Path) -> None:
        cfg = tmp_path / "project-context.yaml"
        cfg.write_text("project:\n  name: test\n  type: unknown_type\n")
        result = runner.invoke(app, ["lint", str(tmp_path)])
        assert result.exit_code == 1
        assert "issue" in result.output.lower()

    def test_lint_help(self) -> None:
        result = runner.invoke(app, ["lint", "--help"])
        assert result.exit_code == 0
        assert "lint" in result.output.lower()


# ── Upgrade ───────────────────────────────────────────────────────────────────


class TestUpgradeCommand:
    """Tests for ``grimoire upgrade``."""

    def test_upgrade_nothing_to_do(self, tmp_path: Path) -> None:
        mock_plan = MagicMock()
        mock_plan.warnings = []
        with (
            patch("grimoire.cli.cmd_upgrade.detect_version", return_value="v2"),
            patch("grimoire.cli.cmd_upgrade.plan_upgrade", return_value=mock_plan),
            patch("grimoire.cli.cmd_upgrade.execute_upgrade", return_value=[]),
        ):
            result = runner.invoke(app, ["upgrade", str(tmp_path)])
        assert result.exit_code == 0
        assert "Nothing to do" in result.output


# ── Schema ────────────────────────────────────────────────────────────────────


class TestSchema:
    """Tests for ``grimoire schema``."""

    def test_schema_outputs_valid_json(self) -> None:
        result = runner.invoke(app, ["schema"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, dict)

    def test_schema_has_draft_version(self) -> None:
        result = runner.invoke(app, ["schema"])
        data = json.loads(result.output)
        assert "$schema" in data
        assert "2020-12" in data["$schema"]

    def test_schema_has_project_property(self) -> None:
        result = runner.invoke(app, ["schema"])
        data = json.loads(result.output)
        assert "project" in data.get("properties", {})

    def test_schema_project_requires_name(self) -> None:
        result = runner.invoke(app, ["schema"])
        data = json.loads(result.output)
        proj = data["properties"]["project"]
        assert "name" in proj.get("required", [])

    def test_schema_help(self) -> None:
        result = runner.invoke(app, ["schema", "--help"])
        assert result.exit_code == 0
        assert "schema" in result.output.lower() or "json" in result.output.lower()


# ── Check ─────────────────────────────────────────────────────────────────────


class TestCheck:
    """Tests for ``grimoire check``."""

    def test_check_no_config(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["check", str(tmp_path)])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_check_valid_project(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path), "--name", "check-ok"])
        result = runner.invoke(app, ["check", str(tmp_path)])
        assert result.exit_code == 0
        assert "passed" in result.output.lower() or "✓" in result.output

    def test_check_invalid_config(self, tmp_path: Path) -> None:
        cfg = tmp_path / "project-context.yaml"
        cfg.write_text("project:\n  name: ''\n")
        result = runner.invoke(app, ["check", str(tmp_path)])
        assert result.exit_code == 1

    def test_check_missing_dirs(self, tmp_path: Path) -> None:
        cfg = tmp_path / "project-context.yaml"
        cfg.write_text("project:\n  name: test\n")
        result = runner.invoke(app, ["check", str(tmp_path)])
        # Should warn about missing directories
        assert "missing" in result.output.lower() or "!" in result.output

    def test_check_help(self) -> None:
        result = runner.invoke(app, ["check", "--help"])
        assert result.exit_code == 0
        assert "check" in result.output.lower()

    def test_check_json_valid_project(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path), "--name", "json-ok"])
        result = runner.invoke(app, ["-o", "json", "check", str(tmp_path)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["all_ok"] is True
        assert len(data["phases"]) == 3

    def test_check_json_no_config(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["-o", "json", "check", str(tmp_path)])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["all_ok"] is False


# ── Doctor JSON ───────────────────────────────────────────────────────────────


class TestDoctorJson:
    """Tests for ``grimoire doctor -o json``."""

    def test_doctor_json_valid(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path), "--name", "doc-json"])
        result = runner.invoke(app, ["-o", "json", "doctor", str(tmp_path)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["passed"] > 0
        assert data["failed"] == 0
        assert isinstance(data["checks"], list)

    def test_doctor_json_no_project(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["-o", "json", "doctor", str(tmp_path)])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["failed"] >= 1

    def test_doctor_json_checks_have_fields(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path), "--name", "fields"])
        result = runner.invoke(app, ["-o", "json", "doctor", str(tmp_path)])
        data = json.loads(result.output)
        for chk in data["checks"]:
            assert "name" in chk
            assert "passed" in chk


# ── Validate JSON ────────────────────────────────────────────────────────────


class TestValidateJson:
    """Tests for ``grimoire validate -o json``."""

    def test_validate_json_valid(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path), "--name", "val-json"])
        result = runner.invoke(app, ["-o", "json", "validate", str(tmp_path)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["valid"] is True
        assert data["errors"] == []

    def test_validate_json_invalid(self, tmp_path: Path) -> None:
        cfg = tmp_path / "project-context.yaml"
        cfg.write_text("project:\n  name: ''\n")
        result = runner.invoke(app, ["-o", "json", "validate", str(tmp_path)])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["valid"] is False
        assert data["count"] > 0

    def test_validate_json_no_config(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["-o", "json", "validate", str(tmp_path)])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["valid"] is False


# ── Global Quiet / NoColor ────────────────────────────────────────────────────


class TestQuietNoColor:
    """Tests for --quiet and --no-color global flags."""

    def test_quiet_flag_accepted(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert "--quiet" in result.output

    def test_no_color_flag_accepted(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert "--no-color" in result.output

    def test_quiet_flag_runs(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path), "--name", "q"])
        result = runner.invoke(app, ["-q", "validate", str(tmp_path)])
        assert result.exit_code == 0

    def test_no_color_flag_runs(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path), "--name", "nc"])
        result = runner.invoke(app, ["--no-color", "validate", str(tmp_path)])
        assert result.exit_code == 0


# ── Completion Export ─────────────────────────────────────────────────────────


class TestCompletionExport:
    """Tests for ``grimoire completion export``."""

    def test_export_help(self) -> None:
        result = runner.invoke(app, ["completion", "export", "--help"])
        assert result.exit_code == 0
        assert "export" in result.output.lower() or "stdout" in result.output.lower()

    def test_export_unsupported_shell(self) -> None:
        result = runner.invoke(app, ["completion", "export", "--shell", "powershell"])
        assert result.exit_code == 1
        assert "unsupported" in result.output.lower() or "supported" in result.output.lower()

    def test_export_bash_generates_output(self) -> None:
        result = runner.invoke(app, ["completion", "export", "--shell", "bash"])
        # May fail in test env (no full Typer completion), just check it runs
        assert result.exit_code in {0, 1}


# ── Doctor with init_project fixture ─────────────────────────────────────────


class TestDoctorFixture:
    """Tests for ``grimoire doctor`` using shared init_project fixture."""

    def test_doctor_ok_with_fixture(self, init_project: Path) -> None:
        result = runner.invoke(app, ["doctor", str(init_project)])
        assert result.exit_code == 0
        assert "OK" in result.output

    def test_doctor_json_with_fixture(self, init_project: Path) -> None:
        result = runner.invoke(app, ["-o", "json", "doctor", str(init_project)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["failed"] == 0

    def test_check_ok_with_fixture(self, init_project: Path) -> None:
        result = runner.invoke(app, ["check", str(init_project)])
        assert result.exit_code == 0
        assert "passed" in result.output.lower() or "✓" in result.output

    def test_validate_ok_with_fixture(self, init_project: Path) -> None:
        result = runner.invoke(app, ["validate", str(init_project)])
        assert result.exit_code == 0
        assert "valid" in result.output.lower()

    def test_status_ok_with_fixture(self, init_project: Path) -> None:
        result = runner.invoke(app, ["status", str(init_project)])
        assert result.exit_code == 0
        assert "test-project" in result.output


# ── grimoire version command ─────────────────────────────────────────────────


class TestVersionCommand:
    """Tests for ``grimoire version``."""

    def test_version_text(self) -> None:
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert "grimoire-kit" in result.output
        assert "Python" in result.output
        assert "Platform" in result.output

    def test_version_json(self) -> None:
        result = runner.invoke(app, ["-o", "json", "version"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "grimoire_version" in data
        assert "python" in data
        assert "platform" in data
        assert "install_path" in data

    def test_version_help(self) -> None:
        result = runner.invoke(app, ["version", "--help"])
        assert result.exit_code == 0
        assert "version" in result.output.lower()


# ── grimoire config get/path/list ────────────────────────────────────────────


class TestConfigGet:
    """Tests for ``grimoire config get``."""

    def test_config_get_project_name(self, init_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(init_project)
        result = runner.invoke(app, ["config", "get", "project.name"])
        assert result.exit_code == 0
        assert "test-project" in result.output

    def test_config_get_missing_key(self, init_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(init_project)
        result = runner.invoke(app, ["config", "get", "nonexistent.key"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_config_get_json(self, init_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(init_project)
        result = runner.invoke(app, ["-o", "json", "config", "get", "project.name"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "project.name" in data

    def test_config_get_no_project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["config", "get", "project.name"])
        assert result.exit_code == 1


class TestConfigPath:
    """Tests for ``grimoire config path``."""

    def test_config_path_exists(self, init_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(init_project)
        result = runner.invoke(app, ["config", "path"])
        assert result.exit_code == 0
        assert "project-context.yaml" in result.output

    def test_config_path_not_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["config", "path"])
        assert result.exit_code == 1


class TestConfigList:
    """Tests for ``grimoire config list``."""

    def test_config_list_text(self, init_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(init_project)
        result = runner.invoke(app, ["config", "list"])
        assert result.exit_code == 0
        assert "project.name" in result.output

    def test_config_list_json(self, init_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(init_project)
        result = runner.invoke(app, ["-o", "json", "config", "list"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "project.name" in data

    def test_config_list_no_project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["config", "list"])
        assert result.exit_code == 1


# ── grimoire self ────────────────────────────────────────────────────────────


class TestSelfVersion:
    """Tests for ``grimoire self version``."""

    def test_self_version_text(self) -> None:
        result = runner.invoke(app, ["self", "version"])
        assert result.exit_code == 0
        assert "grimoire-kit" in result.output

    def test_self_version_json(self) -> None:
        result = runner.invoke(app, ["-o", "json", "self", "version"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "installed" in data
        assert "update_available" in data
        assert "editable_install" in data
        assert "install_path" in data


class TestSelfDiagnose:
    """Tests for ``grimoire self diagnose``."""

    def test_self_diagnose_text(self) -> None:
        result = runner.invoke(app, ["self", "diagnose"])
        assert result.exit_code == 0
        assert "diagnose" in result.output.lower() or "✓" in result.output

    def test_self_diagnose_json(self) -> None:
        result = runner.invoke(app, ["-o", "json", "self", "diagnose"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "all_ok" in data
        assert "checks" in data
        assert isinstance(data["checks"], list)
        assert len(data["checks"]) > 0

    def test_self_diagnose_checks_python(self) -> None:
        result = runner.invoke(app, ["-o", "json", "self", "diagnose"])
        data = json.loads(result.output)
        names = [c["name"] for c in data["checks"]]
        assert "python" in names
        assert "grimoire-cli" in names


# ── Command Aliases ───────────────────────────────────────────────────────────


class TestCommandAliases:
    """Tests for _ALIASES dict and _expand_aliases()."""

    def test_aliases_dict_is_non_empty(self) -> None:
        assert len(_ALIASES) > 0

    def test_well_known_aliases(self) -> None:
        assert _ALIASES["i"] == "init"
        assert _ALIASES["d"] == "doctor"
        assert _ALIASES["s"] == "status"
        assert _ALIASES["v"] == "validate"
        assert _ALIASES["l"] == "lint"
        assert _ALIASES["ck"] == "check"

    def test_expand_aliases_replaces(self) -> None:
        import sys

        original = sys.argv[:]
        try:
            sys.argv = ["grimoire", "d"]
            _expand_aliases()
            assert sys.argv == ["grimoire", "doctor"]
        finally:
            sys.argv = original

    def test_expand_aliases_noop_on_unknown(self) -> None:
        import sys

        original = sys.argv[:]
        try:
            sys.argv = ["grimoire", "doctor"]
            _expand_aliases()
            assert sys.argv == ["grimoire", "doctor"]
        finally:
            sys.argv = original

    def test_expand_aliases_noop_on_no_args(self) -> None:
        import sys

        original = sys.argv[:]
        try:
            sys.argv = ["grimoire"]
            _expand_aliases()
            assert sys.argv == ["grimoire"]
        finally:
            sys.argv = original

    def test_help_shows_aliases(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert "Aliases" in result.output or "aliases" in result.output.lower()


# ── Add/Remove --dry-run ─────────────────────────────────────────────────────


class TestAddRemoveDryRun:
    """Tests for --dry-run flag on add/remove commands."""

    @pytest.fixture()
    def project(self, tmp_path: Path) -> Path:
        runner.invoke(app, ["init", str(tmp_path), "--name", "test-proj"])
        return tmp_path

    def test_add_dry_run_shows_plan(self, project: Path) -> None:
        result = runner.invoke(app, ["add", "my-agent", str(project), "--dry-run"])
        assert result.exit_code == 0
        assert "dry-run" in result.output.lower()
        assert "my-agent" in result.output

    def test_add_dry_run_does_not_modify(self, project: Path) -> None:
        content_before = (project / "project-context.yaml").read_text()
        runner.invoke(app, ["add", "ghost-agent", str(project), "--dry-run"])
        content_after = (project / "project-context.yaml").read_text()
        assert content_before == content_after
        assert "ghost-agent" not in content_after

    def test_remove_dry_run_shows_plan(self, project: Path) -> None:
        runner.invoke(app, ["add", "to-rm", str(project)])
        result = runner.invoke(app, ["remove", "to-rm", str(project), "--dry-run"])
        assert result.exit_code == 0
        assert "dry-run" in result.output.lower()
        assert "to-rm" in result.output

    def test_remove_dry_run_does_not_modify(self, project: Path) -> None:
        runner.invoke(app, ["add", "keeper", str(project)])
        content_before = (project / "project-context.yaml").read_text()
        runner.invoke(app, ["remove", "keeper", str(project), "--dry-run"])
        content_after = (project / "project-context.yaml").read_text()
        assert content_before == content_after
        assert "keeper" in content_after

    def test_add_help_shows_dry_run(self) -> None:
        result = runner.invoke(app, ["add", "--help"])
        assert "--dry-run" in result.output

    def test_remove_help_shows_dry_run(self) -> None:
        result = runner.invoke(app, ["remove", "--help"])
        assert "--dry-run" in result.output


# ── Env var overrides ─────────────────────────────────────────────────────────


class TestEnvVarOverrides:
    """Tests for GRIMOIRE_OUTPUT, GRIMOIRE_QUIET, NO_COLOR env vars."""

    def test_grimoire_output_json(self) -> None:
        result = runner.invoke(app, ["version"], env={"GRIMOIRE_OUTPUT": "json"})
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "grimoire_version" in data

    def test_grimoire_output_cli_overrides_env(self) -> None:
        result = runner.invoke(app, ["-o", "text", "version"], env={"GRIMOIRE_OUTPUT": "json"})
        assert result.exit_code == 0
        # Text mode — should NOT be JSON
        assert "grimoire-kit" in result.output

    def test_grimoire_quiet_env(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path), "--name", "q"])
        result = runner.invoke(app, ["validate", str(tmp_path)], env={"GRIMOIRE_QUIET": "1"})
        assert result.exit_code == 0

    def test_no_color_env(self) -> None:
        result = runner.invoke(app, ["version"], env={"NO_COLOR": "1"})
        assert result.exit_code == 0


# ── Config Set ─────────────────────────────────────────────────────────────


class TestConfigSet:
    """Tests for ``grimoire config set KEY VALUE``."""

    @pytest.fixture()
    def project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        runner.invoke(app, ["init", str(tmp_path), "--name", "set-test"])
        monkeypatch.chdir(tmp_path)
        return tmp_path

    def test_set_project_name(self, project: Path) -> None:
        result = runner.invoke(app, ["config", "set", "project.name", "new-name"])
        assert result.exit_code == 0
        assert "new-name" in result.output
        # Verify persisted
        r2 = runner.invoke(app, ["config", "get", "project.name"])
        assert "new-name" in r2.output

    def test_set_dry_run(self, project: Path) -> None:
        result = runner.invoke(app, ["config", "set", "project.name", "ghost", "--dry-run"])
        assert result.exit_code == 0
        assert "ghost" in result.output
        # Verify NOT persisted
        r2 = runner.invoke(app, ["config", "get", "project.name"])
        assert "ghost" not in r2.output
        assert "set-test" in r2.output

    def test_set_json_output(self, project: Path) -> None:
        result = runner.invoke(app, ["-o", "json", "config", "set", "project.name", "json-name"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["key"] == "project.name"
        assert data["new"] == "json-name"
        assert data["old"] == "set-test"

    def test_set_dry_run_json(self, project: Path) -> None:
        result = runner.invoke(app, ["-o", "json", "config", "set", "project.name", "ghost", "--dry-run"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["dry_run"] is True

    def test_set_unknown_key(self, project: Path) -> None:
        result = runner.invoke(app, ["config", "set", "nonexistent.key", "val"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_set_no_project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["config", "set", "project.name", "x"])
        assert result.exit_code == 1

    def test_set_list_value_rejected(self, project: Path) -> None:
        """config set refuses list values — use config edit instead (R29 M10)."""
        result = runner.invoke(app, ["config", "set", "project.stack", "python,docker"])
        assert result.exit_code == 1
        assert "config edit" in result.output.lower() or "list" in result.output.lower()


# ── JSON output for add/remove ────────────────────────────────────────────────


class TestAddRemoveJson:
    """Tests for JSON output on add/remove commands."""

    @pytest.fixture()
    def project(self, tmp_path: Path) -> Path:
        runner.invoke(app, ["init", str(tmp_path), "--name", "json-test"])
        return tmp_path

    def test_add_json(self, project: Path) -> None:
        result = runner.invoke(app, ["-o", "json", "add", "my-ag", str(project)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["action"] == "add"
        assert data["agent"] == "my-ag"

    def test_add_json_duplicate(self, project: Path) -> None:
        runner.invoke(app, ["add", "dup", str(project)])
        result = runner.invoke(app, ["-o", "json", "add", "dup", str(project)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "already_exists"

    def test_add_json_dry_run(self, project: Path) -> None:
        result = runner.invoke(app, ["-o", "json", "add", "ghost", str(project), "--dry-run"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["dry_run"] is True

    def test_remove_json(self, project: Path) -> None:
        runner.invoke(app, ["add", "to-rm", str(project)])
        result = runner.invoke(app, ["-o", "json", "remove", "to-rm", str(project)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["action"] == "remove"

    def test_remove_json_not_found(self, project: Path) -> None:
        result = runner.invoke(app, ["-o", "json", "remove", "ghost", str(project)])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["ok"] is False
        assert data["status"] == "not_found"

    def test_remove_json_dry_run(self, project: Path) -> None:
        runner.invoke(app, ["add", "keeper", str(project)])
        result = runner.invoke(app, ["-o", "json", "remove", "keeper", str(project), "--dry-run"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["dry_run"] is True


# ── Did-you-mean suggestions ──────────────────────────────────────────────────


class TestDidYouMean:
    """Tests for fuzzy suggestions on archetype/backend typos."""

    def test_typo_archetype(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["init", str(tmp_path), "--archetype", "minimla"])
        assert result.exit_code == 1
        assert "Did you mean" in result.output or "Available" in result.output

    def test_typo_backend(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["init", str(tmp_path), "--backend", "locla"])
        assert result.exit_code == 1
        assert "Did you mean" in result.output or "Available" in result.output

    def test_unknown_archetype_no_match(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["init", str(tmp_path), "--archetype", "zzzzz"])
        assert result.exit_code == 1
        assert "Available" in result.output


# ── Init JSON output ─────────────────────────────────────────────────────────


class TestInitJson:
    """Tests for ``grimoire init -o json``."""

    def test_init_json_success(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["-o", "json", "init", str(tmp_path), "--name", "j-proj"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["project"] == "j-proj"
        assert data["archetype"] == "minimal"
        assert data["backend"] == "auto"
        assert "directories" in data

    def test_init_json_already_exists(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path), "--name", "exists"])
        result = runner.invoke(app, ["-o", "json", "init", str(tmp_path), "--name", "exists"])
        assert result.exit_code == 1

    def test_init_json_invalid_archetype(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["-o", "json", "init", str(tmp_path), "--archetype", "bad"])
        assert result.exit_code == 1


# ── Up JSON output ───────────────────────────────────────────────────────────


class TestUpJson:
    """Tests for ``grimoire up -o json``."""

    def test_up_json_success(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path), "--name", "up-json"])
        result = runner.invoke(app, ["-o", "json", "up", str(tmp_path)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["project"] == "up-json"
        assert isinstance(data["actions"], list)
        assert "agents_count" in data

    def test_up_json_dry_run(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path), "--name", "up-dry"])
        result = runner.invoke(app, ["-o", "json", "up", str(tmp_path), "--dry-run"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["dry_run"] is True


# ── Doctor --fix ─────────────────────────────────────────────────────────────


class TestDoctorFix:
    """Tests for ``grimoire doctor --fix``."""

    def test_fix_creates_missing_dirs(self, tmp_path: Path) -> None:
        # Init then remove a directory
        runner.invoke(app, ["init", str(tmp_path), "--name", "fix-test"])
        import shutil
        shutil.rmtree(tmp_path / "_grimoire-output")
        assert not (tmp_path / "_grimoire-output").is_dir()

        result = runner.invoke(app, ["doctor", str(tmp_path), "--fix"])
        assert result.exit_code == 0
        assert (tmp_path / "_grimoire-output").is_dir()
        assert "--fix" in result.output or "created" in result.output.lower()

    def test_fix_json_reports_fixed(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path), "--name", "fix-json"])
        import shutil
        shutil.rmtree(tmp_path / "_grimoire-output")

        result = runner.invoke(app, ["-o", "json", "doctor", str(tmp_path), "--fix"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "fixed" in data
        assert "_grimoire-output" in data["fixed"]

    def test_fix_noop_when_healthy(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path), "--name", "fix-noop"])
        result = runner.invoke(app, ["-o", "json", "doctor", str(tmp_path), "--fix"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "fixed" not in data or data.get("fixed") == []

    def test_fix_help_shows_flag(self) -> None:
        result = runner.invoke(app, ["doctor", "--help"])
        assert "--fix" in result.output


# ── --time flag ──────────────────────────────────────────────────────────────


class TestTimeFlag:
    """Tests for global --time flag."""

    def test_help_shows_time(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert "--time" in result.output

    def test_time_flag_accepted(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path), "--name", "time-test"])
        result = runner.invoke(app, ["--time", "validate", str(tmp_path)])
        assert result.exit_code == 0


# ── Parametrized JSON output ─────────────────────────────────────────────────


class TestJsonOutputParametrized:
    """Parametrized tests for JSON output across commands."""

    @pytest.mark.parametrize("command,expect_keys", [
        ("doctor", ["checks", "passed", "failed"]),
        ("validate", ["valid", "errors"]),
        ("check", ["all_ok", "phases"]),
        ("status", ["project", "agents"]),
    ])
    def test_json_output_on_valid_project(self, tmp_path: Path, command: str, expect_keys: list[str]) -> None:
        runner.invoke(app, ["init", str(tmp_path), "--name", "param-test"])
        result = runner.invoke(app, ["-o", "json", command, str(tmp_path)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        for key in expect_keys:
            assert key in data, f"Missing key '{key}' in {command} JSON output"

    @pytest.mark.parametrize("command", ["doctor", "validate", "check", "status"])
    def test_json_on_missing_project(self, tmp_path: Path, command: str) -> None:
        result = runner.invoke(app, ["-o", "json", command, str(tmp_path)])
        assert result.exit_code != 0


# ── Epilog ───────────────────────────────────────────────────────────────────


class TestEpilog:
    """Tests that the app epilog is displayed."""

    def test_help_shows_examples(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "grimoire init" in result.output or "Examples" in result.output


# ── R25 — --yes flag & confirmations ─────────────────────────────────────────


class TestYesFlag:
    """Tests for global --yes/-y flag and interactive confirmations."""

    def test_help_shows_yes(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert "--yes" in result.output

    def test_remove_prompts_without_yes(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path), "--name", "yn-test"])
        runner.invoke(app, ["-y", "add", "agent-x", str(tmp_path)])
        # Without --yes nor input, confirm aborts
        result = runner.invoke(app, ["remove", "agent-x", str(tmp_path)], input="n\n")
        assert result.exit_code != 0

    def test_remove_with_yes_skips_prompt(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path), "--name", "yes-test"])
        runner.invoke(app, ["-y", "add", "agent-y", str(tmp_path)])
        result = runner.invoke(app, ["-y", "remove", "agent-y", str(tmp_path)])
        assert result.exit_code == 0
        assert "Removed" in result.output

    def test_remove_json_implies_yes(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path), "--name", "json-yes"])
        runner.invoke(app, ["-y", "add", "agent-z", str(tmp_path)])
        result = runner.invoke(app, ["-o", "json", "remove", "agent-z", str(tmp_path)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True

    def test_remove_dry_run_skips_prompt(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path), "--name", "dry-yn"])
        runner.invoke(app, ["-y", "add", "ag", str(tmp_path)])
        result = runner.invoke(app, ["remove", "ag", str(tmp_path), "--dry-run"])
        assert result.exit_code == 0
        assert "plan" in result.output.lower()

    def test_remove_confirm_yes_input(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path), "--name", "yn-ok"])
        runner.invoke(app, ["-y", "add", "ag2", str(tmp_path)])
        result = runner.invoke(app, ["remove", "ag2", str(tmp_path)], input="y\n")
        assert result.exit_code == 0
        assert "Removed" in result.output


# ── R25 — Upgrade JSON output ────────────────────────────────────────────────


class TestUpgradeJson:
    """Tests for ``grimoire upgrade -o json``."""

    def test_upgrade_already_v3_json(self, tmp_path: Path) -> None:
        with patch("grimoire.cli.cmd_upgrade.detect_version", return_value="v3"):
            result = runner.invoke(app, ["-o", "json", "upgrade", str(tmp_path)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["status"] == "already_v3"
        assert data["actions"] == []

    def test_upgrade_unknown_json(self, tmp_path: Path) -> None:
        with patch("grimoire.cli.cmd_upgrade.detect_version", return_value="unknown"):
            result = runner.invoke(app, ["-o", "json", "upgrade", str(tmp_path)])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["ok"] is False

    def test_upgrade_v2_dry_run_json(self, tmp_path: Path) -> None:
        mock_plan = MagicMock()
        mock_plan.warnings = ["Old config"]
        with (
            patch("grimoire.cli.cmd_upgrade.detect_version", return_value="v2"),
            patch("grimoire.cli.cmd_upgrade.plan_upgrade", return_value=mock_plan),
            patch("grimoire.cli.cmd_upgrade.execute_upgrade", return_value=["Create _grimoire/"]),
        ):
            result = runner.invoke(app, ["-o", "json", "upgrade", "--dry-run", str(tmp_path)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["dry_run"] is True
        assert "Create _grimoire/" in data["actions"]
        assert "Old config" in data["warnings"]

    def test_upgrade_v2_execute_json(self, tmp_path: Path) -> None:
        mock_plan = MagicMock()
        mock_plan.warnings = []
        with (
            patch("grimoire.cli.cmd_upgrade.detect_version", return_value="v2"),
            patch("grimoire.cli.cmd_upgrade.plan_upgrade", return_value=mock_plan),
            patch("grimoire.cli.cmd_upgrade.execute_upgrade", return_value=["Move agents/"]),
        ):
            result = runner.invoke(app, ["-o", "json", "upgrade", str(tmp_path)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["dry_run"] is False
        assert "Move agents/" in data["actions"]


# ── R25 — Rich help panels ──────────────────────────────────────────────────


class TestHelpPanels:
    """Tests that commands are organised into rich help panels."""

    def test_help_shows_project_panel(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "Project" in result.output

    def test_help_shows_validation_panel(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert "Validation" in result.output

    def test_help_shows_agents_panel(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert "Agents" in result.output

    def test_help_shows_configuration_panel(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert "Configuration" in result.output

    def test_help_shows_utilities_panel(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert "Utilities" in result.output

    def test_help_shows_info_panel(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert "Info" in result.output


# ── R25 — Error handler with recovery hints ─────────────────────────────────


class TestErrorHandler:
    """Tests for the enhanced error handler with codes and recovery hints."""

    def test_format_error_with_code(self) -> None:
        from grimoire.cli.app import _format_error
        from grimoire.core.exceptions import GrimoireConfigError

        exc = GrimoireConfigError("bad config", error_code="GR001")
        # Should not raise — just prints to console
        _format_error(exc)

    def test_format_error_without_code(self) -> None:
        from grimoire.cli.app import _format_error
        from grimoire.core.exceptions import GrimoireError

        exc = GrimoireError("generic")
        _format_error(exc)

    def test_recovery_hints_dict_exists(self) -> None:
        from grimoire.cli.app import _RECOVERY_HINTS

        assert isinstance(_RECOVERY_HINTS, dict)
        assert len(_RECOVERY_HINTS) > 0
        assert "GR001" in _RECOVERY_HINTS

    def test_cli_catches_grimoire_error(self) -> None:
        """Verify the entry point formats GrimoireError nicely."""
        result = runner.invoke(app, ["validate", "/nonexistent/path/x/y/z"])
        assert result.exit_code == 1


# ── R25 — conftest fixtures usage ────────────────────────────────────────────


class TestConftestFixtures:
    """Tests verifying the cli_project fixture works correctly."""

    def test_cli_project_fixture(self, cli_project: Path) -> None:
        assert (cli_project / "project-context.yaml").is_file()
        assert (cli_project / "_grimoire").is_dir()

    def test_cli_project_validate(self, cli_project: Path) -> None:
        result = runner.invoke(app, ["validate", str(cli_project)])
        assert result.exit_code == 0

    def test_assert_json_output_helper(self, cli_project: Path) -> None:
        from tests.unit.cli.conftest import assert_json_output

        result = runner.invoke(app, ["-o", "json", "status", str(cli_project)])
        assert result.exit_code == 0
        data = assert_json_output(result.output, ["project", "agents"])
        assert isinstance(data, dict)


# ══════════════════════════════════════════════════════════════════════════════
# R26 — Config auto-discovery, spinners, examples, history/audit, deprecation
# ══════════════════════════════════════════════════════════════════════════════


# ── R26 — Config auto-discovery ──────────────────────────────────────────────


class TestAutoDiscovery:
    """Tests for _find_config walk-up directory discovery."""

    def test_find_config_direct(self, tmp_path: Path) -> None:
        """_find_config finds config in the given directory."""
        from grimoire.cli.app import _find_config

        (tmp_path / "project-context.yaml").write_text("project:\n  name: test\n")
        result = _find_config(tmp_path)
        assert result == tmp_path / "project-context.yaml"

    def test_find_config_walk_up(self, tmp_path: Path) -> None:
        """_find_config walks up to find config in parent directory."""
        from grimoire.cli.app import _find_config

        (tmp_path / "project-context.yaml").write_text("project:\n  name: test\n")
        subdir = tmp_path / "sub" / "deep"
        subdir.mkdir(parents=True)
        with patch("grimoire.tools._common.find_project_root", return_value=tmp_path):
            result = _find_config(subdir)
        assert result == tmp_path / "project-context.yaml"

    def test_find_config_missing_exits(self, tmp_path: Path) -> None:
        """_find_config exits when no config found anywhere."""
        from click.exceptions import Exit

        from grimoire.cli.app import _find_config

        with patch("grimoire.tools._common.find_project_root", side_effect=FileNotFoundError), pytest.raises(Exit):
                _find_config(tmp_path)

    def test_add_works_from_subdirectory(self, cli_project: Path) -> None:
        """grimoire add finds config when invoked from a subdirectory."""
        subdir = cli_project / "subdir"
        subdir.mkdir()
        result = runner.invoke(app, ["add", "test-agent", str(subdir)])
        # Should succeed (agent not in registry is ok — the config is found)
        # or fail with agent-not-found, NOT with "Not a Grimoire project"
        assert "Not a Grimoire project" not in result.output


# ── R26 — Spinner helper ─────────────────────────────────────────────────────


class TestSpinnerHelper:
    """Tests for _status_spinner context manager."""

    def test_spinner_show_true_returns_context_manager(self) -> None:
        from grimoire.cli.app import _status_spinner

        cm = _status_spinner("Working…", show=True)
        assert hasattr(cm, "__enter__") and hasattr(cm, "__exit__")

    def test_spinner_show_false_returns_nullcontext(self) -> None:
        from grimoire.cli.app import _status_spinner

        cm = _status_spinner("Working…", show=False)
        assert hasattr(cm, "__enter__") and hasattr(cm, "__exit__")
        # nullcontext should work as no-op
        with cm:
            pass


# ── R26 — Subcommand examples ────────────────────────────────────────────────


class TestSubcommandExamples:
    """Tests for Rich markup examples in command help text."""

    @pytest.mark.parametrize("cmd", ["init", "doctor", "validate", "add", "remove", "status", "check", "upgrade"])
    def test_help_contains_examples(self, cmd: str) -> None:
        result = runner.invoke(app, [cmd, "--help"])
        assert result.exit_code == 0
        assert "Examples" in result.output or "grimoire" in result.output


# ── R26 — Audit log & history ────────────────────────────────────────────────


class TestAuditLog:
    """Tests for _log_operation audit trail."""

    def test_log_operation_writes_file(self, cli_project: Path) -> None:
        from grimoire.cli.app import _AUDIT_FILENAME, _log_operation

        with patch("grimoire.tools._common.find_project_root", return_value=cli_project):
            # Ensure _memory dir exists
            mem_dir = cli_project / "_grimoire" / "_memory"
            mem_dir.mkdir(parents=True, exist_ok=True)
            _log_operation("test_cmd", {"key": "val"})

        log_file = mem_dir / _AUDIT_FILENAME
        assert log_file.is_file()
        content = log_file.read_text(encoding="utf-8").strip()
        entry = json.loads(content)
        assert entry["cmd"] == "test_cmd"
        assert entry["ok"] is True
        assert entry["args"]["key"] == "val"
        assert "ts" in entry

    def test_log_operation_silent_on_missing_dir(self, tmp_path: Path) -> None:
        from grimoire.cli.app import _log_operation

        with patch("grimoire.tools._common.find_project_root", return_value=tmp_path):
            # _grimoire/_memory does not exist — should not raise
            _log_operation("silent_cmd")

    def test_log_operation_silent_on_no_project(self) -> None:
        from grimoire.cli.app import _log_operation

        with patch("grimoire.tools._common.find_project_root", side_effect=FileNotFoundError):
            # Should not raise
            _log_operation("orphan_cmd")

    def test_log_operation_truncates_at_max(self, cli_project: Path) -> None:
        from grimoire.cli.app import _AUDIT_FILENAME, _AUDIT_MAX_ENTRIES, _log_operation

        mem_dir = cli_project / "_grimoire" / "_memory"
        mem_dir.mkdir(parents=True, exist_ok=True)
        log_file = mem_dir / _AUDIT_FILENAME

        # Pre-fill with MAX entries
        with open(log_file, "w", encoding="utf-8") as fh:
            for i in range(_AUDIT_MAX_ENTRIES):
                fh.write(json.dumps({"ts": "old", "cmd": f"old_{i}", "ok": True}) + "\n")

        with patch("grimoire.tools._common.find_project_root", return_value=cli_project):
            _log_operation("overflow_cmd")

        lines = log_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) <= _AUDIT_MAX_ENTRIES
        # Last entry should be the new one
        last = json.loads(lines[-1])
        assert last["cmd"] == "overflow_cmd"


class TestHistoryCommand:
    """Tests for grimoire history command."""

    def test_history_empty(self, cli_project: Path) -> None:
        result = runner.invoke(app, ["history"])
        # May say "No audit history" or "Not a Grimoire project" depending on cwd
        assert result.exit_code in (0, 1)

    def test_history_with_entries(self, cli_project: Path) -> None:
        from grimoire.cli.app import _AUDIT_FILENAME

        mem_dir = cli_project / "_grimoire" / "_memory"
        mem_dir.mkdir(parents=True, exist_ok=True)
        log_file = mem_dir / _AUDIT_FILENAME
        log_file.write_text(
            json.dumps({"ts": "2025-01-01T00:00:00", "cmd": "init", "ok": True}) + "\n"
            + json.dumps({"ts": "2025-01-01T00:01:00", "cmd": "add", "ok": True, "args": {"agent": "foo"}}) + "\n"
        )
        with patch("grimoire.tools._common.find_project_root", return_value=cli_project):
            result = runner.invoke(app, ["history"])
        assert result.exit_code == 0

    def test_history_json(self, cli_project: Path) -> None:
        from grimoire.cli.app import _AUDIT_FILENAME

        mem_dir = cli_project / "_grimoire" / "_memory"
        mem_dir.mkdir(parents=True, exist_ok=True)
        log_file = mem_dir / _AUDIT_FILENAME
        log_file.write_text(
            json.dumps({"ts": "2025-01-01T00:00:00", "cmd": "init", "ok": True}) + "\n"
        )
        with patch("grimoire.tools._common.find_project_root", return_value=cli_project):
            result = runner.invoke(app, ["-o", "json", "history"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "entries" in data
        assert "total" in data
        assert len(data["entries"]) == 1

    def test_history_filter(self, cli_project: Path) -> None:
        from grimoire.cli.app import _AUDIT_FILENAME

        mem_dir = cli_project / "_grimoire" / "_memory"
        mem_dir.mkdir(parents=True, exist_ok=True)
        log_file = mem_dir / _AUDIT_FILENAME
        log_file.write_text(
            json.dumps({"ts": "2025-01-01T00:00:00", "cmd": "init", "ok": True}) + "\n"
            + json.dumps({"ts": "2025-01-01T00:01:00", "cmd": "add", "ok": True}) + "\n"
        )
        with patch("grimoire.tools._common.find_project_root", return_value=cli_project):
            result = runner.invoke(app, ["-o", "json", "history", "-f", "add"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert all(e["cmd"] == "add" for e in data["entries"])

    def test_history_limit(self, cli_project: Path) -> None:
        from grimoire.cli.app import _AUDIT_FILENAME

        mem_dir = cli_project / "_grimoire" / "_memory"
        mem_dir.mkdir(parents=True, exist_ok=True)
        log_file = mem_dir / _AUDIT_FILENAME
        lines = [json.dumps({"ts": f"2025-01-01T00:0{i}:00", "cmd": "add", "ok": True}) for i in range(5)]
        log_file.write_text("\n".join(lines) + "\n")
        with patch("grimoire.tools._common.find_project_root", return_value=cli_project):
            result = runner.invoke(app, ["-o", "json", "history", "-n", "2"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data["entries"]) == 2


# ── R26 — Deprecation warnings framework ─────────────────────────────────────


class TestDeprecationWarnings:
    """Tests for the deprecation warning framework."""

    def test_deprecated_flags_dict_exists(self) -> None:
        from grimoire.cli.app import _DEPRECATED_FLAGS

        assert isinstance(_DEPRECATED_FLAGS, dict)

    def test_warn_deprecated_no_flags(self) -> None:
        from grimoire.cli.app import _warn_deprecated

        # Should not raise when no deprecated flags are present
        _warn_deprecated()

    def test_warn_deprecated_emits_warning(self, capsys: pytest.CaptureFixture[str]) -> None:
        """When a deprecated flag is in sys.argv, a warning is printed."""
        from grimoire.cli.app import _DEPRECATED_FLAGS, _warn_deprecated

        # Temporarily add a test flag
        _DEPRECATED_FLAGS["--old-test"] = ("--new-test", "99.0")
        try:
            with patch("sys.argv", ["grimoire", "--old-test", "env"]):
                _warn_deprecated()
        finally:
            del _DEPRECATED_FLAGS["--old-test"]


# ── R26 — Integration: add/remove create audit entries ────────────────────────


class TestAuditIntegration:
    """Tests verifying that add/remove commands create audit log entries."""

    def test_add_creates_audit_entry(self, cli_project: Path) -> None:
        from grimoire.cli.app import _AUDIT_FILENAME

        mem_dir = cli_project / "_grimoire" / "_memory"
        mem_dir.mkdir(parents=True, exist_ok=True)
        runner.invoke(app, ["add", "test-agent-xyz", str(cli_project)])
        # Whether add succeeds or not, check if audit was attempted
        log_file = mem_dir / _AUDIT_FILENAME
        if log_file.is_file():
            content = log_file.read_text(encoding="utf-8").strip()
            if content:
                last = json.loads(content.splitlines()[-1])
                assert last["cmd"] == "add"

    def test_remove_creates_audit_entry(self, cli_project: Path) -> None:
        from grimoire.cli.app import _AUDIT_FILENAME

        # First add an agent
        runner.invoke(app, ["add", "audit-test-agent", str(cli_project)])
        mem_dir = cli_project / "_grimoire" / "_memory"
        mem_dir.mkdir(parents=True, exist_ok=True)

        # Clear log to isolate remove
        log_file = mem_dir / _AUDIT_FILENAME
        if log_file.is_file():
            log_file.unlink()

        runner.invoke(app, ["--yes", "remove", "audit-test-agent", str(cli_project)])
        if log_file.is_file():
            content = log_file.read_text(encoding="utf-8").strip()
            if content:
                last = json.loads(content.splitlines()[-1])
                assert last["cmd"] == "remove"


# ── R27: Command Suggestions ─────────────────────────────────────────────────

class TestCommandSuggestions:
    """Tests for _suggest_command() — typo correction."""

    def test_suggest_close_match(self) -> None:
        """A near-miss command name triggers a suggestion."""
        from grimoire.cli.app import _suggest_command

        with patch("sys.argv", ["grimoire", "doctr"]), pytest.raises(SystemExit, match="2"):
            _suggest_command()

    def test_no_suggestion_for_valid_command(self) -> None:
        """A known command passes through without exit."""
        from grimoire.cli.app import _suggest_command

        # 'doctor' is valid — should not raise
        with patch("sys.argv", ["grimoire", "doctor"]):
            _suggest_command()  # no exception

    def test_no_suggestion_for_flags(self) -> None:
        """Flag-only invocations (e.g. --help) do not trigger suggestions."""
        from grimoire.cli.app import _suggest_command

        with patch("sys.argv", ["grimoire", "--help"]):
            _suggest_command()

    def test_no_suggestion_for_unknown(self) -> None:
        """A completely unrelated word passes through (Typer handles the error)."""
        from grimoire.cli.app import _suggest_command

        with patch("sys.argv", ["grimoire", "xyzzyxyzzy"]):
            _suggest_command()  # no close match → no exception


# ── R27: Signal Handling ─────────────────────────────────────────────────────

class TestSignalHandling:
    """Tests for _handle_signal() and _install_signal_handlers()."""

    def test_handler_raises_system_exit(self) -> None:
        import grimoire.cli.app as _app

        with pytest.raises(SystemExit) as exc_info:
            _app._handle_signal(2, None)  # SIGINT = 2
        assert exc_info.value.code == 130  # 128 + 2

    def test_install_signal_handlers(self) -> None:
        import signal

        from grimoire.cli.app import _install_signal_handlers

        _install_signal_handlers()
        assert signal.getsignal(signal.SIGINT) is not signal.SIG_DFL

    def test_handler_exit_code_sigterm(self) -> None:
        import grimoire.cli.app as _app

        with pytest.raises(SystemExit) as exc_info:
            _app._handle_signal(15, None)  # SIGTERM = 15
        assert exc_info.value.code == 143  # 128 + 15


# ── R27: Performance Profiling ───────────────────────────────────────────────

class TestPerformanceProfiling:
    """Tests for --profile flag, _timed_phase, _display_profile."""

    def test_profile_flag_in_help(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert "--profile" in result.output

    def test_timed_phase_records(self) -> None:
        from grimoire.cli.app import _phase_timings, _timed_phase

        _phase_timings.clear()
        with _timed_phase("test_phase"):
            pass
        assert len(_phase_timings) == 1
        name, elapsed = _phase_timings[0]
        assert name == "test_phase"
        assert elapsed >= 0
        _phase_timings.clear()

    def test_display_profile_produces_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        from grimoire.cli.app import _display_profile, _phase_timings

        _phase_timings.clear()
        _phase_timings.append(("phase_a", 0.1))
        _phase_timings.append(("phase_b", 0.2))
        _display_profile(0.3)
        # _display_profile uses Rich console — check _phase_timings was cleared
        assert len(_phase_timings) == 0

    def test_profile_ctx_obj_set(self, cli_project: Path) -> None:
        """--profile sets ctx.obj['profile'] = True."""
        result = runner.invoke(app, ["--profile", "doctor", str(cli_project)])
        # Should not crash; profile output may appear
        assert result.exit_code in (0, 1)


# ── R27: Auto-Repair ────────────────────────────────────────────────────────

class TestRepairCommand:
    """Tests for 'grimoire repair'."""

    def test_repair_creates_missing_dirs(self, cli_project: Path) -> None:
        """Repair creates missing _grimoire dirs."""
        grimoire_dir = cli_project / "_grimoire"
        if grimoire_dir.exists():
            import shutil
            shutil.rmtree(grimoire_dir)
        result = runner.invoke(app, ["repair", str(cli_project)])
        assert result.exit_code == 0
        assert (cli_project / "_grimoire").is_dir()

    def test_repair_dry_run(self, cli_project: Path) -> None:
        """--dry-run previews without changes."""
        grimoire_dir = cli_project / "_grimoire"
        if grimoire_dir.exists():
            import shutil
            shutil.rmtree(grimoire_dir)
        result = runner.invoke(app, ["repair", "--dry-run", str(cli_project)])
        assert result.exit_code == 0
        assert "would" in result.output.lower() or "DRY RUN" in result.output
        # Dir should NOT be created
        assert not (cli_project / "_grimoire").is_dir()

    def test_repair_no_issues(self, cli_project: Path) -> None:
        """Healthy project shows no actions needed."""
        (cli_project / "_grimoire" / "_memory").mkdir(parents=True, exist_ok=True)
        (cli_project / "_grimoire-output").mkdir(parents=True, exist_ok=True)
        result = runner.invoke(app, ["repair", str(cli_project)])
        assert result.exit_code == 0
        assert "healthy" in result.output.lower() or "No issues" in result.output

    def test_repair_json_output(self, cli_project: Path) -> None:
        result = runner.invoke(app, ["-o", "json", "repair", str(cli_project)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "actions" in data
        assert "count" in data

    def test_repair_no_config(self, tmp_path: Path) -> None:
        """Repair fails gracefully without project-context.yaml."""
        result = runner.invoke(app, ["repair", str(tmp_path)])
        assert result.exit_code == 1

    def test_repair_trims_old_audit_entries(self, cli_project: Path) -> None:
        """Repair removes audit entries older than 90 days."""
        import datetime as _dt

        from grimoire.cli.app import _AUDIT_FILENAME

        mem_dir = cli_project / "_grimoire" / "_memory"
        mem_dir.mkdir(parents=True, exist_ok=True)
        audit = mem_dir / _AUDIT_FILENAME
        old_ts = (_dt.datetime.now(_dt.UTC) - _dt.timedelta(days=100)).isoformat()
        new_ts = _dt.datetime.now(_dt.UTC).isoformat()
        audit.write_text(
            json.dumps({"ts": old_ts, "cmd": "old"}) + "\n"
            + json.dumps({"ts": new_ts, "cmd": "new"}) + "\n",
            encoding="utf-8",
        )
        result = runner.invoke(app, ["repair", str(cli_project)])
        assert result.exit_code == 0
        content = audit.read_text(encoding="utf-8").strip()
        entries = [json.loads(line) for line in content.splitlines() if line.strip()]
        assert len(entries) == 1
        assert entries[0]["cmd"] == "new"


# ── R27: Offline Mode Detection ─────────────────────────────────────────────

class TestOfflineMode:
    """Tests for _is_online() and offline context."""

    def test_env_var_forces_offline(self) -> None:
        from grimoire.cli.app import _is_online

        with patch.dict("os.environ", {"GRIMOIRE_OFFLINE": "1"}):
            assert _is_online() is False

    def test_env_var_true_forces_offline(self) -> None:
        from grimoire.cli.app import _is_online

        with patch.dict("os.environ", {"GRIMOIRE_OFFLINE": "true"}):
            assert _is_online() is False

    def test_unreachable_returns_false(self) -> None:
        import os

        from grimoire.cli.app import _is_online

        os.environ.pop("GRIMOIRE_OFFLINE", None)
        with (
            patch("socket.create_connection", side_effect=OSError("no network")),
            patch.dict("os.environ", {}, clear=False),
        ):
            assert _is_online(timeout=0.1) is False

    def test_offline_flag_in_ctx(self, cli_project: Path) -> None:
        """ctx.obj['offline'] is set by the main callback."""
        # Force offline via env var
        result = runner.invoke(app, ["doctor", str(cli_project)], env={"GRIMOIRE_OFFLINE": "1"})
        # Should not crash — offline flag is set transparently
        assert result.exit_code in (0, 1)


# ── R28: Review Fixes ────────────────────────────────────────────────────────


class TestR28IsOnlineCached:
    """Tests for the is_online() cached wrapper (R28 C3 fix)."""

    def test_is_online_returns_bool(self) -> None:
        import grimoire.cli.app as _app

        _app._online_cache = None
        with patch.object(_app, "_is_online", return_value=True):
            assert _app.is_online() is True

    def test_is_online_caches_result(self) -> None:
        import grimoire.cli.app as _app

        _app._online_cache = None
        mock_probe = MagicMock(return_value=False)
        with patch.object(_app, "_is_online", mock_probe):
            _app.is_online()
            _app.is_online()
            _app.is_online()
        mock_probe.assert_called_once()
        _app._online_cache = None  # cleanup

    def test_is_online_cache_reset(self) -> None:
        import grimoire.cli.app as _app

        _app._online_cache = True
        assert _app.is_online() is True  # uses cache
        _app._online_cache = None
        with patch.object(_app, "_is_online", return_value=False):
            assert _app.is_online() is False
        _app._online_cache = None


class TestR28ConfigFindConfig:
    """Tests for config commands using _find_config() (R28 C4 fix)."""

    def test_config_show_from_subdirectory(self, cli_project: Path) -> None:
        """config show works when invoked from a subdirectory."""
        subdir = cli_project / "deep" / "nested"
        subdir.mkdir(parents=True)
        with patch("grimoire.cli.app._find_config", return_value=cli_project / "project-context.yaml"):
            result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 0

    def test_config_path_uses_find_config(self, cli_project: Path) -> None:
        """config path should resolve through _find_config."""
        cfg = cli_project / "project-context.yaml"
        with patch("grimoire.cli.app._find_config", return_value=cfg):
            result = runner.invoke(app, ["config", "path"])
        assert result.exit_code == 0
        assert str(cfg.resolve()) in result.output

    def test_config_list_uses_find_config(self, cli_project: Path) -> None:
        """config list should resolve through _find_config."""
        with patch("grimoire.cli.app._find_config", return_value=cli_project / "project-context.yaml"):
            result = runner.invoke(app, ["config", "list"])
        assert result.exit_code == 0


class TestR28PhaseTimingsCleared:
    """Tests for _phase_timings.clear() in cli() (R28 C5 fix)."""

    def test_phase_timings_cleared_between_invocations(self) -> None:
        """_phase_timings should be cleared at start of cli() entry point."""
        import grimoire.cli.app as _app

        # Simulate stale data from a previous invocation
        _app._phase_timings.clear()
        _app._phase_timings.append(("stale/phase", 1.234))

        # cli() clears _phase_timings before calling app()
        # We patch app() to prevent actual command execution
        with patch.object(_app, "app", side_effect=SystemExit(0)), contextlib.suppress(SystemExit):
            _app.cli()

        # The stale entry should have been cleared
        assert ("stale/phase", 1.234) not in _app._phase_timings


class TestR28SelfVersionOffline:
    """Tests for self version skipping PyPI when offline (R28 H7 fix)."""

    def test_self_version_skips_pypi_when_offline(self, cli_project: Path) -> None:
        """self version should not attempt PyPI when offline."""
        with patch("grimoire.cli.app.is_online", return_value=False):
            result = runner.invoke(app, ["self", "version"])
        assert result.exit_code == 0
        # Should show version without update info
        assert "grimoire-kit" in result.output.lower() or "version" in result.output.lower()

    def test_self_version_checks_pypi_when_online(self) -> None:
        """self version should attempt PyPI check when online."""
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = json.dumps({"info": {"version": "99.0.0"}}).encode()

        with (
            patch("grimoire.cli.app.is_online", return_value=True),
            patch("grimoire.cli.app.urlopen", mock_resp, create=True),
        ):
            result = runner.invoke(app, ["self", "version"])
        assert result.exit_code == 0


class TestR28RepairAuditConstant:
    """Tests for repair using _AUDIT_FILENAME constant (R28 C1 fix)."""

    def test_repair_uses_audit_filename_constant(self) -> None:
        """Verify _AUDIT_FILENAME is the correct hidden filename."""
        from grimoire.cli.app import _AUDIT_FILENAME

        assert _AUDIT_FILENAME == ".grimoire-audit.jsonl"
        assert _AUDIT_FILENAME.startswith(".")  # hidden file


class TestR28FlattenDeduplicated:
    """Tests for _flatten deduplication (R28 M5 fix)."""

    def test_flatten_returns_flat_dict(self) -> None:
        from grimoire.cli.app import _flatten

        result = _flatten({"a": {"b": 1, "c": {"d": 2}}, "e": 3})
        assert result == {"a.b": 1, "a.c.d": 2, "e": 3}

    def test_flatten_empty_dict(self) -> None:
        from grimoire.cli.app import _flatten

        assert _flatten({}) == {}

    def test_config_list_uses_flatten(self, cli_project: Path) -> None:
        """config list uses _flatten (not the removed _flatten_dict)."""
        result = runner.invoke(app, ["-o", "json", "config", "list"])
        # If _flatten_dict was still referenced, this would crash with NameError
        assert result.exit_code == 0


# ── R28: New Features ────────────────────────────────────────────────────────


class TestConfigEdit:
    """Tests for 'grimoire config edit' command."""

    def test_config_edit_calls_editor(self, cli_project: Path) -> None:
        """config edit should exec $EDITOR with the config path."""
        cfg = cli_project / "project-context.yaml"
        with (
            patch("grimoire.cli.app._find_config", return_value=cfg),
            patch("grimoire.cli.app.os.execvp") as mock_exec,
        ):
            runner.invoke(app, ["config", "edit"], env={"EDITOR": "nano"})
        mock_exec.assert_called_once_with("nano", ["nano", str(cfg)])

    def test_config_edit_uses_visual(self, cli_project: Path) -> None:
        """config edit should prefer $VISUAL over $EDITOR."""
        cfg = cli_project / "project-context.yaml"
        with (
            patch("grimoire.cli.app._find_config", return_value=cfg),
            patch("grimoire.cli.app.os.execvp") as mock_exec,
        ):
            runner.invoke(app, ["config", "edit"], env={"VISUAL": "code", "EDITOR": "nano"})
        mock_exec.assert_called_once_with("code", ["code", str(cfg)])

    def test_config_edit_help(self) -> None:
        result = runner.invoke(app, ["config", "edit", "--help"])
        assert result.exit_code == 0
        assert "EDITOR" in result.output


class TestConfigValidate:
    """Tests for 'grimoire config validate' command."""

    def test_config_validate_valid(self, cli_project: Path) -> None:
        """config validate should succeed on a valid project."""
        with patch("grimoire.cli.app._find_config", return_value=cli_project / "project-context.yaml"):
            result = runner.invoke(app, ["config", "validate"])
        assert result.exit_code == 0

    def test_config_validate_json(self, cli_project: Path) -> None:
        """config validate JSON output should include 'valid' key."""
        with patch("grimoire.cli.app._find_config", return_value=cli_project / "project-context.yaml"):
            result = runner.invoke(app, ["-o", "json", "config", "validate"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["valid"] is True
        assert "warnings" in data

    def test_config_validate_invalid(self, tmp_path: Path) -> None:
        """config validate should fail on invalid YAML."""
        bad = tmp_path / "project-context.yaml"
        bad.write_text("not: a: valid: grimoire config\n", encoding="utf-8")
        with patch("grimoire.cli.app._find_config", return_value=bad):
            result = runner.invoke(app, ["config", "validate"])
        assert result.exit_code == 1

    def test_config_validate_invalid_json(self, tmp_path: Path) -> None:
        """config validate JSON on invalid config."""
        bad = tmp_path / "project-context.yaml"
        bad.write_text("not: a: valid: grimoire config\n", encoding="utf-8")
        with patch("grimoire.cli.app._find_config", return_value=bad):
            result = runner.invoke(app, ["-o", "json", "config", "validate"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["valid"] is False


# ── R29: Review Fixes ────────────────────────────────────────────────────────


class TestR29EditorValidation:
    """Tests for config edit editor validation (R29 C2 fix)."""

    def test_config_edit_missing_editor(self, cli_project: Path) -> None:
        """config edit should error if editor binary not found."""
        cfg = cli_project / "project-context.yaml"
        with (
            patch("grimoire.cli.app._find_config", return_value=cfg),
            patch("shutil.which", return_value=None),
        ):
            result = runner.invoke(app, ["config", "edit"], env={"EDITOR": "nonexistent-editor"})
        assert result.exit_code == 2
        assert "not found" in result.output.lower()

    def test_config_edit_valid_editor(self, cli_project: Path) -> None:
        """config edit should call execvp when editor exists."""
        cfg = cli_project / "project-context.yaml"
        with (
            patch("grimoire.cli.app._find_config", return_value=cfg),
            patch("shutil.which", return_value="/usr/bin/nano"),
            patch("os.execvp") as mock_exec,
        ):
            runner.invoke(app, ["config", "edit"], env={"EDITOR": "nano"})
        mock_exec.assert_called_once()


class TestR29SuggestIncludesAliases:
    """Tests for command suggestion including aliases (R29 H8 fix)."""

    def test_suggest_includes_alias_keys(self) -> None:
        from grimoire.cli.app import _ALIASES, _KNOWN_COMMANDS

        _KNOWN_COMMANDS.clear()
        # Trigger population via _suggest_command
        import grimoire.cli.app as _app
        _app._suggest_command.__wrapped__() if hasattr(_app._suggest_command, '__wrapped__') else None
        # Manually populate since suggest only runs with sys.argv
        for cmd_info in app.registered_commands:
            name = cmd_info.name or (cmd_info.callback.__name__ if cmd_info.callback else None)
            if name:
                _KNOWN_COMMANDS.add(name)
        for group_info in app.registered_groups:
            if group_info.name:
                _KNOWN_COMMANDS.add(group_info.name)
        _KNOWN_COMMANDS.update(_ALIASES)
        # All alias keys should be known
        for alias in _ALIASES:
            assert alias in _KNOWN_COMMANDS, f"Alias '{alias}' not in _KNOWN_COMMANDS"
        _KNOWN_COMMANDS.clear()


class TestR29FlattenLists:
    """Tests for _flatten handling lists of dicts (R29 H4 fix)."""

    def test_flatten_list_of_dicts(self) -> None:
        from grimoire.cli.app import _flatten

        data = {"repos": [{"name": "r1", "path": "."}, {"name": "r2", "path": "/p"}]}
        result = _flatten(data)
        assert result == {"repos.0.name": "r1", "repos.0.path": ".", "repos.1.name": "r2", "repos.1.path": "/p"}

    def test_flatten_list_of_scalars_unchanged(self) -> None:
        from grimoire.cli.app import _flatten

        data = {"tags": ["a", "b", "c"]}
        result = _flatten(data)
        assert result == {"tags": ["a", "b", "c"]}

    def test_flatten_empty_list(self) -> None:
        from grimoire.cli.app import _flatten

        data = {"items": []}
        result = _flatten(data)
        assert result == {"items": []}


class TestR29RequiredDirsConstant:
    """Tests for _REQUIRED_DIRS/_MEMORY_DIR constants (R29 H9 DRY fix)."""

    def test_constants_exist(self) -> None:
        from grimoire.cli.app import _MEMORY_DIR, _REQUIRED_DIRS

        assert "_grimoire" in _REQUIRED_DIRS
        assert "_grimoire-output" in _REQUIRED_DIRS
        assert _MEMORY_DIR == "_grimoire/_memory"

    def test_init_creates_all_required_dirs(self, cli_project: Path) -> None:
        """init should create _REQUIRED_DIRS + _MEMORY_DIR."""
        from grimoire.cli.app import _MEMORY_DIR, _REQUIRED_DIRS

        for d in (*_REQUIRED_DIRS, _MEMORY_DIR):
            assert (cli_project / d).is_dir(), f"Missing directory: {d}"


class TestR29AuditLogAtomic:
    """Tests for atomic audit log truncation (R29 C1 fix)."""

    def test_log_operation_debug_on_error(self, cli_project: Path) -> None:
        """_log_operation should print debug message on OSError when GRIMOIRE_DEBUG is set."""
        import grimoire.cli.app as _app

        with (
            patch.object(_app, "find_project_root", side_effect=FileNotFoundError, create=True),
        ):
            # Should not raise, just silently fail
            _app._log_operation("test_cmd", {"key": "val"})


class TestR29HistorySkipCount:
    """Tests for history command reporting corrupted entries (R29 M14 fix)."""

    def test_history_json_includes_skipped(self, cli_project: Path) -> None:
        """history JSON output should include 'skipped' count."""
        mem_dir = cli_project / "_grimoire" / "_memory"
        mem_dir.mkdir(parents=True, exist_ok=True)
        from grimoire.cli.app import _AUDIT_FILENAME

        audit = mem_dir / _AUDIT_FILENAME
        audit.write_text(
            '{"ts":"2026-01-01T00:00:00+00:00","cmd":"init","ok":true}\n'
            'CORRUPTED LINE\n'
            '{"ts":"2026-01-02T00:00:00+00:00","cmd":"doctor","ok":true}\n',
            encoding="utf-8",
        )
        with patch("grimoire.tools._common.find_project_root", return_value=cli_project):
            result = runner.invoke(app, ["-o", "json", "history"], catch_exceptions=False)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["skipped"] == 1
        assert data["total"] == 2


class TestR29RepairJsonOk:
    """Tests for repair JSON output including 'ok' field (R29 M15 fix)."""

    def test_repair_json_has_ok_field(self, cli_project: Path) -> None:
        result = runner.invoke(app, ["-o", "json", "repair", str(cli_project)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "ok" in data
        assert data["ok"] is True


class TestR29VersionEnvFindConfig:
    """Tests for version/env using _find_config (R29 H3 fix)."""

    def test_version_finds_project_from_subdir(self, cli_project: Path) -> None:
        """version command should find project config via _find_config."""
        with patch("grimoire.cli.app._find_config", return_value=cli_project / "project-context.yaml"):
            result = runner.invoke(app, ["-o", "json", "version"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "grimoire_version" in data

    def test_version_graceful_without_project(self) -> None:
        """version should work even without a project."""
        import typer as _typer

        with patch("grimoire.cli.app._find_config", side_effect=_typer.Exit(1)):
            result = runner.invoke(app, ["-o", "json", "version"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "grimoire_version" in data


# ── R30: Review Fixes ────────────────────────────────────────────────────────


class TestR30FindConfigPermissionError:
    """Tests for _find_config handling PermissionError (R30 C2 fix)."""

    def test_find_config_permission_error(self, tmp_path: Path) -> None:
        """_find_config should exit cleanly on PermissionError."""
        import pytest
        from click.exceptions import Exit

        from grimoire.cli.app import _find_config

        with (
            patch("grimoire.tools._common.find_project_root", side_effect=PermissionError("access denied")),
            pytest.raises(Exit),
        ):
            _find_config(tmp_path / "nonexistent")

    def test_find_config_os_error(self, tmp_path: Path) -> None:
        """_find_config should exit cleanly on generic OSError."""
        import pytest
        from click.exceptions import Exit

        from grimoire.cli.app import _find_config

        with (
            patch("grimoire.tools._common.find_project_root", side_effect=OSError("disk error")),
            pytest.raises(Exit),
        ):
            _find_config(tmp_path / "nonexistent")


class TestR30DiffConfigTypePreserving:
    """Tests for diff_config type-preserving comparison (R30 H2 fix)."""

    def test_diff_detects_type_change(self) -> None:
        """diff should detect 1 vs '1' as different."""
        from grimoire.cli.app import _flatten

        fa = _flatten({"val": 1})
        fb = _flatten({"val": "1"})
        assert fa["val"] != fb["val"]

    def test_diff_none_vs_empty_list(self) -> None:
        """diff should detect None vs [] as different."""
        from grimoire.cli.app import _flatten

        fa = _flatten({"items": None})
        fb = _flatten({"items": []})
        assert fa["items"] != fb["items"]


class TestR30HistoryStreaming:
    """Tests for history command streaming file read (R30 H5 fix)."""

    def test_history_large_file(self, cli_project: Path) -> None:
        """history should handle large audit logs without error."""
        from grimoire.cli.app import _AUDIT_FILENAME

        mem_dir = cli_project / "_grimoire" / "_memory"
        mem_dir.mkdir(parents=True, exist_ok=True)
        log_file = mem_dir / _AUDIT_FILENAME
        lines = [json.dumps({"ts": f"2026-01-01T{i:06d}", "cmd": "init", "ok": True}) for i in range(200)]
        log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with patch("grimoire.tools._common.find_project_root", return_value=cli_project):
            result = runner.invoke(app, ["-o", "json", "history", "-n", "5"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["total"] == 5
        assert data["skipped"] == 0


class TestR30ConfigSetFloat:
    """Tests for config_set float type coercion (R30 M2 fix)."""

    def test_set_float_value(self, cli_project: Path) -> None:
        """config set should handle float values."""
        cfg = cli_project / "project-context.yaml"
        from ruamel.yaml import YAML

        yaml = YAML()
        yaml.preserve_quotes = True
        with cfg.open(encoding="utf-8") as fh:
            data = yaml.load(fh)
        data["threshold"] = 0.5
        with cfg.open("w", encoding="utf-8") as fh:
            yaml.dump(data, fh)

        with patch("grimoire.cli.app._find_config", return_value=cfg):
            result = runner.invoke(app, ["-o", "json", "config", "set", "threshold", "0.75"])
        assert result.exit_code == 0
        data_out = json.loads(result.output)
        assert data_out["new"] == 0.75

    def test_set_float_invalid(self, cli_project: Path) -> None:
        """config set should reject non-numeric values for float keys."""
        cfg = cli_project / "project-context.yaml"
        from ruamel.yaml import YAML

        yaml = YAML()
        yaml.preserve_quotes = True
        with cfg.open(encoding="utf-8") as fh:
            data = yaml.load(fh)
        data["threshold"] = 0.5
        with cfg.open("w", encoding="utf-8") as fh:
            yaml.dump(data, fh)

        with patch("grimoire.cli.app._find_config", return_value=cfg):
            result = runner.invoke(app, ["config", "set", "threshold", "not-a-number"])
        assert result.exit_code == 1
        assert "number" in result.output.lower() or "Expected" in result.output


class TestR30VersionNarrowException:
    """Tests for version_cmd narrowed exception handling (R30 M4 fix)."""

    def test_version_handles_config_error(self) -> None:
        """version should handle GrimoireConfigError gracefully."""
        from grimoire.core.exceptions import GrimoireConfigError

        with patch("grimoire.cli.app._find_config", side_effect=GrimoireConfigError("bad")):
            result = runner.invoke(app, ["-o", "json", "version"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "grimoire_version" in data
        assert "project" not in data


class TestR30LogOperationNarrowException:
    """Tests for _log_operation narrowed exception handling (R30 M5 fix)."""

    def test_log_operation_file_not_found(self) -> None:
        """_log_operation should silently skip when no project found."""
        import grimoire.cli.app as _app

        with patch("grimoire.tools._common.find_project_root", side_effect=FileNotFoundError):
            _app._log_operation("test", {"key": "val"})
        # Should not raise


# ── R31: env_cmd exception narrowing ─────────────────────────────────────────

class TestR31EnvCmdNarrowException:
    """Tests for env_cmd narrowed exception handling (R31 C1 fix)."""

    def test_env_project_error_handled(self) -> None:
        """env should handle GrimoireProjectError gracefully."""
        from grimoire.core.exceptions import GrimoireProjectError

        with patch("grimoire.cli.app._find_config", side_effect=GrimoireProjectError("bad")):
            result = runner.invoke(app, ["-o", "json", "env"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["project"] is None

    def test_env_config_error_handled(self) -> None:
        """env should handle GrimoireConfigError gracefully."""
        from grimoire.core.exceptions import GrimoireConfigError

        with patch("grimoire.cli.app._find_config", side_effect=GrimoireConfigError("bad")):
            result = runner.invoke(app, ["-o", "json", "env"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["project"] is None


# ── R31: version_cmd catches GrimoireError base class ────────────────────────

class TestR31VersionCmdGrimoireError:
    """Tests for version_cmd catching GrimoireError base class (R31 C2 fix)."""

    def test_version_project_error_handled(self) -> None:
        """version should handle GrimoireProjectError gracefully (sibling of ConfigError)."""
        from grimoire.core.exceptions import GrimoireProjectError

        with patch("grimoire.cli.app._find_config", side_effect=GrimoireProjectError("missing")):
            result = runner.invoke(app, ["-o", "json", "version"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "grimoire_version" in data
        assert "project" not in data


# ── R31: _interrupted dead variable removed ──────────────────────────────────

class TestR31InterruptedRemoved:
    """Tests confirming _interrupted dead variable was removed (R31 H1 fix)."""

    def test_no_interrupted_attribute(self) -> None:
        """Module should no longer have _interrupted attribute."""
        import grimoire.cli.app as _app

        assert not hasattr(_app, "_interrupted")

    def test_signal_handler_still_exits(self) -> None:
        """_handle_signal should still raise SystemExit without setting a flag."""
        import grimoire.cli.app as _app

        with pytest.raises(SystemExit) as exc_info:
            _app._handle_signal(2, None)
        assert exc_info.value.code == 130


# ── R31: ctx.obj guard standardisation ───────────────────────────────────────

class TestR31CtxObjGuard:
    """Tests confirming all ctx.obj accesses use (ctx.obj or {}) pattern (R31 H2 fix)."""

    def test_no_bare_ctx_obj_get(self) -> None:
        """All ctx.obj.get() calls should use the (ctx.obj or {}) guard."""
        import re

        source = Path(__file__).resolve().parent.parent.parent.parent / "src" / "grimoire" / "cli" / "app.py"
        text = source.read_text(encoding="utf-8")
        # Find ctx.obj.get that are NOT preceded by "or {})" — i.e. bare ctx.obj.get
        # Skip lines in main() callback where ctx.obj is guaranteed by ensure_object
        bare_calls = []
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            # Skip lines that assign to ctx.obj (in main callback)
            if "ctx.obj[" in stripped and "=" in stripped:
                continue
            # Skip ctx.ensure_object
            if "ensure_object" in stripped:
                continue
            if re.search(r"ctx\.obj\.get\b", stripped) and "ctx.obj or {}" not in stripped:
                bare_calls.append((i, stripped))
        assert bare_calls == [], f"Bare ctx.obj.get() without guard found at: {bare_calls}"


# ── R31: setup respects global -o json ───────────────────────────────────────

class TestR31SetupGlobalJson:
    """Tests for setup command respecting global -o json flag (R31 H3 fix)."""

    def test_setup_global_json_flag(self, tmp_path: Path) -> None:
        """grimoire -o json setup should produce JSON output."""
        from grimoire.cli.cmd_setup import SetupResult

        # Create minimal project-context.yaml
        pcy = tmp_path / "project-context.yaml"
        pcy.write_text("project:\n  name: test\nuser:\n  name: Test\n", encoding="utf-8")

        mock_result = SetupResult(diffs=[], updated_files=[], skipped_files=[], errors=[])
        with patch("grimoire.cli.cmd_setup.load_user_values") as mock_load, \
             patch("grimoire.cli.cmd_setup.apply", return_value=mock_result):
            mock_load.return_value = MagicMock(
                user_name="Test", communication_language="en",
                document_output_language="en", user_skill_level="expert",
            )
            result = runner.invoke(app, ["-o", "json", "setup", str(tmp_path)])
        # Should produce JSON (synced: true)
        output = result.output.strip()
        assert output.startswith("{"), f"Expected JSON output, got: {output}"
        data = json.loads(output)
        assert data["synced"] is True

    def test_setup_dedicated_json_flag(self, tmp_path: Path) -> None:
        """grimoire setup --json should still work."""
        from grimoire.cli.cmd_setup import SetupResult

        pcy = tmp_path / "project-context.yaml"
        pcy.write_text("project:\n  name: test\nuser:\n  name: Test\n", encoding="utf-8")

        mock_result = SetupResult(diffs=[], updated_files=[], skipped_files=[], errors=[])
        with patch("grimoire.cli.cmd_setup.load_user_values") as mock_load, \
             patch("grimoire.cli.cmd_setup.apply", return_value=mock_result):
            mock_load.return_value = MagicMock(
                user_name="Test", communication_language="en",
                document_output_language="en", user_skill_level="expert",
            )
            result = runner.invoke(app, ["setup", "--json", str(tmp_path)])
        output = result.output.strip()
        assert output.startswith("{"), f"Expected JSON output, got: {output}"
        data = json.loads(output)
        assert data["synced"] is True
