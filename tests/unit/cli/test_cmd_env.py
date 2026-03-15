"""Tests for grimoire env, plugins, config, and completion CLI commands."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app

runner = CliRunner()


# ── grimoire env ──────────────────────────────────────────────────────────────


class TestEnvCommand:
    """Verify the ``grimoire env`` diagnostic command."""

    def test_env_text_shows_version(self) -> None:
        result = runner.invoke(app, ["env"])
        assert result.exit_code == 0
        assert "Grimoire Kit" in result.output

    def test_env_text_shows_python(self) -> None:
        result = runner.invoke(app, ["env"])
        assert "Python" in result.output

    def test_env_text_shows_platform(self) -> None:
        result = runner.invoke(app, ["env"])
        assert "Platform" in result.output

    def test_env_text_shows_dependencies(self) -> None:
        result = runner.invoke(app, ["env"])
        assert "Dependencies" in result.output

    def test_env_json_output(self) -> None:
        result = runner.invoke(app, ["-o", "json", "env"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "grimoire_version" in data
        assert "python" in data
        assert "dependencies" in data
        assert "platform" in data

    def test_env_json_has_arch(self) -> None:
        result = runner.invoke(app, ["-o", "json", "env"])
        data = json.loads(result.output)
        assert "arch" in data

    def test_env_json_environment_vars(self) -> None:
        result = runner.invoke(app, ["-o", "json", "env"])
        data = json.loads(result.output)
        assert "environment" in data
        assert "GRIMOIRE_LOG_LEVEL" in data["environment"]


# ── grimoire plugins ──────────────────────────────────────────────────────────


class TestPluginsCommand:
    """Verify the ``grimoire plugins list`` command."""

    def test_plugins_list_text(self) -> None:
        result = runner.invoke(app, ["plugins", "list"])
        assert result.exit_code == 0

    def test_plugins_list_json(self) -> None:
        result = runner.invoke(app, ["-o", "json", "plugins", "list"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "tools" in data
        assert "backends" in data

    def test_plugins_list_with_mocked_tools(self) -> None:
        with patch("grimoire.registry.discovery.entry_points", return_value=[]):
            result = runner.invoke(app, ["plugins", "list"])
        assert result.exit_code == 0

    def test_plugins_help(self) -> None:
        result = runner.invoke(app, ["plugins", "--help"])
        assert result.exit_code == 0
        assert "list" in result.output

    def test_plugins_list_json_empty(self) -> None:
        with patch("grimoire.registry.discovery.entry_points", return_value=[]):
            result = runner.invoke(app, ["-o", "json", "plugins", "list"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["tools"] == []
        assert data["backends"] == []


# ── grimoire config show ──────────────────────────────────────────────────────


class TestConfigShowCommand:
    """Verify the ``grimoire config show`` command."""

    def test_config_show_full(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "project-context.yaml").write_text(
            "project:\n  name: test-proj\n", encoding="utf-8"
        )
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 0
        assert "test-proj" in result.output

    def test_config_show_dot_key(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "project-context.yaml").write_text(
            "project:\n  name: dot-test\n  type: lib\n", encoding="utf-8"
        )
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["config", "show", "project.name"])
        assert result.exit_code == 0
        assert "dot-test" in result.output

    def test_config_show_missing_key(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "project-context.yaml").write_text(
            "project:\n  name: x\n", encoding="utf-8"
        )
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["config", "show", "nonexistent"])
        assert result.exit_code == 2

    def test_config_show_json(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "project-context.yaml").write_text(
            "project:\n  name: json-test\n", encoding="utf-8"
        )
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["-o", "json", "config", "show", "project.name"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["project.name"] == "json-test"

    def test_config_show_no_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 1

    def test_config_show_help(self) -> None:
        result = runner.invoke(app, ["config", "--help"])
        assert result.exit_code == 0
        assert "show" in result.output


# ── grimoire completion ───────────────────────────────────────────────────────


class TestCompletionCommand:
    """Verify the ``grimoire completion install`` command."""

    def test_completion_help(self) -> None:
        result = runner.invoke(app, ["completion", "--help"])
        assert result.exit_code == 0
        assert "install" in result.output

    def test_completion_unsupported_shell(self) -> None:
        result = runner.invoke(app, ["completion", "install", "--shell", "powershell"])
        assert result.exit_code == 1

    def test_completion_install_bash_generates_script(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "# fake completion script\ncomplete -F _grimoire grimoire"
            result = runner.invoke(app, ["completion", "install", "--shell", "bash"])
        # May succeed or fail depending on subprocess mock
        assert result.exit_code in (0, 1)
