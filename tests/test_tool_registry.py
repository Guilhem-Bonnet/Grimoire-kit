#!/usr/bin/env python3
"""
Tests pour tool-registry.py — Registry unifié des outils BMAD (BM-45 Story 6.1).

Fonctions testées :
  - BmadTool (to_mcp, to_anthropic, to_openai)
  - ToolParameter, ValidationResult, RegistryStats
  - ToolDiscoverer._inspect_python_tool(), _inspect_shell_tool(), _inspect_markdown_tool()
  - ToolRegistry (discover, get, filter_by_tag, export_all, validate_all, stats)
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
TOOL = KIT_DIR / "framework" / "tools" / "tool-registry.py"


def _import_mod():
    mod_name = "tool_registry"
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
        self.assertTrue(hasattr(self.mod, "TOOL_REGISTRY_VERSION"))

    def test_version_format(self):
        parts = self.mod.TOOL_REGISTRY_VERSION.split(".")
        self.assertEqual(len(parts), 3)

    def test_tools_dir(self):
        self.assertEqual(self.mod.TOOLS_DIR, "framework/tools")

    def test_python_to_json_type_mapping(self):
        m = self.mod.PYTHON_TO_JSON_TYPE
        self.assertEqual(m["str"], "string")
        self.assertEqual(m["int"], "integer")
        self.assertEqual(m["bool"], "boolean")


# ── Data Classes ────────────────────────────────────────────────────────────

class TestToolParameter(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_parameter_defaults(self):
        p = self.mod.ToolParameter(name="test")
        self.assertEqual(p.param_type, "string")
        self.assertFalse(p.required)
        self.assertIsNone(p.default)

    def test_parameter_with_enum(self):
        p = self.mod.ToolParameter(name="format", enum=["mcp", "openai"])
        self.assertEqual(len(p.enum), 2)


class TestBmadTool(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tool = self.mod.BmadTool(
            name="test-tool",
            description="A test tool",
            source_file="framework/tools/test-tool.py",
            parameters=[
                self.mod.ToolParameter(name="input", param_type="string", required=True,
                                       description="Input file"),
                self.mod.ToolParameter(name="verbose", param_type="boolean",
                                       description="Verbose output"),
            ],
        )

    def test_to_mcp(self):
        mcp = self.tool.to_mcp()
        self.assertEqual(mcp["name"], "test-tool")
        self.assertIn("inputSchema", mcp)
        self.assertIn("input", mcp["inputSchema"]["properties"])
        self.assertIn("input", mcp["inputSchema"]["required"])

    def test_to_mcp_json_serializable(self):
        mcp = self.tool.to_mcp()
        serialized = json.dumps(mcp)
        self.assertIsInstance(serialized, str)

    def test_to_anthropic(self):
        anthropic = self.tool.to_anthropic()
        self.assertEqual(anthropic["name"], "test-tool")
        self.assertIn("input_schema", anthropic)
        self.assertIn("input", anthropic["input_schema"]["properties"])

    def test_to_anthropic_json_serializable(self):
        anthropic = self.tool.to_anthropic()
        serialized = json.dumps(anthropic)
        self.assertIsInstance(serialized, str)

    def test_to_openai(self):
        openai = self.tool.to_openai()
        self.assertEqual(openai["type"], "function")
        self.assertEqual(openai["function"]["name"], "test-tool")
        self.assertIn("parameters", openai["function"])

    def test_to_openai_json_serializable(self):
        openai = self.tool.to_openai()
        serialized = json.dumps(openai)
        self.assertIsInstance(serialized, str)

    def test_no_required_params(self):
        tool = self.mod.BmadTool(
            name="simple", description="Simple tool", source_file="x.py",
            parameters=[self.mod.ToolParameter(name="opt", required=False)],
        )
        mcp = tool.to_mcp()
        self.assertNotIn("required", mcp.get("inputSchema", {}))

    def test_param_with_enum(self):
        tool = self.mod.BmadTool(
            name="format-tool", description="T", source_file="x.py",
            parameters=[self.mod.ToolParameter(
                name="format", enum=["json", "yaml"], required=True,
            )],
        )
        mcp = tool.to_mcp()
        self.assertIn("enum", mcp["inputSchema"]["properties"]["format"])

    def test_empty_parameters(self):
        tool = self.mod.BmadTool(name="empty", description="T", source_file="x.py")
        mcp = tool.to_mcp()
        self.assertEqual(mcp["inputSchema"]["properties"], {})


class TestValidationResult(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_defaults(self):
        r = self.mod.ValidationResult(tool_name="test")
        self.assertTrue(r.valid)
        self.assertEqual(r.errors, [])
        self.assertEqual(r.warnings, [])


class TestRegistryStats(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_defaults(self):
        s = self.mod.RegistryStats()
        self.assertEqual(s.total_tools, 0)
        self.assertEqual(s.python_tools, 0)


# ── ToolDiscoverer ──────────────────────────────────────────────────────────

class TestToolDiscoverer(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        self.tools_dir = self.tmpdir / "framework" / "tools"
        self.tools_dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_discover_empty_dir(self):
        d = self.mod.ToolDiscoverer(self.tmpdir)
        tools = d.discover_all()
        self.assertEqual(tools, [])

    def test_discover_python_tool(self):
        tool_file = self.tools_dir / "my-tool.py"
        tool_file.write_text('''#!/usr/bin/env python3
"""
my-tool.py — My awesome tool for testing.
"""

MY_TOOL_VERSION = "1.0.0"

import argparse

def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="Run the tool")
    sub.add_parser("check", help="Check status")
    parser.add_argument("--verbose", help="Verbose output")
    args = parser.parse_args()

if __name__ == "__main__":
    main()
''')
        d = self.mod.ToolDiscoverer(self.tmpdir)
        tools = d.discover_all()
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0].name, "my-tool")
        self.assertIn("run", tools[0].subcommands)
        self.assertEqual(tools[0].version, "1.0.0")

    def test_discover_shell_tool(self):
        tool_file = self.tools_dir / "deploy.sh"
        tool_file.write_text("#!/bin/bash\n# Deploy the application\necho 'deploying'\n")
        d = self.mod.ToolDiscoverer(self.tmpdir)
        tools = d.discover_all()
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0].tool_type, "shell")

    def test_discover_markdown_tool(self):
        tool_file = self.tools_dir / "guide.md"
        tool_file.write_text("# BMAD Usage Guide\n\nThis is a guide.\n")
        d = self.mod.ToolDiscoverer(self.tmpdir)
        tools = d.discover_all()
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0].tool_type, "doc")

    def test_skip_underscore_files(self):
        (self.tools_dir / "_internal.py").write_text("# internal")
        d = self.mod.ToolDiscoverer(self.tmpdir)
        tools = d.discover_all()
        self.assertEqual(tools, [])

    def test_description_extraction(self):
        tool_file = self.tools_dir / "extractor.py"
        tool_file.write_text('"""\nextractor.py — Extract data from files.\n"""\nEXTRACTOR_VERSION = "0.1.0"\n')
        d = self.mod.ToolDiscoverer(self.tmpdir)
        tools = d.discover_all()
        self.assertEqual(len(tools), 1)
        self.assertIn("Extract data", tools[0].description)

    def test_tags_detection(self):
        tool_file = self.tools_dir / "rag-search.py"
        tool_file.write_text('"""\nrag-search.py — Search with Qdrant.\n"""\n')
        d = self.mod.ToolDiscoverer(self.tmpdir)
        tools = d.discover_all()
        self.assertIn("rag", tools[0].tags)


# ── ToolRegistry ───────────────────────────────────────────────────────────

class TestToolRegistry(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        self.tools_dir = self.tmpdir / "framework" / "tools"
        self.tools_dir.mkdir(parents=True)
        # Create sample tools
        (self.tools_dir / "tool-a.py").write_text(
            '"""\ntool-a.py — Tool A description.\n"""\nTOOL_A_VERSION = "1.0.0"\n'
        )
        (self.tools_dir / "tool-b.sh").write_text("#!/bin/bash\n# Tool B shell\n")
        (self.tools_dir / "tool-c.md").write_text("# Tool C Guide\n")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_discover_returns_count(self):
        reg = self.mod.ToolRegistry(self.tmpdir)
        count = reg.discover()
        self.assertEqual(count, 3)

    def test_tools_property(self):
        reg = self.mod.ToolRegistry(self.tmpdir)
        self.assertEqual(len(reg.tools), 3)

    def test_get_existing(self):
        reg = self.mod.ToolRegistry(self.tmpdir)
        tool = reg.get("tool-a")
        self.assertIsNotNone(tool)
        self.assertEqual(tool.name, "tool-a")

    def test_get_nonexistent(self):
        reg = self.mod.ToolRegistry(self.tmpdir)
        self.assertIsNone(reg.get("nonexistent"))

    def test_filter_by_type(self):
        reg = self.mod.ToolRegistry(self.tmpdir)
        shell_tools = reg.filter_by_type("shell")
        self.assertEqual(len(shell_tools), 1)

    def test_export_mcp(self):
        reg = self.mod.ToolRegistry(self.tmpdir)
        exported = reg.export_all("mcp")
        self.assertGreater(len(exported), 0)
        for item in exported:
            self.assertIn("name", item)
            self.assertIn("inputSchema", item)

    def test_export_anthropic(self):
        reg = self.mod.ToolRegistry(self.tmpdir)
        exported = reg.export_all("anthropic")
        for item in exported:
            self.assertIn("input_schema", item)

    def test_export_openai(self):
        reg = self.mod.ToolRegistry(self.tmpdir)
        exported = reg.export_all("openai")
        for item in exported:
            self.assertEqual(item["type"], "function")

    def test_validate_all(self):
        reg = self.mod.ToolRegistry(self.tmpdir)
        results = reg.validate_all()
        self.assertEqual(len(results), 3)
        valid_count = sum(1 for r in results if r.valid)
        self.assertEqual(valid_count, 3)

    def test_stats(self):
        reg = self.mod.ToolRegistry(self.tmpdir)
        s = reg.stats()
        self.assertEqual(s.total_tools, 3)
        self.assertEqual(s.python_tools, 1)
        self.assertEqual(s.shell_tools, 1)
        self.assertEqual(s.markdown_tools, 1)


class TestToolRegistryOnRealProject(unittest.TestCase):
    """Test registry on the actual bmad-custom-kit project."""

    def setUp(self):
        self.mod = _import_mod()

    def test_discover_real_tools(self):
        reg = self.mod.ToolRegistry(KIT_DIR)
        count = reg.discover()
        self.assertGreater(count, 10)

    def test_real_tools_validate(self):
        reg = self.mod.ToolRegistry(KIT_DIR)
        results = reg.validate_all()
        valid = sum(1 for r in results if r.valid)
        self.assertGreater(valid, 10)

    def test_export_real_mcp(self):
        reg = self.mod.ToolRegistry(KIT_DIR)
        exported = reg.export_all("mcp")
        self.assertGreater(len(exported), 0)
        # All should be JSON serializable
        serialized = json.dumps(exported)
        self.assertIsInstance(serialized, str)


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
        self.assertIn("Tool Registry", r.stdout)

    def test_version(self):
        r = self._run("--version")
        self.assertEqual(r.returncode, 0)
        self.assertIn("tool-registry", r.stdout)

    def test_list_command(self):
        r = self._run("--project-root", str(KIT_DIR), "list")
        self.assertEqual(r.returncode, 0)
        self.assertIn("BMAD Tool Registry", r.stdout)

    def test_stats_command(self):
        r = self._run("--project-root", str(KIT_DIR), "stats")
        self.assertEqual(r.returncode, 0)
        self.assertIn("Registry Stats", r.stdout)

    def test_validate_command(self):
        r = self._run("--project-root", str(KIT_DIR), "validate")
        self.assertEqual(r.returncode, 0)
        self.assertIn("Validation", r.stdout)

    def test_export_mcp_json(self):
        r = self._run("--project-root", str(KIT_DIR), "export", "--format", "mcp", "--json")
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        self.assertIsInstance(data, list)

    def test_export_anthropic(self):
        r = self._run("--project-root", str(KIT_DIR), "export", "--format", "anthropic")
        self.assertEqual(r.returncode, 0)

    def test_inspect_nonexistent(self):
        r = self._run("--project-root", str(KIT_DIR), "inspect", "--tool", "nonexistent")
        self.assertNotEqual(r.returncode, 0)

    def test_no_command_shows_help(self):
        r = self._run()
        self.assertIn(r.returncode, (0, 1))


if __name__ == "__main__":
    unittest.main()
