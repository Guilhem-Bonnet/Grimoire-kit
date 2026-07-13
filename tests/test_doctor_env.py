"""Tests for the doctor environment checks and ``doctor --fix`` artifact repair.

Covers cli/cmd_up.py check functions (mocked socket/subprocess/shutil.which)
and their integration into ``grimoire doctor`` in cli/app.py.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from grimoire.cli import cmd_up
from grimoire.cli.cmd_up import (
    EnvCheck,
    check_docker,
    check_mcp_json,
    check_ollama,
    check_qdrant,
    check_uv,
    check_venv,
    repair_project_artifacts,
    run_env_checks,
)

# ── Unit: probe helpers ───────────────────────────────────────────────────────


class TestTcpReachable:
    def test_reachable(self) -> None:
        with patch("grimoire.cli.cmd_up.socket.create_connection") as mock_conn:
            mock_conn.return_value.__enter__ = MagicMock()
            mock_conn.return_value.__exit__ = MagicMock(return_value=False)
            assert cmd_up._tcp_reachable("http://localhost:6333", 6333) is True
        args, kwargs = mock_conn.call_args
        assert args[0] == ("localhost", 6333)
        assert kwargs["timeout"] == 0.5

    def test_unreachable(self) -> None:
        with patch("grimoire.cli.cmd_up.socket.create_connection", side_effect=OSError("refused")):
            assert cmd_up._tcp_reachable("http://localhost:6333", 6333) is False

    def test_timeout_is_an_oserror(self) -> None:
        with patch("grimoire.cli.cmd_up.socket.create_connection", side_effect=TimeoutError("slow")):
            assert cmd_up._tcp_reachable("http://localhost:6333", 6333) is False

    def test_default_port_used_when_missing(self) -> None:
        with patch("grimoire.cli.cmd_up.socket.create_connection") as mock_conn:
            cmd_up._tcp_reachable("http://memory-host", 6333)
        assert mock_conn.call_args[0][0] == ("memory-host", 6333)


class TestCheckUv:
    def test_found(self) -> None:
        with patch("grimoire.cli.cmd_up.shutil.which", return_value="/usr/bin/uv"):
            chk = check_uv()
        assert chk.passed is True
        assert chk.level == "ok"

    def test_missing_is_optional_warning_with_remedy(self) -> None:
        with patch("grimoire.cli.cmd_up.shutil.which", return_value=None):
            chk = check_uv()
        assert chk.passed is True  # optional — never fails doctor
        assert chk.level == "warn"
        assert chk.remedy


class TestCheckDocker:
    def test_cli_missing(self) -> None:
        with patch("grimoire.cli.cmd_up.shutil.which", return_value=None):
            chk = check_docker()
        assert chk.passed is True
        assert chk.level == "warn"
        assert "docker not found" in chk.detail
        assert chk.remedy

    def test_daemon_reachable(self) -> None:
        proc = MagicMock(returncode=0, stdout="29.0.0\n")
        with (
            patch("grimoire.cli.cmd_up.shutil.which", return_value="/usr/bin/docker"),
            patch("grimoire.cli.cmd_up.subprocess.run", return_value=proc) as mock_run,
        ):
            chk = check_docker()
        assert chk.level == "ok"
        assert "29.0.0" in chk.detail
        assert mock_run.call_args.kwargs["timeout"] == 2.0

    def test_daemon_down(self) -> None:
        proc = MagicMock(returncode=1, stdout="")
        with (
            patch("grimoire.cli.cmd_up.shutil.which", return_value="/usr/bin/docker"),
            patch("grimoire.cli.cmd_up.subprocess.run", return_value=proc),
        ):
            chk = check_docker()
        assert chk.passed is True
        assert chk.level == "warn"
        assert "daemon" in chk.detail
        assert chk.remedy

    def test_docker_info_timeout_never_crashes(self) -> None:
        with (
            patch("grimoire.cli.cmd_up.shutil.which", return_value="/usr/bin/docker"),
            patch(
                "grimoire.cli.cmd_up.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=2.0),
            ),
        ):
            chk = check_docker()
        assert chk.passed is True
        assert chk.level == "warn"


class TestCheckQdrant:
    def test_reachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GRIMOIRE_QDRANT_URL", raising=False)
        with patch("grimoire.cli.cmd_up._tcp_reachable", return_value=True):
            chk = check_qdrant()
        assert chk.level == "ok"
        assert "localhost:6333" in chk.detail

    def test_env_url_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GRIMOIRE_QDRANT_URL", "http://memory-host:7777")
        with patch("grimoire.cli.cmd_up._tcp_reachable", return_value=False) as mock_probe:
            chk = check_qdrant()
        assert mock_probe.call_args[0][0] == "http://memory-host:7777"
        assert "memory-host:7777" in chk.detail

    def test_unreachable_remedy_is_docker_run(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv("GRIMOIRE_QDRANT_URL", raising=False)
        with patch("grimoire.cli.cmd_up._tcp_reachable", return_value=False):
            chk = check_qdrant(tmp_path)
        assert chk.passed is True
        assert chk.level == "warn"
        assert chk.remedy.startswith("docker run")

    def test_unreachable_remedy_uses_compose_file_when_present(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("GRIMOIRE_QDRANT_URL", raising=False)
        (tmp_path / "docker-compose.memory.yml").write_text("services: {}\n", encoding="utf-8")
        with patch("grimoire.cli.cmd_up._tcp_reachable", return_value=False):
            chk = check_qdrant(tmp_path)
        assert chk.remedy == "docker compose -f docker-compose.memory.yml up -d"


class TestCheckOllama:
    def test_reachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GRIMOIRE_OLLAMA_URL", raising=False)
        with patch("grimoire.cli.cmd_up._tcp_reachable", return_value=True):
            chk = check_ollama()
        assert chk.level == "ok"

    def test_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GRIMOIRE_OLLAMA_URL", "http://gpu-box:11434")
        with patch("grimoire.cli.cmd_up._tcp_reachable", return_value=False):
            chk = check_ollama()
        assert chk.passed is True
        assert chk.level == "warn"
        assert "gpu-box" in chk.detail


class TestCheckVenv:
    def test_inside_venv(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cmd_up.sys, "prefix", "/proj/.venv")
        monkeypatch.setattr(cmd_up.sys, "base_prefix", "/usr")
        assert check_venv().level == "ok"

    def test_outside_venv_is_info_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cmd_up.sys, "prefix", "/usr")
        monkeypatch.setattr(cmd_up.sys, "base_prefix", "/usr")
        chk = check_venv()
        assert chk.passed is True
        assert chk.level == "info"


class TestCheckMcpJson:
    def test_absent_file_yields_no_checks(self, tmp_path: Path) -> None:
        assert check_mcp_json(tmp_path) == []

    def test_valid_command_on_path(self, tmp_path: Path) -> None:
        (tmp_path / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"grimoire": {"command": "grimoire-mcp", "args": []}}}),
            encoding="utf-8",
        )
        with patch("grimoire.cli.cmd_up.shutil.which", return_value="/usr/bin/grimoire-mcp"):
            checks = check_mcp_json(tmp_path)
        assert len(checks) == 1
        assert checks[0].passed is True

    def test_broken_command_fails_with_remedy(self, tmp_path: Path) -> None:
        (tmp_path / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"grimoire": {"command": "no-such-binary", "args": []}}}),
            encoding="utf-8",
        )
        with patch("grimoire.cli.cmd_up.shutil.which", return_value=None):
            checks = check_mcp_json(tmp_path)
        assert len(checks) == 1
        assert checks[0].passed is False
        assert checks[0].optional is False
        assert "no-such-binary" in checks[0].detail
        assert checks[0].remedy

    def test_broken_path_argument_fails(self, tmp_path: Path) -> None:
        (tmp_path / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"local": {
                "command": "python", "args": ["/nonexistent/server.py"],
            }}}),
            encoding="utf-8",
        )
        with patch("grimoire.cli.cmd_up.shutil.which", return_value="/usr/bin/python"):
            checks = check_mcp_json(tmp_path)
        assert checks[0].passed is False
        assert "/nonexistent/server.py" in checks[0].detail

    def test_relative_path_argument_resolved_against_project(self, tmp_path: Path) -> None:
        (tmp_path / "server.py").write_text("print('hi')\n", encoding="utf-8")
        (tmp_path / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"local": {"command": "python", "args": ["./server.py"]}}}),
            encoding="utf-8",
        )
        with patch("grimoire.cli.cmd_up.shutil.which", return_value="/usr/bin/python"):
            checks = check_mcp_json(tmp_path)
        assert checks[0].passed is True

    def test_invalid_json_fails(self, tmp_path: Path) -> None:
        (tmp_path / ".mcp.json").write_text("{not json", encoding="utf-8")
        checks = check_mcp_json(tmp_path)
        assert len(checks) == 1
        assert checks[0].passed is False
        assert "not valid JSON" in checks[0].detail

    def test_flag_arguments_are_not_treated_as_paths(self, tmp_path: Path) -> None:
        (tmp_path / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"srv": {"command": "python", "args": ["--port", "8080"]}}}),
            encoding="utf-8",
        )
        with patch("grimoire.cli.cmd_up.shutil.which", return_value="/usr/bin/python"):
            checks = check_mcp_json(tmp_path)
        assert checks[0].passed is True


class TestRunEnvChecks:
    def test_never_raises_and_returns_all_probes(self, tmp_path: Path) -> None:
        with (
            patch("grimoire.cli.cmd_up.shutil.which", return_value=None),
            patch("grimoire.cli.cmd_up.socket.create_connection", side_effect=OSError("down")),
        ):
            checks = run_env_checks(tmp_path)
        names = {c.name for c in checks}
        assert {"env_venv", "env_uv", "env_docker", "env_qdrant", "env_ollama"} <= names
        assert all(isinstance(c, EnvCheck) for c in checks)
        # No service available → warnings only, never hard failures
        assert all(c.passed for c in checks)


# ── Integration: grimoire doctor ──────────────────────────────────────────────


@pytest.fixture
def runner():
    from typer.testing import CliRunner

    return CliRunner()


@pytest.fixture
def cli_app():
    from grimoire.cli.app import app

    return app


class TestDoctorEnvIntegration:
    def test_doctor_passes_with_everything_down(self, runner, cli_app, init_project: Path) -> None:
        """Optional env probes must never fail doctor when tools are absent."""
        with (
            patch("grimoire.cli.cmd_up.shutil.which", return_value=None),
            patch("grimoire.cli.cmd_up.socket.create_connection", side_effect=OSError("down")),
        ):
            result = runner.invoke(cli_app, ["doctor", str(init_project)])
        assert result.exit_code == 0
        assert "uv not found" in result.output
        assert "docker not found" in result.output
        assert "Qdrant not reachable" in result.output
        assert "Ollama not reachable" in result.output
        assert "remedy:" in result.output

    def test_doctor_json_includes_env_checks(self, runner, cli_app, init_project: Path) -> None:
        with (
            patch("grimoire.cli.cmd_up.shutil.which", return_value=None),
            patch("grimoire.cli.cmd_up.socket.create_connection", side_effect=OSError("down")),
        ):
            result = runner.invoke(cli_app, ["-o", "json", "doctor", str(init_project)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        names = {c["name"] for c in data["checks"]}
        assert {"env_uv", "env_docker", "env_qdrant", "env_ollama", "env_venv"} <= names
        qdrant = next(c for c in data["checks"] if c["name"] == "env_qdrant")
        assert qdrant["passed"] is True
        assert qdrant["remedy"]

    def test_doctor_fails_on_broken_mcp_json(self, runner, cli_app, init_project: Path) -> None:
        (init_project / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"broken": {"command": "no-such-binary-xyz", "args": []}}}),
            encoding="utf-8",
        )
        with (
            patch("grimoire.cli.cmd_up.shutil.which", return_value=None),
            patch("grimoire.cli.cmd_up.socket.create_connection", side_effect=OSError("down")),
        ):
            result = runner.invoke(cli_app, ["doctor", str(init_project)])
        assert result.exit_code == 1
        assert "no-such-binary-xyz" in result.output


class TestDoctorFix:
    @pytest.fixture
    def project_with_agent(self, init_project: Path) -> Path:
        agents = init_project / "_grimoire" / "_config" / "custom" / "agents"
        agents.mkdir(parents=True)
        (agents / "helper.md").write_text(
            "---\ndescription: Test helper agent\n---\n# helper\n", encoding="utf-8",
        )
        return init_project

    def _env_ok(self):
        """Patch env probes so doctor's environment section is deterministic."""
        proc = MagicMock(returncode=0, stdout="1.0\n")
        return (
            patch("grimoire.cli.cmd_up.shutil.which", return_value="/usr/bin/tool"),
            patch("grimoire.cli.cmd_up.subprocess.run", return_value=proc),
            patch("grimoire.cli.cmd_up.socket.create_connection", side_effect=OSError("down")),
        )

    def test_fix_regenerates_wrappers_and_mcp_json(self, runner, cli_app, project_with_agent: Path) -> None:
        p_which, p_run, p_sock = self._env_ok()
        with p_which, p_run, p_sock:
            result = runner.invoke(cli_app, ["doctor", str(project_with_agent), "--fix"])
        assert result.exit_code == 0
        wrapper = project_with_agent / ".github" / "agents" / "helper.agent.md"
        assert wrapper.is_file()
        assert "helper" in wrapper.read_text(encoding="utf-8")
        mcp = project_with_agent / ".mcp.json"
        assert mcp.is_file()
        data = json.loads(mcp.read_text(encoding="utf-8"))
        assert "grimoire" in data["mcpServers"]
        assert "regenerated (--fix)" in result.output

    def test_without_fix_missing_wrappers_fail_with_fix_hint(
        self, runner, cli_app, project_with_agent: Path
    ) -> None:
        p_which, p_run, p_sock = self._env_ok()
        with p_which, p_run, p_sock:
            result = runner.invoke(cli_app, ["doctor", str(project_with_agent)])
        assert result.exit_code == 1
        assert "grimoire doctor . --fix" in result.output

    def test_repair_is_idempotent(self, project_with_agent: Path) -> None:
        first = repair_project_artifacts(project_with_agent)
        assert ".github/agents/helper.agent.md" in first
        assert ".mcp.json" in first
        second = repair_project_artifacts(project_with_agent)
        assert second == []

    def test_repair_skips_template_agents(self, init_project: Path) -> None:
        agents = init_project / "_grimoire" / "_config" / "custom" / "agents"
        agents.mkdir(parents=True)
        (agents / "custom-agent.tpl.md").write_text("---\ndescription: tpl\n---\n", encoding="utf-8")
        written = repair_project_artifacts(init_project)
        assert written == [".mcp.json"]
