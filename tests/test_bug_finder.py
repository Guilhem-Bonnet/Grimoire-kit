#!/usr/bin/env python3
"""
Tests pour bug-finder.py — Détecteur de bugs logiques BMAD.

Fonctions testées :
  - scan_file()
  - scan_directory()
  - scan_git_diff()
  - PythonBugVisitor
  - _check_secrets()
  - _check_todo_fixme()
  - format_report()
  - build_parser()
"""

import importlib
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

KIT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(KIT_DIR / "framework" / "tools"))
TOOL = KIT_DIR / "framework" / "tools" / "bug-finder.py"


def _import_mod():
    """Import le module bug-finder via importlib."""
    mod_name = "bug_finder"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "bug-finder.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_project(root: Path) -> Path:
    """Créer un projet BMAD minimal pour les tests."""
    (root / "_bmad" / "_memory").mkdir(parents=True, exist_ok=True)
    (root / "_bmad-output").mkdir(parents=True, exist_ok=True)
    (root / "src").mkdir(parents=True, exist_ok=True)
    return root


def _write_py(root: Path, name: str, content: str) -> Path:
    """Écrire un fichier Python de test."""
    filepath = root / "src" / name
    filepath.write_text(textwrap.dedent(content), encoding="utf-8")
    return filepath


# ── Tests Dataclasses ─────────────────────────────────────────────────────────

class TestDataclasses(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_bug_exists(self):
        self.assertTrue(hasattr(self.mod, "Bug"))

    def test_bug_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.Bug)}
        for expected in ["rule_id", "category", "severity", "file", "line", "message"]:
            self.assertIn(expected, fields)

    def test_scan_report_exists(self):
        self.assertTrue(hasattr(self.mod, "ScanReport"))

    def test_scan_report_total(self):
        report = self.mod.ScanReport()
        self.assertEqual(report.total, 0)
        report.bugs.append(self.mod.Bug(
            rule_id="X", category="X", severity="LOW",
            file="x.py", line=1, col=0, message="test"))
        self.assertEqual(report.total, 1)

    def test_scan_report_by_severity(self):
        report = self.mod.ScanReport()
        report.bugs.append(self.mod.Bug(
            rule_id="X", category="X", severity="HIGH",
            file="x.py", line=1, col=0, message="test"))
        report.bugs.append(self.mod.Bug(
            rule_id="X", category="X", severity="HIGH",
            file="x.py", line=2, col=0, message="test2"))
        report.bugs.append(self.mod.Bug(
            rule_id="X", category="X", severity="LOW",
            file="x.py", line=3, col=0, message="test3"))
        self.assertEqual(report.by_severity, {"HIGH": 2, "LOW": 1})


# ── Tests AST Analyzer ───────────────────────────────────────────────────────

class TestMutableDefaults(unittest.TestCase):
    """BF-001: Mutable default arguments."""

    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        _make_project(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_detect_list_default(self):
        fp = _write_py(self.tmpdir, "bad.py", """\
            def foo(items=[]):
                items.append(1)
                return items
        """)
        bugs = self.mod.scan_file(fp)
        bf001 = [b for b in bugs if b.rule_id == "BF-001"]
        self.assertGreaterEqual(len(bf001), 1)

    def test_detect_dict_default(self):
        fp = _write_py(self.tmpdir, "bad2.py", """\
            def bar(config={}):
                return config
        """)
        bugs = self.mod.scan_file(fp)
        bf001 = [b for b in bugs if b.rule_id == "BF-001"]
        self.assertGreaterEqual(len(bf001), 1)

    def test_safe_default_none(self):
        fp = _write_py(self.tmpdir, "good.py", """\
            def foo(items=None):
                if items is None:
                    items = []
                return items
        """)
        bugs = self.mod.scan_file(fp)
        bf001 = [b for b in bugs if b.rule_id == "BF-001"]
        self.assertEqual(len(bf001), 0)


class TestUnreachableCode(unittest.TestCase):
    """BF-003: Dead code after return/break/continue."""

    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        _make_project(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_detect_dead_code_after_return(self):
        fp = _write_py(self.tmpdir, "dead.py", """\
            def foo():
                return 42
                x = 1
                print(x)
        """)
        bugs = self.mod.scan_file(fp)
        bf003 = [b for b in bugs if b.rule_id == "BF-003"]
        self.assertGreaterEqual(len(bf003), 1)

    def test_no_false_positive_normal_return(self):
        fp = _write_py(self.tmpdir, "good.py", """\
            def foo():
                x = 1
                return x
        """)
        bugs = self.mod.scan_file(fp)
        bf003 = [b for b in bugs if b.rule_id == "BF-003"]
        self.assertEqual(len(bf003), 0)


class TestComparePatterns(unittest.TestCase):
    """BF-004 / BF-005: Comparison patterns."""

    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        _make_project(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_detect_eq_none(self):
        fp = _write_py(self.tmpdir, "cmp.py", """\
            x = None
            if x == None:
                pass
        """)
        bugs = self.mod.scan_file(fp)
        bf004 = [b for b in bugs if b.rule_id == "BF-004"]
        self.assertGreaterEqual(len(bf004), 1)

    def test_detect_eq_true(self):
        fp = _write_py(self.tmpdir, "cmp2.py", """\
            x = True
            if x == True:
                pass
        """)
        bugs = self.mod.scan_file(fp)
        bf005 = [b for b in bugs if b.rule_id == "BF-005"]
        self.assertGreaterEqual(len(bf005), 1)


class TestSafetyChecks(unittest.TestCase):
    """BF-006/007/008/009: Safety checks."""

    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        _make_project(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_bare_except(self):
        fp = _write_py(self.tmpdir, "bare.py", """\
            try:
                pass
            except:
                pass
        """)
        bugs = self.mod.scan_file(fp)
        bf006 = [b for b in bugs if b.rule_id == "BF-006"]
        self.assertGreaterEqual(len(bf006), 1)

    def test_except_pass(self):
        fp = _write_py(self.tmpdir, "swallow.py", """\
            try:
                x = 1 / 0
            except ZeroDivisionError:
                pass
        """)
        bugs = self.mod.scan_file(fp)
        bf007 = [b for b in bugs if b.rule_id == "BF-007"]
        self.assertGreaterEqual(len(bf007), 1)

    def test_eval_call(self):
        fp = _write_py(self.tmpdir, "danger.py", """\
            result = eval("1 + 2")
        """)
        bugs = self.mod.scan_file(fp)
        bf008 = [b for b in bugs if b.rule_id == "BF-008"]
        self.assertGreaterEqual(len(bf008), 1)

    def test_open_without_with(self):
        fp = _write_py(self.tmpdir, "noclose.py", """\
            f = open("data.txt")
            data = f.read()
        """)
        bugs = self.mod.scan_file(fp)
        bf009 = [b for b in bugs if b.rule_id == "BF-009"]
        self.assertGreaterEqual(len(bf009), 1)


class TestSecretDetection(unittest.TestCase):
    """BF-013: Hardcoded secrets."""

    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        _make_project(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_detect_hardcoded_password(self):
        fp = _write_py(self.tmpdir, "secret.py", """\
            password = "super_secret_password_123"
        """)
        bugs = self.mod.scan_file(fp)
        bf013 = [b for b in bugs if b.rule_id == "BF-013"]
        self.assertGreaterEqual(len(bf013), 1)

    def test_no_false_positive_env(self):
        fp = _write_py(self.tmpdir, "env.py", """\
            import os
            password = os.environ.get("PASSWORD")
        """)
        bugs = self.mod.scan_file(fp)
        bf013 = [b for b in bugs if b.rule_id == "BF-013"]
        self.assertEqual(len(bf013), 0)


class TestDuplicateDictKeys(unittest.TestCase):
    """BF-011: Duplicate dict keys."""

    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        _make_project(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_detect_dup_keys(self):
        fp = _write_py(self.tmpdir, "dup.py", """\
            d = {"a": 1, "b": 2, "a": 3}
        """)
        bugs = self.mod.scan_file(fp)
        bf011 = [b for b in bugs if b.rule_id == "BF-011"]
        self.assertGreaterEqual(len(bf011), 1)


class TestNesting(unittest.TestCase):
    """BF-010: Excessive nesting."""

    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        _make_project(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_detect_deep_nesting(self):
        fp = _write_py(self.tmpdir, "deep.py", """\
            def too_deep():
                if True:
                    for i in range(10):
                        if i > 0:
                            while i > 0:
                                try:
                                    if i == 5:
                                        print(i)
                                except Exception:
                                    pass
                                i -= 1
        """)
        bugs = self.mod.scan_file(fp)
        bf010 = [b for b in bugs if b.rule_id == "BF-010"]
        self.assertGreaterEqual(len(bf010), 1)


class TestTodoFixme(unittest.TestCase):
    """BF-014: TODO/FIXME markers."""

    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        _make_project(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_detect_todo(self):
        fp = _write_py(self.tmpdir, "todo.py", """\
            def foo():
                # TODO: fix this later
                pass
        """)
        bugs = self.mod.scan_file(fp)
        bf014 = [b for b in bugs if b.rule_id == "BF-014"]
        self.assertGreaterEqual(len(bf014), 1)


# ── Tests scan_directory ──────────────────────────────────────────────────────

class TestScanDirectory(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        _make_project(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_scan_empty_dir(self):
        report = self.mod.scan_directory(self.tmpdir)
        self.assertEqual(report.total, 0)

    def test_scan_with_bugs(self):
        _write_py(self.tmpdir, "bad.py", """\
            def foo(items=[]):
                return items
        """)
        report = self.mod.scan_directory(self.tmpdir, self.tmpdir / "src")
        self.assertGreater(report.files_scanned, 0)
        self.assertGreater(report.total, 0)

    def test_scan_severity_filter(self):
        _write_py(self.tmpdir, "mixed.py", """\
            def foo(items=[]):
                # TODO: fix
                x = 1
                return items
        """)
        report_all = self.mod.scan_directory(self.tmpdir, self.tmpdir / "src")
        report_high = self.mod.scan_directory(self.tmpdir, self.tmpdir / "src", "high")
        # HIGH filter should exclude LOW bugs (TODO)
        self.assertGreaterEqual(report_all.total, report_high.total)


# ── Tests format_report ──────────────────────────────────────────────────────

class TestFormatReport(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_format_empty(self):
        report = self.mod.ScanReport(files_scanned=3)
        output = self.mod.format_report(report)
        self.assertIn("Aucun bug", output)

    def test_format_with_bugs(self):
        report = self.mod.ScanReport(files_scanned=1)
        report.bugs.append(self.mod.Bug(
            rule_id="BF-001", category="SAFETY", severity="HIGH",
            file="test.py", line=5, col=0, message="Mutable default"))
        output = self.mod.format_report(report)
        self.assertIn("BF-001", output)
        self.assertIn("Mutable default", output)

    def test_format_json(self):
        import json
        report = self.mod.ScanReport(files_scanned=1)
        output = self.mod.format_report(report, as_json=True)
        data = json.loads(output)
        self.assertIn("total_bugs", data)
        self.assertEqual(data["total_bugs"], 0)


# ── Tests CLI ─────────────────────────────────────────────────────────────────

class TestCli(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_build_parser(self):
        parser = self.mod.build_parser()
        self.assertIsNotNone(parser)

    def test_version(self):
        result = subprocess.run(
            [sys.executable, str(TOOL), "--version"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertIn("bug-finder", result.stdout)

    def test_no_command(self):
        result = subprocess.run(
            [sys.executable, str(TOOL)],
            capture_output=True, text=True, timeout=10,
        )
        # Should print help and exit with 1
        self.assertNotEqual(result.returncode, 0)

    def test_scan_nonexistent(self):
        result = subprocess.run(
            [sys.executable, str(TOOL), "--project-root", "/nonexistent",
             "scan", "/nonexistent"],
            capture_output=True, text=True, timeout=10,
        )
        # Should handle gracefully
        self.assertIn("0", result.stdout + result.stderr + str(result.returncode))


if __name__ == "__main__":
    unittest.main()
