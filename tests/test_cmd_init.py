"""Tests for cli/cmd_init.py — enhanced init command."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from grimoire.cli.cmd_init import _git_user_name, detect_memory_backend


class TestDetectMemoryBackend:
    def test_returns_local_when_nothing_available(self) -> None:
        with patch("grimoire.cli.cmd_init.urllib.request.urlopen", side_effect=OSError("no server")):
            result = detect_memory_backend()
        assert result == "local"

    def test_returns_qdrant_local_when_qdrant_up(self) -> None:
        class FakeResp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self): return b""

        with patch("grimoire.cli.cmd_init.urllib.request.urlopen", return_value=FakeResp()):
            result = detect_memory_backend()
        assert result == "qdrant-local"

    def test_returns_local_on_timeout(self) -> None:
        with patch("grimoire.cli.cmd_init.urllib.request.urlopen", side_effect=TimeoutError("timeout")):
            result = detect_memory_backend()
        assert result == "local"


class TestGitUserName:
    def test_returns_name_on_success(self) -> None:
        with patch("grimoire.cli.cmd_init.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "Test User\n"
            result = _git_user_name()
        assert result == "Test User"

    def test_returns_empty_on_failure(self) -> None:
        with patch("grimoire.cli.cmd_init.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = ""
            result = _git_user_name()
        assert result == ""

    def test_returns_empty_on_exception(self) -> None:
        with patch("grimoire.cli.cmd_init.subprocess.run", side_effect=FileNotFoundError("no git")):
            result = _git_user_name()
        assert result == ""


class TestInitCLI:
    """CLI integration tests using typer CliRunner."""

    @pytest.fixture
    def runner(self):
        from typer.testing import CliRunner
        return CliRunner()

    @pytest.fixture
    def app(self):
        from grimoire.cli.app import app
        return app

    def test_init_dry_run(self, runner, app, tmp_path: Path) -> None:
        result = runner.invoke(app, ["-y", "init", str(tmp_path), "--dry-run"])
        assert result.exit_code == 0
        assert "dry-run" in result.output.lower() or "mkdir" in result.output.lower()

    def test_init_express_creates_project(self, runner, app, tmp_path: Path) -> None:
        target = tmp_path / "my-project"
        result = runner.invoke(app, ["-y", "init", str(target)])
        assert result.exit_code == 0
        assert (target / "project-context.yaml").is_file()
        assert (target / "_grimoire" / "_config" / "custom" / "agents").is_dir()

    def test_init_express_sets_project_name(self, runner, app, tmp_path: Path) -> None:
        target = tmp_path / "cool-app"
        runner.invoke(app, ["-y", "init", str(target)])
        content = (target / "project-context.yaml").read_text()
        assert "cool-app" in content

    def test_init_with_explicit_name(self, runner, app, tmp_path: Path) -> None:
        target = tmp_path / "dir"
        runner.invoke(app, ["-y", "init", str(target), "--name", "MyApp"])
        content = (target / "project-context.yaml").read_text()
        assert "MyApp" in content

    def test_init_with_explicit_archetype(self, runner, app, tmp_path: Path) -> None:
        target = tmp_path / "infra"
        runner.invoke(app, ["-y", "init", str(target), "--archetype", "infra-ops"])
        content = (target / "project-context.yaml").read_text()
        assert "infra-ops" in content
        # Should have infra-ops agents
        agents_dir = target / "_grimoire" / "_config" / "custom" / "agents"
        agent_names = {f.stem for f in agents_dir.glob("*.md")}
        assert "ops-engineer" in agent_names

    def test_init_refuses_existing_without_force(self, runner, app, tmp_path: Path) -> None:
        target = tmp_path / "existing"
        target.mkdir()
        (target / "project-context.yaml").write_text("existing")
        result = runner.invoke(app, ["-y", "init", str(target)])
        assert result.exit_code == 1

    def test_init_force_overwrites(self, runner, app, tmp_path: Path) -> None:
        target = tmp_path / "overwrite"
        target.mkdir()
        (target / "project-context.yaml").write_text("old")
        result = runner.invoke(app, ["-y", "init", str(target), "--force"])
        assert result.exit_code == 0
        content = (target / "project-context.yaml").read_text()
        assert "old" not in content

    def test_init_json_output(self, runner, app, tmp_path: Path) -> None:
        target = tmp_path / "json-proj"
        result = runner.invoke(app, ["-y", "-o", "json", "init", str(target)])
        assert result.exit_code == 0
        import json
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["project"] == "json-proj"
        assert "agents" in data

    def test_init_invalid_archetype(self, runner, app, tmp_path: Path) -> None:
        target = tmp_path / "bad-arch"
        result = runner.invoke(app, ["-y", "init", str(target), "--archetype", "nonexistent"])
        assert result.exit_code == 1

    def test_init_invalid_backend(self, runner, app, tmp_path: Path) -> None:
        target = tmp_path / "bad-backend"
        result = runner.invoke(app, ["-y", "init", str(target), "--backend", "redis"])
        assert result.exit_code == 1

    def test_init_deploys_meta_agents(self, runner, app, tmp_path: Path) -> None:
        target = tmp_path / "meta-test"
        runner.invoke(app, ["-y", "init", str(target)])
        agents_dir = target / "_grimoire" / "_config" / "custom" / "agents"
        agent_names = {f.stem for f in agents_dir.glob("*.md")}
        assert "project-navigator" in agent_names
        assert "memory-keeper" in agent_names
        assert "agent-optimizer" in agent_names

    def test_init_creates_session_branch(self, runner, app, tmp_path: Path) -> None:
        target = tmp_path / "session-test"
        runner.invoke(app, ["-y", "init", str(target)])
        branch_file = target / "_grimoire-output" / ".runs" / "main" / "branch.json"
        assert branch_file.is_file()
