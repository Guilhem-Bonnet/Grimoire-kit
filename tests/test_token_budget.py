#!/usr/bin/env python3
"""
Tests pour token-budget.py — Enforcement budget token BMAD (BM-41 Story 3.3).

Fonctions testées :
  - PriorityBucket, BudgetStatus, EnforcementAction, EnforcementReport
  - TokenBudgetEnforcer.check(), enforce()
  - mcp_context_budget()
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
TOOL = KIT_DIR / "framework" / "tools" / "token-budget.py"


def _import_mod():
    mod_name = "token_budget"
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
        self.assertTrue(hasattr(self.mod, "TOKEN_BUDGET_VERSION"))

    def test_version_format(self):
        parts = self.mod.TOKEN_BUDGET_VERSION.split(".")
        self.assertEqual(len(parts), 3)

    def test_thresholds(self):
        self.assertAlmostEqual(self.mod.WARNING_THRESHOLD, 0.60)
        self.assertAlmostEqual(self.mod.CRITICAL_THRESHOLD, 0.80)
        self.assertAlmostEqual(self.mod.EMERGENCY_THRESHOLD, 0.95)

    def test_priority_names(self):
        self.assertEqual(len(self.mod.PRIORITY_NAMES), 5)
        self.assertIn(0, self.mod.PRIORITY_NAMES)
        self.assertIn(4, self.mod.PRIORITY_NAMES)

    def test_model_windows_not_empty(self):
        self.assertGreater(len(self.mod.MODEL_WINDOWS), 5)

    def test_default_model(self):
        self.assertIn(self.mod.DEFAULT_MODEL, self.mod.MODEL_WINDOWS)

    def test_chars_per_token(self):
        self.assertEqual(self.mod.CHARS_PER_TOKEN, 4)


# ── Data Classes ────────────────────────────────────────────────────────────

class TestDataclasses(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_priority_bucket(self):
        b = self.mod.PriorityBucket(priority=2, name="P2 — Shared")
        self.assertEqual(b.files_count, 0)
        self.assertEqual(b.tokens, 0)

    def test_budget_status_defaults(self):
        s = self.mod.BudgetStatus()
        self.assertEqual(s.level, "ok")
        self.assertEqual(s.usage_pct, 0.0)

    def test_enforcement_action(self):
        a = self.mod.EnforcementAction(
            action_type="warning", target="all", detail="Test"
        )
        self.assertEqual(a.tokens_freed, 0)

    def test_enforcement_report_defaults(self):
        r = self.mod.EnforcementReport()
        self.assertEqual(r.total_tokens_freed, 0)
        self.assertEqual(r.errors, [])


# ── TokenBudgetEnforcer ───────────────────────────────────────────────────

class TestTokenBudgetEnforcer(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_init(self):
        enforcer = self.mod.TokenBudgetEnforcer(self.tmpdir)
        self.assertEqual(enforcer.model, self.mod.DEFAULT_MODEL)

    def test_get_window_default(self):
        enforcer = self.mod.TokenBudgetEnforcer(self.tmpdir)
        window = enforcer._get_window()
        self.assertGreater(window, 0)

    def test_get_window_known_model(self):
        enforcer = self.mod.TokenBudgetEnforcer(self.tmpdir, model="gpt-4o")
        window = enforcer._get_window()
        self.assertEqual(window, 128_000)

    def test_check_empty_project(self):
        enforcer = self.mod.TokenBudgetEnforcer(self.tmpdir)
        status = enforcer.check()
        self.assertEqual(status.level, "ok")
        self.assertEqual(status.used_tokens, 0)

    def test_check_returns_budget_status(self):
        enforcer = self.mod.TokenBudgetEnforcer(self.tmpdir)
        status = enforcer.check()
        self.assertIsInstance(status, self.mod.BudgetStatus)
        self.assertEqual(len(status.buckets), 5)

    def test_check_buckets_have_names(self):
        enforcer = self.mod.TokenBudgetEnforcer(self.tmpdir)
        status = enforcer.check()
        for bucket in status.buckets:
            self.assertTrue(bucket.name.startswith("P"))

    def test_check_with_files(self):
        """Create files to trigger token usage."""
        docs = self.tmpdir / "docs"
        docs.mkdir()
        (docs / "big-doc.md").write_text("A" * 10000)
        enforcer = self.mod.TokenBudgetEnforcer(self.tmpdir)
        status = enforcer.check()
        # Should detect the doc file
        self.assertIsNotNone(status)

    def test_enforce_ok_level(self):
        enforcer = self.mod.TokenBudgetEnforcer(self.tmpdir)
        report = enforcer.enforce()
        self.assertEqual(len(report.actions), 0)

    def test_enforce_dry_run(self):
        enforcer = self.mod.TokenBudgetEnforcer(self.tmpdir)
        report = enforcer.enforce(dry_run=True)
        self.assertIsNotNone(report.status_before)

    def test_enforce_returns_report(self):
        enforcer = self.mod.TokenBudgetEnforcer(self.tmpdir)
        report = enforcer.enforce()
        self.assertIsInstance(report, self.mod.EnforcementReport)

    def test_level_thresholds(self):
        # Manually test level logic
        status = self.mod.BudgetStatus(usage_pct=0.50)
        self.assertEqual(status.level, "ok")

    def test_custom_thresholds(self):
        enforcer = self.mod.TokenBudgetEnforcer(
            self.tmpdir,
            warning_threshold=0.40,
            critical_threshold=0.60,
        )
        self.assertAlmostEqual(enforcer.warning_threshold, 0.40)
        self.assertAlmostEqual(enforcer.critical_threshold, 0.60)


# ── MCP Tool Interface ──────────────────────────────────────────────────────

class TestMCPInterface(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_mcp_context_budget_returns_dict(self):
        result = self.mod.mcp_context_budget(str(self.tmpdir))
        self.assertIsInstance(result, dict)

    def test_mcp_context_budget_has_fields(self):
        result = self.mod.mcp_context_budget(str(self.tmpdir))
        self.assertIn("level", result)
        self.assertIn("used_tokens", result)
        self.assertIn("window_tokens", result)

    def test_mcp_context_budget_with_model(self):
        result = self.mod.mcp_context_budget(str(self.tmpdir), model="gpt-4o")
        self.assertEqual(result["model"], "gpt-4o")

    def test_mcp_context_budget_serializable(self):
        result = self.mod.mcp_context_budget(str(self.tmpdir))
        serialized = json.dumps(result)
        self.assertIsInstance(serialized, str)


# ── Config Loading ──────────────────────────────────────────────────────────

class TestConfigLoading(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_config_no_file(self):
        config = self.mod.load_budget_config(self.tmpdir)
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
        self.assertIn("Token Budget", r.stdout)

    def test_version(self):
        r = self._run("--version")
        self.assertEqual(r.returncode, 0)
        self.assertIn("token-budget", r.stdout)

    def test_check_command(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            r = self._run("--project-root", tmpdir, "check")
            self.assertEqual(r.returncode, 0)
            self.assertIn("Token Budget", r.stdout)

    def test_enforce_dry_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            r = self._run("--project-root", tmpdir, "enforce", "--dry-run")
            self.assertEqual(r.returncode, 0)

    def test_report_command(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            r = self._run("--project-root", tmpdir, "report")
            self.assertEqual(r.returncode, 0)

    def test_report_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            r = self._run("--project-root", tmpdir, "report", "--json")
            self.assertEqual(r.returncode, 0)
            data = json.loads(r.stdout)
            self.assertIn("level", data)

    def test_no_command_shows_help(self):
        r = self._run()
        self.assertIn(r.returncode, (0, 1))


if __name__ == "__main__":
    unittest.main()
