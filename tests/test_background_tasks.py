"""Tests for background-tasks.py — Story 5.3."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "framework" / "tools" / "background-tasks.py"


def _load():
    mod_name = "background_tasks"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


bt = _load()


class TestConstants(unittest.TestCase):
    def test_version(self):
        self.assertTrue(bt.BACKGROUND_TASKS_VERSION)

    def test_max_concurrent(self):
        self.assertEqual(bt.MAX_CONCURRENT_TASKS, 2)

    def test_valid_task_types(self):
        self.assertIn("analysis", bt.VALID_TASK_TYPES)
        self.assertIn("indexing", bt.VALID_TASK_TYPES)
        self.assertIn("custom", bt.VALID_TASK_TYPES)

    def test_valid_statuses(self):
        self.assertIn("running", bt.VALID_STATUSES)
        self.assertIn("completed", bt.VALID_STATUSES)
        self.assertIn("cancelled", bt.VALID_STATUSES)


class TestTaskProgress(unittest.TestCase):
    def test_defaults(self):
        p = bt.TaskProgress()
        self.assertEqual(p.percentage, 0)
        self.assertEqual(p.current_step, "")
        self.assertEqual(p.total_steps, 0)

    def test_with_data(self):
        p = bt.TaskProgress(percentage=50, current_step="Indexing")
        self.assertEqual(p.percentage, 50)


class TestBackgroundTask(unittest.TestCase):
    def test_create(self):
        t = bt.BackgroundTask(agent="dev", task_type="analysis", description="Analyze code")
        self.assertTrue(t.task_id.startswith("bg-"))
        self.assertEqual(t.agent, "dev")
        self.assertEqual(t.status, "pending")

    def test_to_dict(self):
        t = bt.BackgroundTask(agent="qa", task_type="custom", description="Run tests")
        d = t.to_dict()
        self.assertIn("task_id", d)
        self.assertIn("agent", d)

    def test_from_dict(self):
        t = bt.BackgroundTask(agent="dev", task_type="analysis", description="test")
        d = t.to_dict()
        restored = bt.BackgroundTask.from_dict(d)
        self.assertEqual(restored.agent, "dev")
        self.assertEqual(restored.task_type, "analysis")

    def test_is_terminal(self):
        t = bt.BackgroundTask(status="completed")
        self.assertTrue(t.is_terminal)
        t2 = bt.BackgroundTask(status="running")
        self.assertFalse(t2.is_terminal)


class TestTaskList(unittest.TestCase):
    def test_defaults(self):
        tl = bt.TaskList()
        self.assertEqual(tl.total_count, 0)
        self.assertEqual(tl.running_count, 0)
        self.assertEqual(tl.max_concurrent, bt.MAX_CONCURRENT_TASKS)


class TestBackgroundTaskManager(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.mgr = bt.BackgroundTaskManager(Path(self.tmpdir))

    def test_start_task(self):
        task = self.mgr.start(agent="dev", task_type="analysis", description="Test task")
        self.assertTrue(task.task_id)
        self.assertEqual(task.status, "running")
        self.assertTrue(task.result_path)

    def test_get_status(self):
        task = self.mgr.start(agent="dev", description="Test")
        status = self.mgr.get_status(task.task_id)
        self.assertIsNotNone(status)
        self.assertEqual(status.agent, "dev")

    def test_get_status_nonexistent(self):
        status = self.mgr.get_status("nonexistent")
        self.assertIsNone(status)

    def test_complete_task(self):
        task = self.mgr.start(agent="dev", description="Test")
        result = self.mgr.complete(task.task_id, "Analysis complete")
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "completed")

    def test_cancel_task(self):
        task = self.mgr.start(agent="dev", description="Test")
        result = self.mgr.cancel(task.task_id)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "cancelled")

    def test_update_progress(self):
        task = self.mgr.start(agent="dev", description="Test")
        result = self.mgr.update_progress(task.task_id, percentage=50, current_step="Step 2")
        self.assertIsNotNone(result)
        status = self.mgr.get_status(task.task_id)
        self.assertEqual(status.progress.percentage, 50)

    def test_list_tasks(self):
        self.mgr.start(agent="dev", description="T1")
        self.mgr.start(agent="qa", description="T2")
        tl = self.mgr.list_tasks()
        self.assertGreaterEqual(tl.total_count, 2)

    def test_check_in(self):
        task = self.mgr.start(agent="dev", description="Test")
        result = self.mgr.check_in(task.task_id)
        self.assertIsNotNone(result)
        self.assertIn("task_id", result)
        self.assertIn("status", result)

    def test_check_in_nonexistent(self):
        result = self.mgr.check_in("nonexistent")
        self.assertIn("error", result)

    def test_result_file_created(self):
        task = self.mgr.start(agent="dev", description="Test")
        result_path = Path(task.result_path)
        self.assertTrue(result_path.exists())


class TestMCPInterface(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def test_mcp_start(self):
        result = bt.mcp_background_task(
            self.tmpdir, action="start",
            agent="dev", description="MCP test",
        )
        self.assertTrue(result.get("success"))

    def test_mcp_list(self):
        bt.mcp_background_task(
            self.tmpdir, action="start",
            agent="dev", description="T",
        )
        result = bt.mcp_background_task(self.tmpdir, action="status")
        self.assertIn("total", result)

    def test_mcp_status_single(self):
        start_result = bt.mcp_background_task(
            self.tmpdir, action="start",
            agent="dev", description="T",
        )
        task_id = start_result["task"]["task_id"]
        result = bt.mcp_background_task(self.tmpdir, action="status", task_id=task_id)
        self.assertIn("task_id", result)


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
        self.assertIn("background-tasks", r.stdout)

    def test_list(self):
        tmpdir = tempfile.mkdtemp()
        r = self._run("--project-root", tmpdir, "list")
        self.assertEqual(r.returncode, 0)

    def test_start_and_status(self):
        tmpdir = tempfile.mkdtemp()
        r = self._run("--project-root", tmpdir, "start",
                       "--agent", "dev", "--task", "CLI test",
                       "--type", "analysis", "--json")
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        self.assertIn("task_id", data)

    def test_status_command(self):
        tmpdir = tempfile.mkdtemp()
        r = self._run("--project-root", tmpdir, "status")
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
