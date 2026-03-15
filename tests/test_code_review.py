#!/usr/bin/env python3
"""
Tests pour code-review.py — Revue de code automatisée Grimoire.

Fonctions testées :
  - parse_diff_stat()
  - get_diff_content()
  - check_security()
  - check_test_coverage()
  - check_complexity()
  - check_conventions()
  - check_consistency()
  - run_review()
  - format_review()
  - build_parser()
"""

import importlib
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

KIT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(KIT_DIR / "framework" / "tools"))
TOOL = KIT_DIR / "framework" / "tools" / "code-review.py"


def _import_mod():
    """Import le module code-review via importlib."""
    mod_name = "code_review"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "code-review.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Tests Dataclasses ─────────────────────────────────────────────────────────

class TestDataclasses(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_diff_file_exists(self):
        self.assertTrue(hasattr(self.mod, "DiffFile"))

    def test_diff_file_is_test(self):
        df = self.mod.DiffFile(path="tests/test_foo.py", status="M")
        self.assertTrue(df.is_test)

    def test_diff_file_not_test(self):
        df = self.mod.DiffFile(path="src/foo.py", status="M")
        self.assertFalse(df.is_test)

    def test_diff_file_ext(self):
        df = self.mod.DiffFile(path="src/foo.py", status="M")
        self.assertEqual(df.ext, ".py")

    def test_finding_exists(self):
        self.assertTrue(hasattr(self.mod, "Finding"))

    def test_review_report_exists(self):
        self.assertTrue(hasattr(self.mod, "ReviewReport"))

    def test_review_report_totals(self):
        report = self.mod.ReviewReport()
        report.diff_files.append(
            self.mod.DiffFile(path="a.py", status="M", added_lines=10, removed_lines=5))
        report.diff_files.append(
            self.mod.DiffFile(path="b.py", status="A", added_lines=20, removed_lines=0))
        self.assertEqual(report.total_added, 30)
        self.assertEqual(report.total_removed, 5)

    def test_review_report_to_dict(self):
        report = self.mod.ReviewReport()
        d = report.to_dict()
        self.assertIn("version", d)
        self.assertIn("findings", d)


# ── Tests Security Checker ───────────────────────────────────────────────────

class TestCheckSecurity(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def _make_diff(self, lines: list[str]) -> "DiffFile":  # noqa: F821
        return self.mod.DiffFile(
            path="src/foo.py", status="M",
            added_lines=len(lines),
            added_content=lines,
        )

    def test_detect_eval(self):
        df = self._make_diff(["result = eval(user_input)"])
        findings = self.mod.check_security(df)
        self.assertTrue(any(f.severity == "CRITICAL" for f in findings))

    def test_detect_exec(self):
        df = self._make_diff(["exec(code)"])
        findings = self.mod.check_security(df)
        self.assertTrue(any(f.severity == "CRITICAL" for f in findings))

    def test_detect_hardcoded_secret(self):
        df = self._make_diff(['password = "my_super_secret"'])
        findings = self.mod.check_security(df)
        self.assertTrue(any("CRITICAL" in f.severity for f in findings))

    def test_detect_shell_true(self):
        df = self._make_diff(["subprocess.run(cmd, shell=True)"])
        findings = self.mod.check_security(df)
        self.assertTrue(any(f.rule_id == "CR-SEC" for f in findings))

    def test_clean_code_no_findings(self):
        df = self._make_diff(["x = 1 + 2", "print(x)"])
        findings = self.mod.check_security(df)
        self.assertEqual(len(findings), 0)


# ── Tests Test Coverage Checker ──────────────────────────────────────────────

class TestCheckTestCoverage(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_detect_missing_test(self):
        diff_files = [
            self.mod.DiffFile(path="src/auth.py", status="M", added_lines=50),
        ]
        findings = self.mod.check_test_coverage(diff_files)
        self.assertTrue(any(f.rule_id == "CR-TEST" for f in findings))

    def test_no_finding_with_test(self):
        diff_files = [
            self.mod.DiffFile(path="src/auth.py", status="M", added_lines=50),
            self.mod.DiffFile(path="tests/test_auth.py", status="M", added_lines=30),
        ]
        findings = self.mod.check_test_coverage(diff_files)
        cr_test = [f for f in findings if f.rule_id == "CR-TEST"]
        self.assertEqual(len(cr_test), 0)

    def test_small_change_no_finding(self):
        diff_files = [
            self.mod.DiffFile(path="src/auth.py", status="M", added_lines=5),
        ]
        findings = self.mod.check_test_coverage(diff_files)
        self.assertEqual(len(findings), 0)


# ── Tests Complexity Checker ─────────────────────────────────────────────────

class TestCheckComplexity(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_detect_large_file(self):
        df = self.mod.DiffFile(
            path="src/big.py", status="M", added_lines=250,
            added_content=["x = 1"] * 250,
        )
        findings = self.mod.check_complexity(df)
        cr_size = [f for f in findings if f.rule_id == "CR-SIZE"]
        self.assertGreaterEqual(len(cr_size), 1)

    def test_small_file_no_finding(self):
        df = self.mod.DiffFile(
            path="src/small.py", status="M", added_lines=10,
            added_content=["x = 1"] * 10,
        )
        findings = self.mod.check_complexity(df)
        cr_size = [f for f in findings if f.rule_id == "CR-SIZE"]
        self.assertEqual(len(cr_size), 0)


# ── Tests Convention Checker ─────────────────────────────────────────────────

class TestCheckConventions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_detect_print_in_source(self):
        df = self.mod.DiffFile(
            path="src/foo.py", status="M", added_lines=1,
            added_content=["print('debug')"],
        )
        findings = self.mod.check_conventions(df)
        cr_print = [f for f in findings if f.rule_id == "CR-PRINT"]
        self.assertGreaterEqual(len(cr_print), 1)

    def test_no_print_finding_in_tests(self):
        df = self.mod.DiffFile(
            path="tests/test_foo.py", status="M", added_lines=1,
            added_content=["print('debug')"],
        )
        findings = self.mod.check_conventions(df)
        cr_print = [f for f in findings if f.rule_id == "CR-PRINT"]
        self.assertEqual(len(cr_print), 0)

    def test_detect_todo(self):
        df = self.mod.DiffFile(
            path="src/foo.py", status="M", added_lines=1,
            added_content=["# TODO: fix this"],
        )
        findings = self.mod.check_conventions(df)
        cr_todo = [f for f in findings if f.rule_id == "CR-TODO"]
        self.assertGreaterEqual(len(cr_todo), 1)


# ── Tests Consistency Checker ────────────────────────────────────────────────

class TestCheckConsistency(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_config_without_doc(self):
        diff_files = [
            self.mod.DiffFile(path="config.yaml", status="M", added_lines=5),
        ]
        findings = self.mod.check_consistency(diff_files)
        cr_doc = [f for f in findings if f.rule_id == "CR-DOC"]
        self.assertGreaterEqual(len(cr_doc), 1)

    def test_config_with_doc_no_finding(self):
        diff_files = [
            self.mod.DiffFile(path="config.yaml", status="M", added_lines=5),
            self.mod.DiffFile(path="README.md", status="M", added_lines=3),
        ]
        findings = self.mod.check_consistency(diff_files)
        cr_doc = [f for f in findings if f.rule_id == "CR-DOC"]
        self.assertEqual(len(cr_doc), 0)


# ── Tests Format ─────────────────────────────────────────────────────────────

class TestFormatReview(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_format_empty(self):
        report = self.mod.ReviewReport()
        output = self.mod.format_review(report)
        self.assertIn("Code Review", output)

    def test_format_with_findings(self):
        report = self.mod.ReviewReport()
        report.findings.append(self.mod.Finding(
            rule_id="CR-SEC", category="SECURITY", severity="CRITICAL",
            file="foo.py", line=10, message="eval() détecté"))
        output = self.mod.format_review(report)
        self.assertIn("CR-SEC", output)
        self.assertIn("eval()", output)

    def test_format_json(self):
        report = self.mod.ReviewReport()
        output = self.mod.format_review(report, as_json=True)
        data = json.loads(output)
        self.assertIn("total_findings", data)


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
        self.assertIn("code-review", result.stdout)

    def test_no_command(self):
        result = subprocess.run(
            [sys.executable, str(TOOL)],
            capture_output=True, text=True, timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
