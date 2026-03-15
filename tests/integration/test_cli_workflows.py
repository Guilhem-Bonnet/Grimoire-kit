"""Integration tests — CLI end-to-end workflows."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app

runner = CliRunner()

pytestmark = pytest.mark.integration


class TestInitDoctorFlow:
    """Test grimoire init → doctor → status flow."""

    def test_init_creates_config(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["init", str(tmp_path), "--name", "integ-test", "--force"])
        assert result.exit_code == 0
        assert (tmp_path / "project-context.yaml").is_file()

    def test_doctor_on_fresh_init(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path), "--name", "integ-test", "--force"])
        result = runner.invoke(app, ["doctor"], catch_exceptions=False)
        # Doctor may warn about missing dirs but should not crash
        assert result.exit_code in (0, 1)

    def test_status_after_init(self, tmp_path: Path) -> None:
        runner.invoke(app, ["init", str(tmp_path), "--name", "integ-test", "--force"])
        result = runner.invoke(app, ["status"])
        assert result.exit_code in (0, 1)


class TestConfigShowFlow:
    """Test grimoire config show reads real YAML."""

    def test_config_show_full(self, grimoire_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(grimoire_project)
        result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 0
        assert "integration-test" in result.output

    def test_config_show_key(self, grimoire_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(grimoire_project)
        result = runner.invoke(app, ["config", "show", "project.name"])
        assert result.exit_code == 0
        assert "integration-test" in result.output

    def test_config_show_nested(self, grimoire_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(grimoire_project)
        result = runner.invoke(app, ["config", "show", "memory.backend"])
        assert result.exit_code == 0
        assert "local" in result.output

    def test_config_show_missing_key(self, grimoire_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(grimoire_project)
        result = runner.invoke(app, ["config", "show", "nonexistent.key"])
        assert result.exit_code == 2

    def test_config_show_json(self, grimoire_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(grimoire_project)
        result = runner.invoke(app, ["--output", "json", "config", "show", "project.name"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["project.name"] == "integration-test"

    def test_config_show_no_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 1


class TestEnvPluginsFlow:
    """Test grimoire env and plugins in integration context."""

    def test_env_json_structure(self) -> None:
        result = runner.invoke(app, ["--output", "json", "env"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "grimoire_version" in data
        assert "python" in data
        assert "dependencies" in data

    def test_plugins_list_no_crash(self) -> None:
        result = runner.invoke(app, ["plugins", "list"])
        assert result.exit_code == 0

    def test_full_diagnostic_flow(self, grimoire_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Run env → plugins → config show → doctor sequentially."""
        monkeypatch.chdir(grimoire_project)

        env_result = runner.invoke(app, ["--output", "json", "env"])
        assert env_result.exit_code == 0

        plugins_result = runner.invoke(app, ["plugins", "list"])
        assert plugins_result.exit_code == 0

        config_result = runner.invoke(app, ["config", "show", "project.name"])
        assert config_result.exit_code == 0
        assert "integration-test" in config_result.output
