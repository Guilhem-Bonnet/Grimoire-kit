#!/usr/bin/env python3
"""
Tests pour synapse-dashboard.py — Dashboard unifié Synapse (Story 8.4).

Fonctions testées :
  - build_dashboard() — construction du rapport complet
  - render_markdown() — rendu Markdown
  - Section collectors — chaque section individuellement
  - mcp_synapse_dashboard() — interface MCP
  - CLI (--version, --format, --section, --output)
"""

import importlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT_DIR = Path(__file__).parent.parent
TOOL = KIT_DIR / "framework" / "tools" / "synapse-dashboard.py"


def _load():
    mod_name = "synapse_dashboard_test"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


dash = _load()


# ── Constants ────────────────────────────────────────────────────────────────

class TestConstants(unittest.TestCase):
    def test_version_defined(self):
        self.assertTrue(hasattr(dash, "DASHBOARD_VERSION"))

    def test_version_format(self):
        parts = dash.DASHBOARD_VERSION.split(".")
        self.assertEqual(len(parts), 3)

    def test_all_sections_tuple(self):
        self.assertIsInstance(dash.ALL_SECTIONS, tuple)
        self.assertGreater(len(dash.ALL_SECTIONS), 0)

    def test_all_sections_known(self):
        for s in dash.ALL_SECTIONS:
            self.assertIn(s, dash.SECTION_COLLECTORS)

    def test_section_collectors_all_callable(self):
        for name, fn in dash.SECTION_COLLECTORS.items():
            self.assertTrue(callable(fn), f"Collector {name} is not callable")


# ── Data Classes ─────────────────────────────────────────────────────────────

class TestSectionResult(unittest.TestCase):
    def test_create_default(self):
        sr = dash.SectionResult(name="test")
        self.assertEqual(sr.name, "test")
        self.assertEqual(sr.status, "ok")
        self.assertEqual(sr.markdown, "")

    def test_create_with_error(self):
        sr = dash.SectionResult(name="test", status="error", error="boom")
        self.assertEqual(sr.status, "error")
        self.assertEqual(sr.error, "boom")


class TestDashboardReport(unittest.TestCase):
    def test_create_default(self):
        report = dash.DashboardReport()
        self.assertEqual(report.sections, [])
        self.assertEqual(report.total_tools, 0)

    def test_to_dict(self):
        report = dash.DashboardReport(
            generated_at="2025-01-01T00:00:00Z",
            project_root="/tmp/test",
        )
        d = report.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["generated_at"], "2025-01-01T00:00:00Z")
        self.assertIn("sections", d)

    def test_to_dict_with_sections(self):
        report = dash.DashboardReport()
        report.sections.append(dash.SectionResult(name="test", markdown="# Test"))
        d = report.to_dict()
        self.assertEqual(len(d["sections"]), 1)
        self.assertEqual(d["sections"][0]["name"], "test")


# ── Section Collectors ───────────────────────────────────────────────────────

class TestSectionCollectors(unittest.TestCase):
    """Test chaque collecteur de section individuellement."""

    def setUp(self):
        self.project = Path(tempfile.mkdtemp())

    def test_collect_router(self):
        result = dash._collect_router(self.project)
        self.assertIsInstance(result, dash.SectionResult)
        self.assertEqual(result.name, "router")
        # May be unavailable or ok depending on tool presence
        self.assertIn(result.status, ("ok", "error", "unavailable"))

    def test_collect_cache(self):
        result = dash._collect_cache(self.project)
        self.assertIsInstance(result, dash.SectionResult)
        self.assertEqual(result.name, "cache")

    def test_collect_budget(self):
        result = dash._collect_budget(self.project)
        self.assertIsInstance(result, dash.SectionResult)
        self.assertEqual(result.name, "budget")

    def test_collect_orchestrator(self):
        result = dash._collect_orchestrator(self.project)
        self.assertIsInstance(result, dash.SectionResult)
        self.assertEqual(result.name, "orchestrator")

    def test_collect_bus(self):
        result = dash._collect_bus(self.project)
        self.assertIsInstance(result, dash.SectionResult)
        self.assertEqual(result.name, "bus")

    def test_collect_traces(self):
        result = dash._collect_traces(self.project)
        self.assertIsInstance(result, dash.SectionResult)
        self.assertEqual(result.name, "traces")

    def test_collect_workers(self):
        result = dash._collect_workers(self.project)
        self.assertIsInstance(result, dash.SectionResult)
        self.assertEqual(result.name, "workers")

    def test_collect_registry(self):
        result = dash._collect_registry(self.project)
        self.assertIsInstance(result, dash.SectionResult)
        self.assertEqual(result.name, "registry")

    def test_collect_mcp(self):
        result = dash._collect_mcp(self.project)
        self.assertIsInstance(result, dash.SectionResult)
        self.assertEqual(result.name, "mcp")

    def test_collect_mcp_has_tools(self):
        """MCP section should find tools since grimoire-mcp-tools.py exists."""
        result = dash._collect_mcp(KIT_DIR)
        if result.status == "ok":
            self.assertIn("tools", result.data)

    def test_all_collectors_return_section_result(self):
        for name, collector in dash.SECTION_COLLECTORS.items():
            result = collector(self.project)
            self.assertIsInstance(result, dash.SectionResult, f"Collector {name} returned wrong type")

    def test_collector_timing(self):
        for name, collector in dash.SECTION_COLLECTORS.items():
            result = collector(self.project)
            self.assertGreaterEqual(result.collection_time_ms, 0, f"Collector {name} has negative timing")


# ── Dashboard Builder ────────────────────────────────────────────────────────

class TestBuildDashboard(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp())

    def test_build_returns_report(self):
        report = dash.build_dashboard(self.project)
        self.assertIsInstance(report, dash.DashboardReport)

    def test_build_has_all_sections(self):
        report = dash.build_dashboard(self.project)
        self.assertEqual(len(report.sections), len(dash.ALL_SECTIONS))

    def test_build_with_filter(self):
        report = dash.build_dashboard(self.project, sections=("router", "bus"))
        self.assertEqual(len(report.sections), 2)
        names = {s.name for s in report.sections}
        self.assertEqual(names, {"router", "bus"})

    def test_build_single_section(self):
        report = dash.build_dashboard(self.project, sections=("mcp",))
        self.assertEqual(len(report.sections), 1)
        self.assertEqual(report.sections[0].name, "mcp")

    def test_build_unknown_section(self):
        report = dash.build_dashboard(self.project, sections=("nonexistent",))
        self.assertEqual(len(report.sections), 1)
        self.assertEqual(report.sections[0].status, "error")

    def test_build_generated_at(self):
        report = dash.build_dashboard(self.project)
        self.assertTrue(report.generated_at)
        self.assertIn("T", report.generated_at)

    def test_build_generation_time(self):
        report = dash.build_dashboard(self.project)
        self.assertGreaterEqual(report.generation_time_ms, 0)

    def test_build_project_root(self):
        report = dash.build_dashboard(self.project)
        self.assertEqual(report.project_root, str(self.project))


# ── Markdown Rendering ───────────────────────────────────────────────────────

class TestRenderMarkdown(unittest.TestCase):
    def test_render_empty(self):
        report = dash.DashboardReport(
            generated_at="2025-01-01T00:00:00Z",
            project_root="/tmp",
        )
        md = dash.render_markdown(report)
        self.assertIn("Synapse Intelligence Dashboard", md)
        self.assertIn("2025-01-01", md)

    def test_render_with_ok_section(self):
        report = dash.DashboardReport(generated_at="now")
        report.sections.append(
            dash.SectionResult(name="test", status="ok", markdown="## Test\n\nHello")
        )
        md = dash.render_markdown(report)
        self.assertIn("## Test", md)
        self.assertIn("Hello", md)
        self.assertIn("1 ✅", md)

    def test_render_with_error_section(self):
        report = dash.DashboardReport(generated_at="now")
        report.sections.append(
            dash.SectionResult(name="broken", status="error", error="Something failed")
        )
        md = dash.render_markdown(report)
        self.assertIn("Something failed", md)
        self.assertIn("1 ❌", md)

    def test_render_with_unavailable_section(self):
        report = dash.DashboardReport(generated_at="now")
        report.sections.append(
            dash.SectionResult(name="missing", status="unavailable", error="Not found")
        )
        md = dash.render_markdown(report)
        self.assertIn("indisponible", md)
        self.assertIn("1 ⚠️", md)

    def test_render_footer(self):
        report = dash.DashboardReport(generated_at="now")
        md = dash.render_markdown(report)
        self.assertIn("synapse-dashboard.py", md)

    def test_render_full_dashboard(self):
        report = dash.build_dashboard(KIT_DIR)
        md = dash.render_markdown(report)
        self.assertIsInstance(md, str)
        self.assertGreater(len(md), 100)


# ── MCP Interface ────────────────────────────────────────────────────────────

class TestMCPInterface(unittest.TestCase):
    def setUp(self):
        self.project = tempfile.mkdtemp()

    def test_mcp_markdown(self):
        result = dash.mcp_synapse_dashboard(self.project)
        self.assertIsInstance(result, dict)
        self.assertEqual(result["format"], "markdown")
        self.assertIn("content", result)
        self.assertIn("Synapse", result["content"])

    def test_mcp_json(self):
        result = dash.mcp_synapse_dashboard(self.project, output_format="json")
        self.assertIsInstance(result, dict)
        self.assertIn("sections", result)
        self.assertIn("generated_at", result)

    def test_mcp_with_sections(self):
        result = dash.mcp_synapse_dashboard(self.project, sections="bus,traces")
        self.assertIsInstance(result, dict)
        self.assertEqual(result["sections_total"], 2)

    def test_mcp_empty_sections(self):
        result = dash.mcp_synapse_dashboard(self.project, sections="")
        self.assertIsInstance(result, dict)
        # Empty string = all sections
        self.assertEqual(result["sections_total"], len(dash.ALL_SECTIONS))

    def test_mcp_single_section(self):
        result = dash.mcp_synapse_dashboard(self.project, sections="mcp")
        self.assertEqual(result["sections_total"], 1)


# ── CLI Integration ──────────────────────────────────────────────────────────

class TestCLIIntegration(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(TOOL)] + list(args),
            capture_output=True, text=True, timeout=30,
        )

    def test_version(self):
        r = self._run("--version")
        self.assertEqual(r.returncode, 0)
        self.assertIn("synapse-dashboard", r.stdout)

    def test_default_output(self):
        r = self._run("--project-root", str(KIT_DIR))
        self.assertEqual(r.returncode, 0)
        self.assertIn("Synapse Intelligence Dashboard", r.stdout)

    def test_json_output(self):
        r = self._run("--project-root", str(KIT_DIR), "--format", "json")
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        self.assertIn("sections", data)

    def test_section_filter(self):
        r = self._run("--project-root", str(KIT_DIR), "--section", "mcp")
        self.assertEqual(r.returncode, 0)
        # Section name is .title() → "Mcp" or rendered markdown may have "MCP"
        self.assertTrue("mcp" in r.stdout.lower() or "MCP" in r.stdout)

    def test_output_to_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "dashboard.md"
            r = self._run("--project-root", str(KIT_DIR), "--output", str(out_file))
            self.assertEqual(r.returncode, 0)
            self.assertTrue(out_file.exists())
            content = out_file.read_text(encoding="utf-8")
            self.assertIn("Synapse", content)

    def test_json_output_to_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "dashboard.json"
            r = self._run(
                "--project-root", str(KIT_DIR),
                "--format", "json",
                "--output", str(out_file),
            )
            self.assertEqual(r.returncode, 0)
            self.assertTrue(out_file.exists())
            data = json.loads(out_file.read_text(encoding="utf-8"))
            self.assertIn("sections", data)


# ── Import Helper ────────────────────────────────────────────────────────────

class TestImportTool(unittest.TestCase):
    def test_import_existing_tool(self):
        mod = dash._import_tool("synapse-config.py", "synapse_config_import_test")
        self.assertIsNotNone(mod)

    def test_import_nonexistent_tool(self):
        mod = dash._import_tool("nonexistent-tool.py", "no_tool_test")
        self.assertIsNone(mod)


# ── Enhanced Budget Section (Sprint 2) ───────────────────────────────────────

class TestEnhancedBudgetSection(unittest.TestCase):
    """Tests for enhanced _collect_budget in v1.1.0."""

    def test_version_bumped(self):
        self.assertEqual(dash.DASHBOARD_VERSION, "1.1.0")

    def test_collect_budget_callable(self):
        self.assertTrue(callable(dash._collect_budget))

    def test_collect_budget_returns_section_result(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = dash._collect_budget(Path(tmpdir))
            self.assertIsInstance(result, dash.SectionResult)
            self.assertEqual(result.name, "budget")

    def test_collect_budget_with_real_project(self):
        result = dash._collect_budget(KIT_DIR)
        self.assertIsInstance(result, dash.SectionResult)
        if result.status == "ok":
            self.assertIn("Token Budget", result.markdown)
            # Check for new enhanced features
            self.assertIn("Utilisation", result.markdown)

    def test_collect_budget_contains_bar(self):
        result = dash._collect_budget(KIT_DIR)
        if result.status == "ok":
            # Bar uses █ or ░ characters depending on usage
            self.assertTrue("█" in result.markdown or "░" in result.markdown)

    def test_collect_budget_data_has_trend(self):
        result = dash._collect_budget(KIT_DIR)
        if result.status == "ok" and isinstance(result.data, dict):
            self.assertIn("trend", result.data)


if __name__ == "__main__":
    unittest.main()
