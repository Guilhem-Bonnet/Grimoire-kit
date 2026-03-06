"""Tests for grimoire-daemon.py — Background maintenance daemon."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "framework" / "tools"
sys.path.insert(0, str(TOOLS))

# Module uses dashes in filename — import via spec
_spec = importlib.util.spec_from_file_location("grimoire_daemon", TOOLS / "grimoire-daemon.py")
daemon = importlib.util.module_from_spec(_spec)
sys.modules["grimoire_daemon"] = daemon
_spec.loader.exec_module(daemon)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_project(tmp_path):
    """Crée un projet temp avec structure minimale."""
    (tmp_path / "_bmad" / "_memory" / "daemon").mkdir(parents=True)
    (tmp_path / "framework" / "tools").mkdir(parents=True)
    return tmp_path


# ── State Management ─────────────────────────────────────────────────────────


class TestState:
    def test_read_empty_state(self, tmp_project):
        state = daemon._read_state(tmp_project)
        assert state.status == "stopped"
        assert state.total_cycles == 0

    def test_write_and_read_state(self, tmp_project):
        state = daemon.DaemonState(pid=1234, status="running", total_cycles=5)
        daemon._write_state(tmp_project, state)
        loaded = daemon._read_state(tmp_project)
        assert loaded.pid == 1234
        assert loaded.status == "running"
        assert loaded.total_cycles == 5

    def test_pid_write_read_clear(self, tmp_project):
        daemon._write_pid(tmp_project)
        pid = daemon._read_pid(tmp_project)
        assert pid == os.getpid()

        daemon._clear_pid(tmp_project)
        assert daemon._read_pid(tmp_project) is None

    def test_is_running_false_when_no_pid(self, tmp_project):
        assert daemon._is_running(tmp_project) is False


# ── Task Runner ──────────────────────────────────────────────────────────────


class TestTaskRunner:
    def test_run_tool_missing(self, tmp_project):
        result = daemon._run_tool(tmp_project, "nonexistent.py", [])
        assert result.status == "skipped"
        assert "not found" in result.message

    def test_run_tool_success(self, tmp_project):
        # Create a minimal tool that exits 0
        tool = tmp_project / "framework" / "tools" / "ok-tool.py"
        tool.write_text("import argparse\np=argparse.ArgumentParser()\np.add_argument('--project-root')\np.parse_args()\nprint('OK')\n")
        result = daemon._run_tool(tmp_project, "ok-tool.py", [])
        assert result.status == "success"
        assert "OK" in result.message

    def test_run_tool_fail(self, tmp_project):
        # Create a tool that exits 1
        tool = tmp_project / "framework" / "tools" / "fail-tool.py"
        tool.write_text("import sys,argparse\np=argparse.ArgumentParser()\np.add_argument('--project-root')\np.parse_args()\nprint('FAIL',file=sys.stderr)\nsys.exit(1)\n")
        result = daemon._run_tool(tmp_project, "fail-tool.py", [])
        assert result.status == "failed"


# ── Maintenance Cycle ────────────────────────────────────────────────────────


class TestCycle:
    def test_run_cycle_no_tools(self, tmp_project):
        result = daemon.run_maintenance_cycle(tmp_project)
        assert result.cycle == 1
        assert len(result.tasks) == 3
        # All skipped since tools don't exist in tmp dir
        for t in result.tasks:
            assert t.status == "skipped"

    def test_run_cycle_increments(self, tmp_project):
        r1 = daemon.run_maintenance_cycle(tmp_project, cycle_num=1)
        r2 = daemon.run_maintenance_cycle(tmp_project, cycle_num=2)
        assert r1.cycle == 1
        assert r2.cycle == 2


# ── MCP Interface ────────────────────────────────────────────────────────────


class TestMCP:
    def test_mcp_status(self, tmp_project):
        result = daemon.mcp_grimoire_daemon(str(tmp_project), action="status")
        assert result["status"] == "ok"
        assert result["running"] is False

    def test_mcp_run_once(self, tmp_project):
        result = daemon.mcp_grimoire_daemon(str(tmp_project), action="run-once")
        assert result["status"] == "ok"
        assert "tasks" in result

    def test_mcp_unknown_action(self, tmp_project):
        result = daemon.mcp_grimoire_daemon(str(tmp_project), action="nope")
        assert result["status"] == "error"


# ── CLI ──────────────────────────────────────────────────────────────────────


class TestCLI:
    def test_status_cli(self, capsys, tmp_project):
        ret = daemon.main(["--project-root", str(tmp_project), "status"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "Daemon" in out

    def test_run_once_cli(self, capsys, tmp_project):
        ret = daemon.main(["--project-root", str(tmp_project), "run-once"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "Cycle" in out

    def test_stop_no_daemon(self, capsys, tmp_project):
        ret = daemon.main(["--project-root", str(tmp_project), "stop"])
        assert ret == 1
