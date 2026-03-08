#!/usr/bin/env python3
"""
Tests pour agent-caller.py — Agent-to-Agent Tool Calling Grimoire (BM-45 Story 6.2).

Fonctions testées :
  - AgentCallRequest, AgentCallResponse, AgentToolSpec
  - TraceWriter.write()
  - CallHistoryManager (record, get_history, get_stats)
  - AgentCaller (list_agents, get_agent_schema, call, get_history, get_stats)
  - main()
"""

import importlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT_DIR = Path(__file__).parent.parent
TOOL = KIT_DIR / "framework" / "tools" / "agent-caller.py"


def _import_mod():
    mod_name = "agent_caller"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Constants ────────────────────────────────────────────────────────────────

class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_version_defined(self):
        self.assertTrue(hasattr(self.mod, "AGENT_CALLER_VERSION"))

    def test_version_format(self):
        parts = self.mod.AGENT_CALLER_VERSION.split(".")
        self.assertEqual(len(parts), 3)

    def test_default_timeout(self):
        self.assertEqual(self.mod.DEFAULT_TIMEOUT, 120)

    def test_max_retries(self):
        self.assertEqual(self.mod.MAX_RETRIES, 3)

    def test_known_agents_not_empty(self):
        self.assertGreater(len(self.mod.KNOWN_AGENTS), 5)

    def test_known_agents_has_dev(self):
        self.assertIn("dev", self.mod.KNOWN_AGENTS)

    def test_known_agents_has_architect(self):
        self.assertIn("architect", self.mod.KNOWN_AGENTS)

    def test_agent_has_title(self):
        for agent_id, info in self.mod.KNOWN_AGENTS.items():
            self.assertIn("title", info, f"Agent {agent_id} missing title")

    def test_agent_has_capabilities(self):
        for agent_id, info in self.mod.KNOWN_AGENTS.items():
            self.assertIn("capabilities", info, f"Agent {agent_id} missing capabilities")


# ── Data Classes ────────────────────────────────────────────────────────────

class TestAgentCallRequest(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_auto_call_id(self):
        req = self.mod.AgentCallRequest(from_agent="dev", to_agent="architect", task="Review")
        self.assertTrue(len(req.call_id) > 0)

    def test_auto_timestamp(self):
        req = self.mod.AgentCallRequest(from_agent="dev", to_agent="architect", task="Review")
        self.assertIn("T", req.timestamp)

    def test_auto_trace_id(self):
        req = self.mod.AgentCallRequest(from_agent="dev", to_agent="architect", task="Review")
        self.assertTrue(req.trace_id.startswith("a2a-"))

    def test_custom_values(self):
        req = self.mod.AgentCallRequest(
            call_id="custom-id",
            from_agent="dev",
            to_agent="qa",
            task="Test this",
            context="src/auth.py",
            timeout=60,
        )
        self.assertEqual(req.call_id, "custom-id")
        self.assertEqual(req.timeout, 60)


class TestAgentCallResponse(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_defaults(self):
        resp = self.mod.AgentCallResponse()
        self.assertEqual(resp.status, "pending")
        self.assertTrue(resp.validation_passed)
        self.assertEqual(resp.retries, 0)


class TestAgentToolSpec(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_to_tool_use(self):
        spec = self.mod.AgentToolSpec(
            agent_id="architect",
            title="Architect",
            description="Architecture review",
            capabilities=["API design", "scalable patterns"],
        )
        tool_use = spec.to_tool_use()
        self.assertEqual(tool_use["name"], "call_agent_architect")
        self.assertIn("input_schema", tool_use)
        self.assertIn("task", tool_use["input_schema"]["properties"])

    def test_to_function_calling(self):
        spec = self.mod.AgentToolSpec(
            agent_id="dev",
            title="Developer",
            description="Code implementation",
        )
        fc = spec.to_function_calling()
        self.assertEqual(fc["type"], "function")
        self.assertEqual(fc["function"]["name"], "call_agent_dev")

    def test_tool_use_json_serializable(self):
        spec = self.mod.AgentToolSpec(
            agent_id="qa", title="QA", description="Testing",
        )
        serialized = json.dumps(spec.to_tool_use())
        self.assertIsInstance(serialized, str)

    def test_function_calling_json_serializable(self):
        spec = self.mod.AgentToolSpec(
            agent_id="qa", title="QA", description="Testing",
        )
        serialized = json.dumps(spec.to_function_calling())
        self.assertIsInstance(serialized, str)


# ── TraceWriter ─────────────────────────────────────────────────────────────

class TestTraceWriter(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_write_creates_file(self):
        (self.tmpdir / "_grimoire-output").mkdir()
        writer = self.mod.TraceWriter(self.tmpdir)
        writer.write("dev", "TOOL:call", "test payload")
        self.assertTrue(writer.trace_file.exists())

    def test_write_appends(self):
        (self.tmpdir / "_grimoire-output").mkdir()
        writer = self.mod.TraceWriter(self.tmpdir)
        writer.write("dev", "TOOL:call", "first")
        writer.write("architect", "TOOL:result", "second")
        content = writer.trace_file.read_text()
        self.assertIn("first", content)
        self.assertIn("second", content)
        self.assertEqual(content.count("\n"), 2)

    def test_write_format(self):
        (self.tmpdir / "_grimoire-output").mkdir()
        writer = self.mod.TraceWriter(self.tmpdir)
        writer.write("dev", "ACTION", "did something")
        content = writer.trace_file.read_text()
        self.assertIn("[dev]", content)
        self.assertIn("[ACTION]", content)


# ── CallHistoryManager ─────────────────────────────────────────────────────

class TestCallHistoryManager(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        (self.tmpdir / "_grimoire-output").mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_empty_history(self):
        mgr = self.mod.CallHistoryManager(self.tmpdir)
        history = mgr.get_history()
        self.assertEqual(history, [])

    def test_record_and_retrieve(self):
        mgr = self.mod.CallHistoryManager(self.tmpdir)
        req = self.mod.AgentCallRequest(
            from_agent="dev", to_agent="architect", task="Review"
        )
        resp = self.mod.AgentCallResponse(
            call_id=req.call_id, status="success", tokens_used=100
        )
        mgr.record(req, resp)
        history = mgr.get_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["status"], "success")

    def test_last_n(self):
        mgr = self.mod.CallHistoryManager(self.tmpdir)
        for i in range(5):
            req = self.mod.AgentCallRequest(
                from_agent="dev", to_agent="qa", task=f"Task {i}"
            )
            resp = self.mod.AgentCallResponse(call_id=req.call_id, status="success")
            mgr.record(req, resp)
        history = mgr.get_history(last_n=3)
        self.assertEqual(len(history), 3)

    def test_stats_empty(self):
        mgr = self.mod.CallHistoryManager(self.tmpdir)
        stats = mgr.get_stats()
        self.assertEqual(stats["total_calls"], 0)

    def test_stats_with_data(self):
        mgr = self.mod.CallHistoryManager(self.tmpdir)
        for status in ["success", "success", "error"]:
            req = self.mod.AgentCallRequest(
                from_agent="dev", to_agent="architect", task="T"
            )
            resp = self.mod.AgentCallResponse(
                call_id=req.call_id, status=status, tokens_used=50
            )
            mgr.record(req, resp)
        stats = mgr.get_stats()
        self.assertEqual(stats["total_calls"], 3)
        self.assertEqual(stats["success"], 2)
        self.assertEqual(stats["errors"], 1)
        self.assertAlmostEqual(stats["success_rate"], 0.667, places=2)

    def test_persistence(self):
        mgr1 = self.mod.CallHistoryManager(self.tmpdir)
        req = self.mod.AgentCallRequest(from_agent="dev", to_agent="qa", task="T")
        resp = self.mod.AgentCallResponse(call_id=req.call_id, status="success")
        mgr1.record(req, resp)

        mgr2 = self.mod.CallHistoryManager(self.tmpdir)
        history = mgr2.get_history()
        self.assertEqual(len(history), 1)


# ── AgentCaller ────────────────────────────────────────────────────────────

class TestAgentCaller(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        (self.tmpdir / "_grimoire-output").mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_list_agents(self):
        caller = self.mod.AgentCaller(self.tmpdir)
        agents = caller.list_agents()
        self.assertGreater(len(agents), 0)
        names = [a.agent_id for a in agents]
        self.assertIn("dev", names)
        self.assertIn("architect", names)

    def test_get_agent_schema(self):
        caller = self.mod.AgentCaller(self.tmpdir)
        spec = caller.get_agent_schema("dev")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.agent_id, "dev")

    def test_get_agent_schema_nonexistent(self):
        caller = self.mod.AgentCaller(self.tmpdir)
        spec = caller.get_agent_schema("nonexistent-agent")
        self.assertIsNone(spec)

    def test_call_success(self):
        caller = self.mod.AgentCaller(self.tmpdir)
        req = self.mod.AgentCallRequest(
            from_agent="dev",
            to_agent="architect",
            task="Review the auth module architecture",
            context="src/auth/",
        )
        resp = caller.call(req)
        self.assertEqual(resp.status, "success")
        self.assertGreater(len(resp.response), 0)
        self.assertGreater(resp.tokens_used, 0)

    def test_call_unknown_agent(self):
        caller = self.mod.AgentCaller(self.tmpdir)
        req = self.mod.AgentCallRequest(
            from_agent="dev",
            to_agent="nonexistent-agent",
            task="Do something",
        )
        resp = caller.call(req)
        self.assertEqual(resp.status, "error")
        self.assertIn("inconnu", resp.response)

    def test_call_generates_trace(self):
        caller = self.mod.AgentCaller(self.tmpdir)
        req = self.mod.AgentCallRequest(
            from_agent="dev", to_agent="architect", task="Review"
        )
        caller.call(req)
        # Trace is at _grimoire-output/Grimoire_TRACE.md
        actual_trace = self.tmpdir / "_grimoire-output" / "Grimoire_TRACE.md"
        self.assertTrue(actual_trace.exists())
        content = actual_trace.read_text()
        self.assertIn("TOOL:call", content)

    def test_call_records_history(self):
        caller = self.mod.AgentCaller(self.tmpdir)
        req = self.mod.AgentCallRequest(
            from_agent="dev", to_agent="qa", task="Test this"
        )
        caller.call(req)
        history = caller.get_history()
        self.assertEqual(len(history), 1)

    def test_call_prompt_format(self):
        caller = self.mod.AgentCaller(self.tmpdir)
        req = self.mod.AgentCallRequest(
            from_agent="dev",
            to_agent="architect",
            task="Review auth module",
            context="src/auth/",
            expected_format="JSON with decision field",
        )
        resp = caller.call(req)
        self.assertIn("Agent Call", resp.response)
        self.assertIn("Review auth module", resp.response)
        self.assertIn("Context", resp.response)
        self.assertIn("Expected Output Format", resp.response)

    def test_get_stats(self):
        caller = self.mod.AgentCaller(self.tmpdir)
        for target in ["architect", "qa", "architect"]:
            req = self.mod.AgentCallRequest(
                from_agent="dev", to_agent=target, task="Task"
            )
            caller.call(req)
        stats = caller.get_stats()
        self.assertEqual(stats["total_calls"], 3)
        self.assertEqual(stats["calls_per_agent"]["architect"], 2)

    def test_multiple_calls_sequential(self):
        caller = self.mod.AgentCaller(self.tmpdir)
        for i in range(3):
            req = self.mod.AgentCallRequest(
                from_agent="dev", to_agent="architect", task=f"Task {i}"
            )
            resp = caller.call(req)
            self.assertEqual(resp.status, "success")
        history = caller.get_history()
        self.assertEqual(len(history), 3)


# ── Config Loading ──────────────────────────────────────────────────────────

class TestConfigLoading(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_config_no_file(self):
        config = self.mod.load_caller_config(self.tmpdir)
        self.assertEqual(config, {})


# ── CLI Integration ────────────────────────────────────────────────────────

class TestCLIIntegration(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(TOOL), *args],
            capture_output=True, text=True, timeout=15,
        )

    def test_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("Agent Caller", r.stdout)

    def test_version(self):
        r = self._run("--version")
        self.assertEqual(r.returncode, 0)
        self.assertIn("agent-caller", r.stdout)

    def test_list_command(self):
        r = self._run("--project-root", str(KIT_DIR), "list")
        self.assertEqual(r.returncode, 0)
        self.assertIn("Agents appelables", r.stdout)

    def test_history_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            r = self._run("--project-root", tmpdir, "history")
            self.assertEqual(r.returncode, 0)
            self.assertIn("Historique", r.stdout)

    def test_history_stats(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            r = self._run("--project-root", tmpdir, "history", "--stats")
            self.assertEqual(r.returncode, 0)
            self.assertIn("Statistiques", r.stdout)

    def test_schema_known_agent(self):
        r = self._run("--project-root", str(KIT_DIR), "schema", "--agent", "architect")
        self.assertEqual(r.returncode, 0)
        self.assertIn("Architect", r.stdout)

    def test_schema_unknown_agent(self):
        r = self._run("--project-root", str(KIT_DIR), "schema", "--agent", "nonexistent")
        self.assertNotEqual(r.returncode, 0)

    def test_call_command(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create _grimoire-output for trace
            Path(tmpdir, "_grimoire-output").mkdir()
            r = self._run(
                "--project-root", tmpdir,
                "call", "--from", "dev", "--to", "architect",
                "--task", "Review the module",
            )
            self.assertEqual(r.returncode, 0)
            self.assertIn("Agent Call", r.stdout)

    def test_call_json_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "_grimoire-output").mkdir()
            r = self._run(
                "--project-root", tmpdir,
                "call", "--from", "dev", "--to", "qa",
                "--task", "Test this", "--json",
            )
            self.assertEqual(r.returncode, 0)
            data = json.loads(r.stdout)
            self.assertEqual(data["status"], "success")

    def test_no_command_shows_help(self):
        r = self._run()
        self.assertIn(r.returncode, (0, 1))


if __name__ == "__main__":
    unittest.main()
