"""Tests for dep-check.py — T3 Dependency Registry."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "framework" / "tools" / "dep-check.py"


def _load():
    mod_name = "dep_check_mod"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


dc = _load()


class TestVersion(unittest.TestCase):
    def test_version(self):
        self.assertTrue(dc.DEP_CHECK_VERSION)


class TestStripDocstrings(unittest.TestCase):
    def test_removes_triple_quotes(self):
        src = 'x = 1\n"""docstring with tool-name.py"""\ny = 2'
        stripped = dc._strip_docstrings(src)
        self.assertNotIn("tool-name", stripped)
        self.assertIn("x = 1", stripped)
        self.assertIn("y = 2", stripped)

    def test_removes_comments(self):
        src = "x = 1  # reference to tool-name.py\ny = 2"
        stripped = dc._strip_docstrings(src)
        self.assertNotIn("tool-name", stripped)

    def test_preserves_code(self):
        src = '_load_tool("real-tool")\n# not a real dep\n'
        stripped = dc._strip_docstrings(src)
        self.assertIn('_load_tool("real-tool")', stripped)


class TestScanDependencies(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.tools_dir = Path(self.tmpdir) / "framework" / "tools"
        self.tools_dir.mkdir(parents=True)

    def test_empty_project(self):
        graph = dc.scan_dependencies(Path(self.tmpdir))
        self.assertEqual(graph, {})

    def test_standalone_tool(self):
        (self.tools_dir / "my-tool.py").write_text("x = 1\n")
        graph = dc.scan_dependencies(Path(self.tmpdir))
        self.assertIn("my-tool", graph)
        self.assertEqual(graph["my-tool"], [])

    def test_load_tool_dep(self):
        (self.tools_dir / "alpha.py").write_text('x = _load_tool("beta")\n')
        (self.tools_dir / "beta.py").write_text("y = 1\n")
        graph = dc.scan_dependencies(Path(self.tmpdir))
        self.assertEqual(graph["alpha"], ["beta"])
        self.assertEqual(graph["beta"], [])

    def test_local_import(self):
        (self.tools_dir / "main.py").write_text("from rnd_core import foo\n")
        (self.tools_dir / "rnd_core.py").write_text("foo = 1\n")
        graph = dc.scan_dependencies(Path(self.tmpdir))
        self.assertEqual(graph["main"], ["rnd_core"])

    def test_self_reference_excluded(self):
        (self.tools_dir / "alpha.py").write_text('x = _load_tool("alpha")\n')
        graph = dc.scan_dependencies(Path(self.tmpdir))
        self.assertEqual(graph["alpha"], [])

    def test_nonexistent_root(self):
        graph = dc.scan_dependencies(Path("/nonexistent/path/xyz"))
        self.assertEqual(graph, {})


class TestCheckMissing(unittest.TestCase):
    def test_all_resolved(self):
        graph = {"a": ["b"], "b": []}
        issues = dc.check_missing(graph, {"a", "b"})
        self.assertEqual(issues, [])

    def test_missing_dep(self):
        graph = {"a": ["missing"], "b": []}
        issues = dc.check_missing(graph, {"a", "b"})
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["missing_dep"], "missing")


class TestFindOrphans(unittest.TestCase):
    def test_no_orphans(self):
        graph = {"a": ["b"], "b": ["a"]}
        orphans = dc.find_orphans(graph)
        self.assertEqual(orphans, [])

    def test_orphans(self):
        graph = {"a": ["b"], "b": [], "c": []}
        orphans = dc.find_orphans(graph)
        self.assertIn("a", orphans)
        self.assertIn("c", orphans)
        self.assertNotIn("b", orphans)


class TestFindCycles(unittest.TestCase):
    def test_no_cycle(self):
        graph = {"a": ["b"], "b": [], "c": []}
        cycles = dc.find_cycles(graph)
        self.assertEqual(cycles, [])

    def test_direct_cycle(self):
        graph = {"a": ["b"], "b": ["a"]}
        cycles = dc.find_cycles(graph)
        self.assertTrue(len(cycles) > 0)

    def test_triangle_cycle(self):
        graph = {"a": ["b"], "b": ["c"], "c": ["a"]}
        cycles = dc.find_cycles(graph)
        self.assertTrue(len(cycles) > 0)


class TestFormat(unittest.TestCase):
    def test_format_graph(self):
        graph = {"a": ["b"], "b": []}
        output = dc._format_graph(graph)
        self.assertIn("a → b", output)
        self.assertIn("standalone", output)

    def test_format_mermaid(self):
        graph = {"a": ["b"], "b": []}
        output = dc._format_mermaid(graph)
        self.assertIn("graph LR", output)
        self.assertIn("a --> b", output)


if __name__ == "__main__":
    unittest.main()
