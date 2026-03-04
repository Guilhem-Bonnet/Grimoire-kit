"""Tests pour synapse-trace.py — Middleware de traçabilité Synapse (Story 7.2)."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

# ── Load module via importlib ────────────────────────────────────────────────

TOOLS_DIR = Path(__file__).resolve().parent.parent / "framework" / "tools"


def _load_module(name: str):
    mod_name = name.replace("-", "_")
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    mod_path = TOOLS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(mod_name, mod_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load_module("synapse-trace")


# ── TraceEntry tests ─────────────────────────────────────────────────────────


class TestTraceEntry(unittest.TestCase):
    """Test TraceEntry data class."""

    def test_default_timestamp_set(self):
        entry = mod.TraceEntry(tool="test", operation="op")
        self.assertTrue(entry.timestamp)
        self.assertIn("T", entry.timestamp)

    def test_explicit_timestamp(self):
        entry = mod.TraceEntry(timestamp="2025-01-01T00:00:00Z", tool="t", operation="o")
        self.assertEqual(entry.timestamp, "2025-01-01T00:00:00Z")

    def test_default_status(self):
        entry = mod.TraceEntry(tool="t", operation="o")
        self.assertEqual(entry.status, "ok")

    def test_to_dict(self):
        entry = mod.TraceEntry(tool="router", operation="route", agent="dev")
        d = entry.to_dict()
        self.assertEqual(d["tool"], "router")
        self.assertEqual(d["operation"], "route")
        self.assertEqual(d["agent"], "dev")
        self.assertIn("timestamp", d)

    def test_from_dict(self):
        data = {"tool": "orchestrator", "operation": "plan", "agent": "architect", "status": "error"}
        entry = mod.TraceEntry.from_dict(data)
        self.assertEqual(entry.tool, "orchestrator")
        self.assertEqual(entry.status, "error")

    def test_from_dict_ignores_unknown(self):
        data = {"tool": "t", "operation": "o", "unknown_field": 42}
        entry = mod.TraceEntry.from_dict(data)
        self.assertEqual(entry.tool, "t")

    def test_to_markdown(self):
        entry = mod.TraceEntry(
            timestamp="2025-01-01T00:00:00Z",
            tool="router",
            operation="classify",
            agent="dev",
            duration_ms=42.5,
            tokens_estimated=100,
            status="ok",
        )
        md = entry.to_markdown()
        self.assertIn("[SYNAPSE]", md)
        self.assertIn("router.classify", md)
        self.assertIn("dev", md)
        self.assertIn("42ms", md)
        self.assertIn("~100", md)

    def test_to_markdown_with_details(self):
        entry = mod.TraceEntry(
            tool="cache",
            operation="lookup",
            details={"hit": True, "similarity": 0.95},
        )
        md = entry.to_markdown()
        self.assertIn("hit", md)

    def test_roundtrip_dict(self):
        original = mod.TraceEntry(tool="t", operation="o", agent="a", duration_ms=10, tokens_estimated=5)
        restored = mod.TraceEntry.from_dict(original.to_dict())
        self.assertEqual(original.tool, restored.tool)
        self.assertEqual(original.agent, restored.agent)


# ── TraceStats tests ─────────────────────────────────────────────────────────


class TestTraceStats(unittest.TestCase):
    """Test TraceStats data class."""

    def test_defaults(self):
        stats = mod.TraceStats()
        self.assertEqual(stats.total_entries, 0)
        self.assertEqual(stats.total_duration_ms, 0.0)
        self.assertEqual(stats.errors_count, 0)

    def test_fields_exist(self):
        stats = mod.TraceStats()
        self.assertIsInstance(stats.by_tool, dict)
        self.assertIsInstance(stats.by_agent, dict)
        self.assertIsInstance(stats.by_status, dict)


# ── SynapseTracer tests ─────────────────────────────────────────────────────


class TestSynapseTracer(unittest.TestCase):
    """Test SynapseTracer core functionality."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.root = Path(self._tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        mod.reset_global_tracer()

    def test_init(self):
        tracer = mod.SynapseTracer(self.root)
        self.assertEqual(len(tracer.entries), 0)

    def test_trace_path(self):
        tracer = mod.SynapseTracer(self.root)
        self.assertIn("BMAD_TRACE.md", str(tracer.trace_path))

    def test_record_dry_run(self):
        tracer = mod.SynapseTracer(self.root, dry_run=True)
        entry = mod.TraceEntry(tool="test", operation="test_op")
        tracer.record(entry)
        self.assertEqual(len(tracer.entries), 1)
        # File should NOT exist in dry_run
        self.assertFalse(tracer.trace_path.exists())

    def test_record_writes_file(self):
        tracer = mod.SynapseTracer(self.root)
        entry = mod.TraceEntry(tool="router", operation="classify")
        tracer.record(entry)
        self.assertTrue(tracer.trace_path.exists())
        content = tracer.trace_path.read_text()
        self.assertIn("[SYNAPSE]", content)
        self.assertIn("router.classify", content)

    def test_record_disabled(self):
        tracer = mod.SynapseTracer(self.root, enabled=False)
        entry = mod.TraceEntry(tool="t", operation="o")
        tracer.record(entry)
        self.assertEqual(len(tracer.entries), 0)

    def test_multiple_records(self):
        tracer = mod.SynapseTracer(self.root, dry_run=True)
        for i in range(5):
            tracer.record(mod.TraceEntry(tool=f"tool{i}", operation="op"))
        self.assertEqual(len(tracer.entries), 5)

    def test_entries_capped(self):
        tracer = mod.SynapseTracer(self.root, dry_run=True)
        original_max = mod.MAX_TRACE_ENTRIES
        mod.MAX_TRACE_ENTRIES = 10
        try:
            for i in range(20):
                tracer.record(mod.TraceEntry(tool=f"tool{i}", operation="op"))
            self.assertLessEqual(len(tracer.entries), 10)
        finally:
            mod.MAX_TRACE_ENTRIES = original_max

    def test_load_from_empty_file(self):
        tracer = mod.SynapseTracer(self.root)
        count = tracer.load_from_file()
        self.assertEqual(count, 0)

    def test_record_then_load(self):
        # Write entries
        tracer1 = mod.SynapseTracer(self.root)
        tracer1.record(mod.TraceEntry(tool="orchestrator", operation="plan", agent="dev"))
        tracer1.record(mod.TraceEntry(tool="router", operation="route", status="error"))

        # Load from a fresh tracer
        tracer2 = mod.SynapseTracer(self.root)
        count = tracer2.load_from_file()
        self.assertGreaterEqual(count, 1)  # At least one parsed

    def test_get_stats_empty(self):
        tracer = mod.SynapseTracer(self.root, dry_run=True)
        stats = tracer.get_stats()
        self.assertEqual(stats.total_entries, 0)
        self.assertEqual(stats.total_duration_ms, 0.0)

    def test_get_stats_populated(self):
        tracer = mod.SynapseTracer(self.root, dry_run=True)
        tracer.record(mod.TraceEntry(tool="router", operation="op1", duration_ms=10, tokens_estimated=50))
        tracer.record(mod.TraceEntry(tool="router", operation="op2", duration_ms=20, tokens_estimated=100))
        tracer.record(mod.TraceEntry(tool="cache", operation="lookup", duration_ms=5, status="error"))
        stats = tracer.get_stats()
        self.assertEqual(stats.total_entries, 3)
        self.assertEqual(stats.by_tool["router"], 2)
        self.assertEqual(stats.by_tool["cache"], 1)
        self.assertAlmostEqual(stats.total_duration_ms, 35)
        self.assertEqual(stats.total_tokens, 150)
        self.assertEqual(stats.errors_count, 1)

    def test_get_stats_oldest_newest(self):
        tracer = mod.SynapseTracer(self.root, dry_run=True)
        tracer.record(mod.TraceEntry(timestamp="2025-01-01T00:00:00Z", tool="t", operation="o"))
        tracer.record(mod.TraceEntry(timestamp="2025-06-15T12:00:00Z", tool="t", operation="o"))
        stats = tracer.get_stats()
        self.assertEqual(stats.oldest_entry, "2025-01-01T00:00:00Z")
        self.assertEqual(stats.newest_entry, "2025-06-15T12:00:00Z")


# ── Search tests ─────────────────────────────────────────────────────────────


class TestSearch(unittest.TestCase):
    """Test trace search functionality."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.root = Path(self._tmpdir)
        self.tracer = mod.SynapseTracer(self.root, dry_run=True)
        entries = [
            mod.TraceEntry(tool="router", operation="classify", agent="dev", status="ok"),
            mod.TraceEntry(tool="router", operation="route", agent="architect", status="ok"),
            mod.TraceEntry(tool="orchestrator", operation="plan", agent="dev", status="error"),
            mod.TraceEntry(tool="cache", operation="lookup", agent="qa", status="ok"),
            mod.TraceEntry(tool="cache", operation="store", agent="dev", status="ok"),
        ]
        for e in entries:
            self.tracer.record(e)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        mod.reset_global_tracer()

    def test_search_all(self):
        result = self.tracer.search()
        self.assertEqual(result.total_matches, 5)

    def test_search_by_tool(self):
        result = self.tracer.search(tool="router")
        self.assertEqual(result.total_matches, 2)

    def test_search_by_agent(self):
        result = self.tracer.search(agent="dev")
        self.assertEqual(result.total_matches, 3)

    def test_search_by_status(self):
        result = self.tracer.search(status="error")
        self.assertEqual(result.total_matches, 1)
        self.assertEqual(result.matches[0].tool, "orchestrator")

    def test_search_combined(self):
        result = self.tracer.search(tool="router", agent="dev")
        self.assertEqual(result.total_matches, 1)

    def test_search_limit(self):
        result = self.tracer.search(limit=2)
        self.assertEqual(result.total_matches, 2)

    def test_search_no_match(self):
        result = self.tracer.search(tool="nonexistent")
        self.assertEqual(result.total_matches, 0)

    def test_search_case_insensitive(self):
        result = self.tracer.search(tool="Router")
        self.assertEqual(result.total_matches, 2)

    def test_search_query_string(self):
        result = self.tracer.search(tool="cache")
        self.assertIn("tool=cache", result.query)


# ── Clear tests ──────────────────────────────────────────────────────────────


class TestClear(unittest.TestCase):
    """Test clear_synapse_entries."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.root = Path(self._tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        mod.reset_global_tracer()

    def test_clear_removes_entries(self):
        tracer = mod.SynapseTracer(self.root)
        tracer.record(mod.TraceEntry(tool="a", operation="b"))
        tracer.record(mod.TraceEntry(tool="c", operation="d"))
        count = tracer.clear_synapse_entries()
        self.assertEqual(count, 2)
        self.assertEqual(len(tracer.entries), 0)

    def test_clear_preserves_non_synapse(self):
        trace_path = self.root / "_bmad-output" / "BMAD_TRACE.md"
        trace_path.parent.mkdir(parents=True)
        existing = "### [2025-01-01] Some other trace\n- detail: value\n"
        trace_path.write_text(existing, encoding="utf-8")

        tracer = mod.SynapseTracer(self.root)
        tracer.record(mod.TraceEntry(tool="test", operation="op"))
        tracer.clear_synapse_entries()

        content = trace_path.read_text()
        self.assertIn("Some other trace", content)
        self.assertNotIn("[SYNAPSE]", content)

    def test_clear_no_file(self):
        tracer = mod.SynapseTracer(self.root)
        count = tracer.clear_synapse_entries()
        self.assertEqual(count, 0)


# ── Export tests ─────────────────────────────────────────────────────────────


class TestExport(unittest.TestCase):
    """Test JSON export."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.root = Path(self._tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        mod.reset_global_tracer()

    def test_export_empty(self):
        tracer = mod.SynapseTracer(self.root, dry_run=True)
        result = tracer.export_json()
        self.assertEqual(result, [])

    def test_export_entries(self):
        tracer = mod.SynapseTracer(self.root, dry_run=True)
        tracer.record(mod.TraceEntry(tool="a", operation="b"))
        tracer.record(mod.TraceEntry(tool="c", operation="d"))
        result = tracer.export_json()
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["tool"], "a")
        self.assertEqual(result[1]["tool"], "c")

    def test_export_serializable(self):
        tracer = mod.SynapseTracer(self.root, dry_run=True)
        tracer.record(mod.TraceEntry(tool="t", operation="o", details={"key": "value"}))
        result = tracer.export_json()
        # Must be JSON serializable
        j = json.dumps(result)
        self.assertIn("key", j)


# ── Global Tracer tests ─────────────────────────────────────────────────────


class TestGlobalTracer(unittest.TestCase):
    """Test global tracer singleton."""

    def setUp(self):
        mod.reset_global_tracer()

    def tearDown(self):
        mod.reset_global_tracer()

    def test_get_creates_tracer(self):
        tracer = mod.get_global_tracer("/tmp/test")
        self.assertIsInstance(tracer, mod.SynapseTracer)

    def test_get_returns_same(self):
        t1 = mod.get_global_tracer("/tmp/test")
        t2 = mod.get_global_tracer("/tmp/test")
        self.assertIs(t1, t2)

    def test_set_replaces(self):
        custom = mod.SynapseTracer("/tmp/custom", dry_run=True)
        mod.set_global_tracer(custom)
        self.assertIs(mod.get_global_tracer(), custom)

    def test_reset_clears(self):
        mod.get_global_tracer("/tmp/test")
        mod.reset_global_tracer()
        # Next call should create new
        t = mod.get_global_tracer("/tmp/test2")
        self.assertIsInstance(t, mod.SynapseTracer)


# ── Decorator tests ──────────────────────────────────────────────────────────


class TestSynapseTracedDecorator(unittest.TestCase):
    """Test @synapse_traced decorator."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.root = Path(self._tmpdir)
        self.tracer = mod.SynapseTracer(self.root, dry_run=True)
        mod.set_global_tracer(self.tracer)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        mod.reset_global_tracer()

    def test_decorator_records_success(self):
        @mod.synapse_traced("test-tool", "test-op")
        def sample_fn():
            return {"status": "ok"}

        sample_fn()
        self.assertEqual(len(self.tracer.entries), 1)
        entry = self.tracer.entries[0]
        self.assertEqual(entry.tool, "test-tool")
        self.assertEqual(entry.operation, "test-op")
        self.assertEqual(entry.status, "ok")

    def test_decorator_records_error(self):
        @mod.synapse_traced("fail-tool", "fail-op")
        def failing_fn():
            raise ValueError("boom")

        with self.assertRaises(ValueError):
            failing_fn()

        self.assertEqual(len(self.tracer.entries), 1)
        entry = self.tracer.entries[0]
        self.assertEqual(entry.status, "error")
        self.assertIn("boom", entry.details.get("error", ""))

    def test_decorator_measures_duration(self):
        @mod.synapse_traced("slow-tool", "slow-op")
        def slow_fn():
            time.sleep(0.01)
            return {}

        slow_fn()
        entry = self.tracer.entries[0]
        self.assertGreater(entry.duration_ms, 0)

    def test_decorator_with_agent_kwarg(self):
        @mod.synapse_traced("tool", "op")
        def fn_with_agent(agent=""):
            return {}

        fn_with_agent(agent="dev")
        entry = self.tracer.entries[0]
        self.assertEqual(entry.agent, "dev")

    def test_decorator_preserves_return(self):
        @mod.synapse_traced("tool", "op")
        def fn():
            return {"status": "ok", "data": 42}

        result = fn()
        self.assertEqual(result["data"], 42)

    def test_decorator_captures_result_status(self):
        @mod.synapse_traced("tool", "op")
        def fn():
            return {"status": "success"}

        fn()
        entry = self.tracer.entries[0]
        self.assertEqual(entry.details.get("result_status"), "success")


# ── MCP Interface tests ─────────────────────────────────────────────────────


class TestMCPInterface(unittest.TestCase):
    """Test MCP tool function."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.root = Path(self._tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        mod.reset_global_tracer()

    def test_mcp_status(self):
        result = mod.mcp_synapse_trace(str(self.root), action="status")
        self.assertEqual(result["status"], "ok")
        self.assertIn("stats", result)
        self.assertEqual(result["stats"]["total_entries"], 0)

    def test_mcp_search(self):
        result = mod.mcp_synapse_trace(str(self.root), action="search", tool="router")
        self.assertEqual(result["status"], "ok")
        self.assertIn("matches", result)

    def test_mcp_export(self):
        result = mod.mcp_synapse_trace(str(self.root), action="export")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["count"], 0)
        self.assertIsInstance(result["entries"], list)

    def test_mcp_clear(self):
        result = mod.mcp_synapse_trace(str(self.root), action="clear")
        self.assertEqual(result["status"], "ok")
        self.assertIn("cleared", result)

    def test_mcp_unknown_action(self):
        result = mod.mcp_synapse_trace(str(self.root), action="foo")
        self.assertEqual(result["status"], "error")


# ── CLI tests ────────────────────────────────────────────────────────────────


class TestCLI(unittest.TestCase):
    """Test CLI commands."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.root = Path(self._tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        mod.reset_global_tracer()

    def test_no_command_shows_help(self):
        r = mod.main([])
        self.assertIn(r, (0, 1))

    def test_status_command(self):
        r = mod.main(["--project-root", str(self.root), "status"])
        self.assertEqual(r, 0)

    def test_search_command(self):
        r = mod.main(["--project-root", str(self.root), "search", "--tool", "router"])
        self.assertEqual(r, 0)

    def test_search_json(self):
        r = mod.main(["--project-root", str(self.root), "search", "--json"])
        self.assertEqual(r, 0)

    def test_export_json(self):
        r = mod.main(["--project-root", str(self.root), "export", "--format", "json"])
        self.assertEqual(r, 0)

    def test_export_markdown(self):
        r = mod.main(["--project-root", str(self.root), "export", "--format", "markdown"])
        self.assertEqual(r, 0)

    def test_clear_command(self):
        r = mod.main(["--project-root", str(self.root), "clear"])
        self.assertEqual(r, 0)


if __name__ == "__main__":
    unittest.main()
