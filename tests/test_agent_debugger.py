"""Tests for agent-debugger.py — reality-first agent observability debugger."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "framework" / "tools" / "agent-debugger.py"


def _load():
    mod_name = "agent_debugger"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


dbg = _load()


class TestConstants(unittest.TestCase):
    def test_version(self):
        self.assertTrue(dbg.DEBUGGER_VERSION)

    def test_known_paths(self):
        self.assertIn("mcp-audit.jsonl", dbg.MCP_AUDIT_FILE)
        self.assertIn("pheromone-board.json", dbg.PHEROMONE_FILE)


class TestSnapshot(unittest.TestCase):
    def test_empty_project_marks_planned_systems(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = dbg.build_snapshot(Path(tmp))
            by_name = {item.name: item for item in snapshot.capabilities}
            self.assertEqual(by_name["stigmergy"].state, dbg.STATE_PLANNED_ONLY)
            self.assertEqual(by_name["session_chain"].state, dbg.STATE_PLANNED_ONLY)
            self.assertEqual(by_name["vector_db"].state, dbg.STATE_PLANNED_ONLY)

    def test_qdrant_initialized_but_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            qdrant = Path(tmp) / "_grimoire-output" / ".qdrant_data"
            qdrant.mkdir(parents=True)
            (qdrant / "meta.json").write_text(json.dumps({"collections": {}, "aliases": {}}), encoding="utf-8")
            snapshot = dbg.build_snapshot(Path(tmp))
            by_name = {item.name: item for item in snapshot.capabilities}
            self.assertEqual(by_name["vector_db"].state, dbg.STATE_INITIALIZED_EMPTY)

    def test_active_runtime_streams(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory_dir = Path(tmp) / "_grimoire" / "_memory"
            memory_dir.mkdir(parents=True)
            (memory_dir / "mcp-audit.jsonl").write_text(json.dumps({"ts": "2026", "tool": "x"}) + "\n", encoding="utf-8")
            (memory_dir / "token-usage.jsonl").write_text(json.dumps({"ts": "2026", "used": 12}) + "\n", encoding="utf-8")
            output_dir = Path(tmp) / "_grimoire-output"
            output_dir.mkdir(parents=True)
            (output_dir / ".router-stats.jsonl").write_text(json.dumps({"timestamp": "2026", "agent": "dev"}) + "\n", encoding="utf-8")
            snapshot = dbg.build_snapshot(Path(tmp))
            by_name = {item.name: item for item in snapshot.capabilities}
            self.assertEqual(by_name["mcp_audit"].state, dbg.STATE_ACTIVE)
            self.assertEqual(by_name["router_stats"].state, dbg.STATE_ACTIVE)
            self.assertEqual(by_name["token_usage"].state, dbg.STATE_ACTIVE)

    def test_ant_vector_integration_is_not_integrated_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools_dir = Path(tmp) / "framework" / "tools"
            tools_dir.mkdir(parents=True)
            (tools_dir / "stigmergy.py").write_text("print('stigmergy')\n", encoding="utf-8")
            (tools_dir / "rag-indexer.py").write_text("print('qdrant')\n", encoding="utf-8")
            (tools_dir / "memory-sync.py").write_text("print('sync')\n", encoding="utf-8")
            (tools_dir / "rag-retriever.py").write_text("print('retrieve')\n", encoding="utf-8")
            snapshot = dbg.build_snapshot(Path(tmp))
            by_name = {item.name: item for item in snapshot.capabilities}
            self.assertEqual(by_name["ant_vector_integration"].state, dbg.STATE_NOT_INTEGRATED)
            self.assertGreaterEqual(len(by_name["ant_vector_integration"].opportunities), 3)


class TestClaims(unittest.TestCase):
    def test_claims_contradict_missing_parallelism_and_vector_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = dbg.build_snapshot(Path(tmp))
            claims = {item.claim: item for item in snapshot.claims}
            self.assertEqual(claims["Des sessions parallèles multi-agents sont prouvées."].verdict, "contradicted")
            self.assertIn(claims["La DB vectorielle est réellement utilisée."].verdict, {"contradicted", "unverifiable"})


class TestPlan(unittest.TestCase):
    def test_plan_is_generated(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = dbg.build_snapshot(Path(tmp))
            self.assertIsNotNone(snapshot.plan)
            self.assertGreaterEqual(len(snapshot.plan.tasks), 1)

    def test_plan_contains_ant_vector_task_when_not_integrated(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = dbg.build_snapshot(Path(tmp))
            titles = [task.title for task in snapshot.plan.tasks]
            self.assertIn("Brancher l'ant system sur la DB vectorielle", titles)


class TestHtml(unittest.TestCase):
    def test_generate_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = dbg.build_snapshot(Path(tmp))
            html = dbg.generate_html(snapshot)
            self.assertTrue(html.startswith("<!DOCTYPE html>"))
            self.assertIn("Agent Debugger Visuel", html)
            self.assertIn("health score", html)
            self.assertIn("Plan priorisé", html)
            self.assertNotIn("__AGENT_DEBUGGER_DATA__", html)

    def test_write_dashboard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = dbg.write_dashboard(root)
            self.assertTrue(target.exists())
            html = target.read_text(encoding="utf-8")
            self.assertIn("Claims vérifiés", html)
            self.assertIn("Capacités observées", html)


class TestCLI(unittest.TestCase):
    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True, timeout=15)

    def test_status_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run("--project-root", tmp, "status")
            self.assertEqual(result.returncode, 0)
            self.assertIn("health score", result.stdout)

    def test_report_json_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run("--project-root", tmp, "report", "--format", "json")
            self.assertEqual(result.returncode, 0)
            data = json.loads(result.stdout)
            self.assertIn("capabilities", data)
            self.assertIn("claims", data)

    def test_plan_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run("--project-root", tmp, "plan")
            self.assertEqual(result.returncode, 0)
            self.assertIn("Plan de fiabilisation agentique", result.stdout)

    def test_generate_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self._run("--project-root", tmp, "generate")
            self.assertEqual(result.returncode, 0)
            generated = Path(result.stdout.strip())
            self.assertTrue(generated.exists())
            self.assertTrue(str(generated).startswith(str(root.resolve())))


if __name__ == "__main__":
    unittest.main()