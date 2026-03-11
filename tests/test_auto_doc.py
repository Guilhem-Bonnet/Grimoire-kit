#!/usr/bin/env python3
"""Tests pour auto-doc.py — Synchronisation README automatique."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

KIT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(KIT_DIR / "framework" / "tools"))


def _import():
    import importlib
    return importlib.import_module("auto-doc")


def _write(root, relpath, content):
    p = root / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


# ── Introspection ─────────────────────────────────────────────────────────────

class TestIntrospection(unittest.TestCase):
    def setUp(self):
        self.mod = _import()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_count_tests_empty(self):
        tests, files = self.mod.count_tests(self.tmpdir)
        self.assertEqual(tests, 0)
        self.assertEqual(files, 0)

    def test_count_tests_per_file(self):
        _write(self.tmpdir, "tests/test_a.py", """\
import unittest
class T(unittest.TestCase):
    def test_one(self): pass
    def test_two(self): pass
""")
        _write(self.tmpdir, "tests/test_b.py", """\
import unittest
class T(unittest.TestCase):
    def test_three(self): pass
""")
        result = self.mod.count_tests_per_file(self.tmpdir)
        self.assertEqual(result["test_a.py"], 2)
        self.assertEqual(result["test_b.py"], 1)

    def test_list_tools_empty(self):
        tools = self.mod.list_tools(self.tmpdir)
        self.assertEqual(tools, [])

    def test_list_tools_real(self):
        tools = self.mod.list_tools(KIT_DIR)
        self.assertIn("dream", tools)
        self.assertIn("nso", tools)
        self.assertIn("auto-doc", tools)

    def test_get_tool_for_test_known(self):
        self.assertEqual(self.mod.get_tool_for_test("test_dream.py"), "Dream Mode")
        self.assertEqual(self.mod.get_tool_for_test("test_nso.py"), "NSO Orchestrator")

    def test_get_tool_for_test_unknown(self):
        result = self.mod.get_tool_for_test("test_unknown_tool.py")
        self.assertIn("Unknown", result)


# ── Drift detection ──────────────────────────────────────────────────────────

class TestDriftDetection(unittest.TestCase):
    def setUp(self):
        self.mod = _import()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_readme(self):
        report = self.mod.detect_drifts(self.tmpdir)
        self.assertEqual(report.drift_count, 0)

    def test_no_drifts(self):
        _write(self.tmpdir, "tests/test_a.py", """\
import unittest
class T(unittest.TestCase):
    def test_one(self): pass
""")
        _write(self.tmpdir, "README.md", """\
# Test
Suite de 1 tests.
| `test_a.py` | Outil | 1 |
""")
        report = self.mod.detect_drifts(self.tmpdir)
        # No count drift (1 test == 1 test)
        count_drifts = [d for d in report.drifts if d.section == "Test count"]
        self.assertEqual(len(count_drifts), 0)

    def test_detect_count_drift(self):
        _write(self.tmpdir, "tests/test_a.py", """\
import unittest
class T(unittest.TestCase):
    def test_one(self): pass
    def test_two(self): pass
    def test_three(self): pass
""")
        _write(self.tmpdir, "README.md", """\
# Test
Suite de 1 tests.
""")
        report = self.mod.detect_drifts(self.tmpdir)
        count_drifts = [d for d in report.drifts if d.section == "Test count"]
        self.assertGreaterEqual(len(count_drifts), 1)
        self.assertIn("3", count_drifts[0].expected)

    def test_detect_table_drift(self):
        _write(self.tmpdir, "tests/test_a.py", """\
import unittest
class T(unittest.TestCase):
    def test_one(self): pass
    def test_two(self): pass
""")
        _write(self.tmpdir, "README.md", """\
| `test_a.py` | Outil | 99 |
""")
        report = self.mod.detect_drifts(self.tmpdir)
        table_drifts = [d for d in report.drifts if "test_a.py" in d.section]
        self.assertGreaterEqual(len(table_drifts), 1)

    def test_detect_missing_test_file(self):
        """When README has a per-file table, missing files are reported."""
        _write(self.tmpdir, "tests/test_a.py", "def test_x(): pass\n")
        _write(self.tmpdir, "tests/test_b.py", "def test_y(): pass\n")
        _write(self.tmpdir, "README.md", "| `test_a.py` | Tool A | 1 |\n")
        report = self.mod.detect_drifts(self.tmpdir)
        missing = [d for d in report.drifts if "missing" in d.section]
        self.assertGreaterEqual(len(missing), 1)

    def test_no_missing_when_category_table(self):
        """When README uses category table (no per-file rows), no missing entry drift."""
        _write(self.tmpdir, "tests/test_a.py", "def test_x(): pass\n")
        _write(self.tmpdir, "README.md", "# Test\n| Catégorie | Tests |\n| Total | 1 |\n")
        report = self.mod.detect_drifts(self.tmpdir)
        missing = [d for d in report.drifts if "missing" in d.section]
        self.assertEqual(len(missing), 0)

    def test_detect_real_drifts(self):
        """Le vrai README a des drifts connus (737 vs 933+)."""
        report = self.mod.detect_drifts(KIT_DIR)
        self.assertGreater(report.drift_count, 0)


# ── Sync ──────────────────────────────────────────────────────────────────────

class TestSync(unittest.TestCase):
    def setUp(self):
        self.mod = _import()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_sync_fixes_count(self):
        _write(self.tmpdir, "tests/test_a.py", """\
import unittest
class T(unittest.TestCase):
    def test_one(self): pass
    def test_two(self): pass
    def test_three(self): pass
""")
        _write(self.tmpdir, "README.md", "Suite de 1 tests.\n")
        _report, changes = self.mod.sync_readme(self.tmpdir)
        self.assertGreaterEqual(changes, 1)
        text = (self.tmpdir / "README.md").read_text()
        self.assertIn("3", text)

    def test_sync_no_readme(self):
        _report, changes = self.mod.sync_readme(self.tmpdir)
        self.assertEqual(changes, 0)

    def test_sync_adds_missing_rows(self):
        _write(self.tmpdir, "tests/test_x.py", """\
def test_a(): pass
def test_b(): pass
""")
        _write(self.tmpdir, "README.md", """\
# Tests
| `test_existing.py` | Tool | 5 |
""")
        _report, changes = self.mod.sync_readme(self.tmpdir)
        self.assertGreaterEqual(changes, 1)
        text = (self.tmpdir / "README.md").read_text()
        self.assertIn("test_x.py", text)


# ── Rendu ─────────────────────────────────────────────────────────────────────

class TestRendering(unittest.TestCase):
    def setUp(self):
        self.mod = _import()

    def test_render_clean(self):
        report = self.mod.DocReport(readme_path="README.md")
        text = self.mod.render_report(report)
        self.assertIn("✅", text)

    def test_render_with_drifts(self):
        report = self.mod.DocReport(
            readme_path="README.md",
            drifts=[self.mod.DriftItem(
                section="Test count",
                current="100",
                expected="200",
                line=10,
            )],
        )
        text = self.mod.render_report(report)
        self.assertIn("Test count", text)
        self.assertIn("200", text)

    def test_report_to_dict(self):
        report = self.mod.DocReport(readme_path="README.md")
        d = self.mod.report_to_dict(report)
        self.assertEqual(d["drift_count"], 0)
        self.assertEqual(d["readme"], "README.md")

    def test_data_class_properties(self):
        report = self.mod.DocReport(
            drifts=[
                self.mod.DriftItem(section="a", current="x", expected="y", auto_fixable=True),
                self.mod.DriftItem(section="b", current="x", expected="y", auto_fixable=False),
            ],
        )
        self.assertEqual(report.drift_count, 2)
        self.assertEqual(report.fixable_count, 1)


if __name__ == "__main__":
    unittest.main()
