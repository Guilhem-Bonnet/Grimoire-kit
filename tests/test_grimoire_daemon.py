"""Tests for grimoire-daemon.py — Background maintenance daemon."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

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
    (tmp_path / "_grimoire" / "_memory" / "daemon").mkdir(parents=True)
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
        assert len(result.tasks) == 5
        # First 3 skipped since tools don't exist in tmp dir
        for t in result.tasks[:3]:
            assert t.status == "skipped"
        # memory-bridge succeeds (syncs 0 files)
        assert result.tasks[3].name == "memory-bridge"
        # auto-evolve skipped (no fitness-tracker.py in tmp dir)
        assert result.tasks[4].name == "auto-evolve"

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


# ── Memory Bridge ────────────────────────────────────────────────────────────


class TestMemoryBridge:
    def test_bridge_no_source(self, tmp_path):
        # tmp_path frais sans _grimoire/_memory/
        result = daemon._run_memory_bridge(tmp_path)
        assert result.name == "memory-bridge"
        assert result.status == "skipped"

    def test_bridge_syncs_md_files(self, tmp_project):
        src = tmp_project / daemon.MEMORY_BRIDGE_SOURCE
        src.mkdir(parents=True, exist_ok=True)
        (src / "test.md").write_text("# Test\n", encoding="utf-8")
        (src / "data.json").write_text('{"k": 1}', encoding="utf-8")

        result = daemon._run_memory_bridge(tmp_project)
        assert result.status == "success"
        assert "2" in result.message  # 2 files synced

        target = tmp_project / daemon.MEMORY_BRIDGE_TARGET
        assert (target / "test.md").exists()
        assert (target / "data.json").exists()

    def test_bridge_skips_jsonl(self, tmp_project):
        src = tmp_project / daemon.MEMORY_BRIDGE_SOURCE
        src.mkdir(parents=True, exist_ok=True)
        (src / "large.jsonl").write_text('{"big": true}\n', encoding="utf-8")

        result = daemon._run_memory_bridge(tmp_project)
        assert result.status == "success"
        target = tmp_project / daemon.MEMORY_BRIDGE_TARGET
        assert not (target / "large.jsonl").exists()

    def test_bridge_skip_unchanged(self, tmp_project):
        src = tmp_project / daemon.MEMORY_BRIDGE_SOURCE
        src.mkdir(parents=True, exist_ok=True)
        (src / "test.md").write_text("# V1\n", encoding="utf-8")

        # First sync
        daemon._run_memory_bridge(tmp_project)
        # Second sync — should sync 0
        result = daemon._run_memory_bridge(tmp_project)
        assert "0" in result.message


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


# ── Auto-Evolve Cycle ───────────────────────────────────────────────────────


class TestAutoEvolve:
    def test_evolve_no_fitness_tool(self, tmp_project):
        """Sans fitness-tracker.py, le cycle evolve est skipped."""
        result = daemon._run_evolve_cycle(tmp_project)
        assert result.name == "auto-evolve"
        assert result.status == "skipped"
        assert "indisponible" in result.message

    def test_evolve_with_fitness_tool(self, tmp_project):
        """Avec un fitness-tracker.py minimal, le cycle evolve fonctionne."""
        tool = tmp_project / "framework" / "tools" / "fitness-tracker.py"
        tool.write_text(
            'import argparse, json\n'
            'p = argparse.ArgumentParser()\n'
            'p.add_argument("--project-root")\n'
            'p.add_argument("--json", action="store_true")\n'
            'p.add_argument("command", nargs="?")\n'
            'p.parse_args()\n'
            'print(json.dumps({"fitness_score": 85, "level": "HEALTHY"}))\n',
        )
        result = daemon._run_evolve_cycle(tmp_project)
        assert result.name == "auto-evolve"
        assert result.status == "success"
        assert "85" in result.message
        assert "HEALTHY" in result.message

    def test_evolve_low_fitness_triggers_suggestions(self, tmp_project):
        """Fitness < 70 déclenche les suggestions (si self-healing existe)."""
        tool = tmp_project / "framework" / "tools" / "fitness-tracker.py"
        tool.write_text(
            'import argparse, json\n'
            'p = argparse.ArgumentParser()\n'
            'p.add_argument("--project-root")\n'
            'p.add_argument("--json", action="store_true")\n'
            'p.add_argument("command", nargs="?")\n'
            'p.parse_args()\n'
            'print(json.dumps({"fitness_score": 35, "level": "CRITICAL"}))\n',
        )
        result = daemon._run_evolve_cycle(tmp_project)
        assert result.status == "success"
        assert "35" in result.message

    def test_evolve_logs_to_jsonl(self, tmp_project):
        """Le cycle evolve écrit dans evolve-cycle.jsonl."""
        tool = tmp_project / "framework" / "tools" / "fitness-tracker.py"
        tool.write_text(
            'import argparse, json\n'
            'p = argparse.ArgumentParser()\n'
            'p.add_argument("--project-root")\n'
            'p.add_argument("--json", action="store_true")\n'
            'p.add_argument("command", nargs="?")\n'
            'p.parse_args()\n'
            'print(json.dumps({"fitness_score": 72, "level": "HEALTHY"}))\n',
        )
        daemon._run_evolve_cycle(tmp_project)
        log_path = tmp_project / "_grimoire" / "_memory" / "evolve-cycle.jsonl"
        assert log_path.exists()

        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["fitness"] == 72
        assert entry["level"] == "HEALTHY"

    def test_cycle_includes_evolve(self, tmp_project):
        """run_maintenance_cycle inclut la tâche auto-evolve."""
        result = daemon.run_maintenance_cycle(tmp_project)
        task_names = [t.name for t in result.tasks]
        assert "auto-evolve" in task_names
        assert len(result.tasks) == 5  # 4 original + auto-evolve
