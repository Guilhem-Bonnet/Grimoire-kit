"""Tests for grimoire.cli.app — CLI commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app

runner = CliRunner()


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
        result = runner.invoke(app, ["remove", "to-remove", str(project)])
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

        runner.invoke(app, ["remove", "temp", str(project)])
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
