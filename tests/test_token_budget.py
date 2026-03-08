#!/usr/bin/env python3
"""
Tests pour token-budget.py — Enforcement budget token Grimoire (BM-41 Story 3.3).

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


# ── Usage History (Sprint 2) ────────────────────────────────────────────────

class TestUsageHistory(unittest.TestCase):
    """Tests for token usage JSONL tracking added in v1.2.0."""

    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = tempfile.mkdtemp()
        self.project_root = Path(self.tmpdir)
        (self.project_root / "_grimoire" / "_memory").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_version_bumped(self):
        self.assertEqual(self.mod.TOKEN_BUDGET_VERSION, "1.2.0")

    def test_token_usage_log_constant(self):
        self.assertEqual(self.mod.TOKEN_USAGE_LOG, "_grimoire/_memory/token-usage.jsonl")

    def test_token_usage_max_entries_constant(self):
        self.assertEqual(self.mod.TOKEN_USAGE_MAX_ENTRIES, 1000)

    def test_log_usage_creates_file(self):
        status = self.mod.BudgetStatus(
            model="test-model", window_tokens=100000,
            used_tokens=50000, usage_pct=0.5, level="ok",
        )
        self.mod._log_usage(self.project_root, status)
        log_path = self.project_root / self.mod.TOKEN_USAGE_LOG
        self.assertTrue(log_path.exists())

    def test_log_usage_writes_valid_jsonl(self):
        status = self.mod.BudgetStatus(
            model="test-model", window_tokens=200000,
            used_tokens=80000, usage_pct=0.4, level="ok",
        )
        self.mod._log_usage(self.project_root, status)
        log_path = self.project_root / self.mod.TOKEN_USAGE_LOG
        lines = log_path.read_text().strip().splitlines()
        self.assertEqual(len(lines), 1)
        entry = json.loads(lines[0])
        self.assertIn("ts", entry)
        self.assertIn("model", entry)
        self.assertIn("used", entry)
        self.assertIn("window", entry)
        self.assertIn("pct", entry)
        self.assertIn("level", entry)
        self.assertEqual(entry["model"], "test-model")
        self.assertEqual(entry["used"], 80000)

    def test_prune_usage_log(self):
        log_path = self.project_root / self.mod.TOKEN_USAGE_LOG
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w") as f:
            for i in range(1500):
                f.write(json.dumps({"ts": f"t{i}", "pct": 0.1}) + "\n")
        self.mod._prune_usage_log(self.project_root, max_entries=1000)
        lines = log_path.read_text().strip().splitlines()
        self.assertEqual(len(lines), 1000)

    def test_prune_noop_when_under_limit(self):
        log_path = self.project_root / self.mod.TOKEN_USAGE_LOG
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w") as f:
            for i in range(50):
                f.write(json.dumps({"ts": f"t{i}", "pct": 0.1}) + "\n")
        self.mod._prune_usage_log(self.project_root, max_entries=1000)
        lines = log_path.read_text().strip().splitlines()
        self.assertEqual(len(lines), 50)

    def test_load_usage_history_empty(self):
        entries = self.mod._load_usage_history(self.project_root)
        self.assertEqual(entries, [])

    def test_load_usage_history_reads_entries(self):
        log_path = self.project_root / self.mod.TOKEN_USAGE_LOG
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w") as f:
            for i in range(5):
                f.write(json.dumps({"ts": f"t{i}", "pct": i * 0.1}) + "\n")
        entries = self.mod._load_usage_history(self.project_root, last_n=3)
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0]["ts"], "t2")

    def test_usage_trend_empty(self):
        trend = self.mod.usage_trend(self.project_root)
        self.assertEqual(trend["entries"], 0)
        self.assertEqual(trend["direction"], "→")
        self.assertIsNone(trend["latest"])

    def test_usage_trend_with_data(self):
        log_path = self.project_root / self.mod.TOKEN_USAGE_LOG
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w") as f:
            for i in range(20):
                f.write(json.dumps({"ts": f"t{i}", "pct": 0.3 + i * 0.01}) + "\n")
        trend = self.mod.usage_trend(self.project_root)
        self.assertEqual(trend["entries"], 20)
        self.assertGreater(trend["avg_pct"], 0)
        self.assertIn(trend["direction"], ["↑", "↓", "→"])
        self.assertIsNotNone(trend["latest"])

    def test_usage_trend_increasing(self):
        log_path = self.project_root / self.mod.TOKEN_USAGE_LOG
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w") as f:
            # First half: low, second half: high
            for i in range(10):
                f.write(json.dumps({"ts": f"t{i}", "pct": 0.1}) + "\n")
            for i in range(10):
                f.write(json.dumps({"ts": f"t{i+10}", "pct": 0.9}) + "\n")
        trend = self.mod.usage_trend(self.project_root)
        self.assertEqual(trend["direction"], "↑")

    def test_check_auto_logs_usage(self):
        enforcer = self.mod.TokenBudgetEnforcer(self.project_root)
        enforcer.check()
        log_path = self.project_root / self.mod.TOKEN_USAGE_LOG
        self.assertTrue(log_path.exists())

    def test_mcp_context_budget_includes_trend(self):
        result = self.mod.mcp_context_budget(str(self.project_root))
        self.assertIn("trend", result)
        self.assertIsInstance(result["trend"], dict)
        self.assertIn("entries", result["trend"])


if __name__ == "__main__":
    unittest.main()
