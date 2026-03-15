"""Tests for agent-worker.py — Story 4.2."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "framework" / "tools" / "agent-worker.py"


def _load():
    mod_name = "agent_worker"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


aw = _load()


class TestConstants(unittest.TestCase):
    def test_version(self):
        self.assertTrue(aw.AGENT_WORKER_VERSION)

    def test_max_parallel(self):
        self.assertEqual(aw.MAX_PARALLEL_WORKERS, 5)

    def test_known_agents(self):
        self.assertIn("dev", aw.KNOWN_AGENTS)
        self.assertIn("architect", aw.KNOWN_AGENTS)
        self.assertIn("qa", aw.KNOWN_AGENTS)
        self.assertIn("pm", aw.KNOWN_AGENTS)

    def test_valid_providers(self):
        self.assertIn("anthropic", aw.VALID_PROVIDERS)
        self.assertIn("openai", aw.VALID_PROVIDERS)
        self.assertIn("ollama", aw.VALID_PROVIDERS)
        self.assertIn("copilot", aw.VALID_PROVIDERS)


class TestWorkerConfig(unittest.TestCase):
    def test_defaults(self):
        c = aw.WorkerConfig(agent_id="dev")
        self.assertEqual(c.agent_id, "dev")
        self.assertEqual(c.provider, aw.DEFAULT_PROVIDER)
        self.assertEqual(c.max_tasks, 100)


class TestWorkerStatus(unittest.TestCase):
    def test_create(self):
        ws = aw.WorkerStatus(agent_id="dev", status="running")
        self.assertTrue(ws.worker_id.startswith("w-"))
        self.assertEqual(ws.status, "running")

    def test_to_dict(self):
        ws = aw.WorkerStatus(agent_id="dev")
        d = ws.to_dict()
        self.assertIn("worker_id", d)
        self.assertIn("agent_id", d)

    def test_from_dict(self):
        ws = aw.WorkerStatus(agent_id="qa", status="running")
        d = ws.to_dict()
        restored = aw.WorkerStatus.from_dict(d)
        self.assertEqual(restored.agent_id, "qa")


class TestWorkerList(unittest.TestCase):
    def test_defaults(self):
        wl = aw.WorkerList()
        self.assertEqual(wl.total_count, 0)
        self.assertEqual(wl.running_count, 0)
        self.assertEqual(wl.max_parallel, aw.MAX_PARALLEL_WORKERS)


class TestTaskResult(unittest.TestCase):
    def test_create(self):
        r = aw.TaskResult(task_id="t1", agent_id="dev", status="success")
        self.assertEqual(r.task_id, "t1")
        self.assertEqual(r.status, "success")


class TestAgentWorkerManager(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.mgr = aw.AgentWorkerManager(Path(self.tmpdir))

    def test_start_worker(self):
        ws = self.mgr.start_worker("dev")
        self.assertEqual(ws.agent_id, "dev")
        self.assertEqual(ws.status, "running")
        self.assertTrue(ws.worker_id)
        self.assertTrue(ws.model)  # Should resolve a model

    def test_start_unknown_agent(self):
        with self.assertRaises(ValueError):
            self.mgr.start_worker("nonexistent-agent")

    def test_stop_worker(self):
        self.mgr.start_worker("dev")
        ws = self.mgr.stop_worker("dev")
        self.assertIsNotNone(ws)
        self.assertEqual(ws.status, "stopped")

    def test_stop_nonexistent(self):
        result = self.mgr.stop_worker("nobody")
        self.assertIsNone(result)

    def test_get_status(self):
        self.mgr.start_worker("qa")
        ws = self.mgr.get_status("qa")
        self.assertIsNotNone(ws)
        self.assertEqual(ws.agent_id, "qa")

    def test_list_workers(self):
        self.mgr.start_worker("dev")
        self.mgr.start_worker("qa")
        wl = self.mgr.list_workers()
        self.assertGreaterEqual(wl.total_count, 2)

    def test_execute_task(self):
        self.mgr.start_worker("dev")
        result = self.mgr.execute_task("dev", {"description": "Test task", "task_id": "t1"})
        self.assertEqual(result.status, "success")
        self.assertEqual(result.agent_id, "dev")
        self.assertIn("result", result.output)

    def test_list_available_agents(self):
        agents = self.mgr.list_available_agents()
        self.assertGreater(len(agents), 5)
        ids = [a["agent_id"] for a in agents]
        self.assertIn("dev", ids)
        self.assertIn("architect", ids)


class TestMCPInterface(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def test_mcp_list(self):
        result = aw.mcp_agent_worker(self.tmpdir, action="list")
        self.assertIn("agents", result)
        self.assertGreater(len(result["agents"]), 0)

    def test_mcp_start(self):
        result = aw.mcp_agent_worker(self.tmpdir, action="start", agent_id="dev")
        self.assertTrue(result.get("success"))

    def test_mcp_start_missing(self):
        result = aw.mcp_agent_worker(self.tmpdir, action="start")
        self.assertIn("error", result)

    def test_mcp_stop(self):
        aw.mcp_agent_worker(self.tmpdir, action="start", agent_id="dev")
        result = aw.mcp_agent_worker(self.tmpdir, action="stop", agent_id="dev")
        self.assertTrue(result.get("success"))

    def test_mcp_status(self):
        result = aw.mcp_agent_worker(self.tmpdir, action="status")
        self.assertIn("total", result)


class TestCLIIntegration(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(TOOL), *list(args)],
            capture_output=True, text=True, timeout=15,
        )

    def test_no_command_shows_help(self):
        r = self._run()
        self.assertIn(r.returncode, (0, 1))

    def test_version(self):
        r = self._run("--version")
        self.assertEqual(r.returncode, 0)
        self.assertIn("agent-worker", r.stdout)

    def test_list(self):
        r = self._run("list")
        self.assertEqual(r.returncode, 0)

    def test_start_and_status(self):
        tmpdir = tempfile.mkdtemp()
        r = self._run("--project-root", tmpdir, "start", "--agent", "dev", "--json")
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        self.assertIn("agent_id", data)

    def test_status(self):
        tmpdir = tempfile.mkdtemp()
        r = self._run("--project-root", tmpdir, "status")
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
