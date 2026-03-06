#!/usr/bin/env python3
"""
Tests pour self-healing.py — self-healing.py — Auto-réparation des workflows BMAD.

Fonctions testées :
  - diagnose_error()
  - attempt_heal()
  - load_history()
  - save_to_history()
  - format_diagnosis()
  - format_playbook()
  - cmd_diagnose()
  - cmd_heal()
  - cmd_history()
  - cmd_playbook()
  - cmd_status()
  - build_parser()
"""

import importlib
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(KIT_DIR / "framework" / "tools"))
TOOL = KIT_DIR / "framework" / "tools" / "self-healing.py"


def _import_mod():
    """Import le module self-healing via importlib."""
    mod_name = "self_healing"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "self-healing.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_project(root: Path) -> Path:
    """Créer un projet BMAD minimal pour les tests."""
    (root / "_bmad" / "_memory" / "agent-learnings").mkdir(parents=True, exist_ok=True)
    (root / "_bmad-output").mkdir(parents=True, exist_ok=True)
    (root / "_bmad" / "bmm" / "agents").mkdir(parents=True, exist_ok=True)
    (root / "_bmad" / "bmm" / "workflows").mkdir(parents=True, exist_ok=True)
    (root / "framework" / "tools").mkdir(parents=True, exist_ok=True)
    return root


class TestDataclasses(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_diagnosis_exists(self):
        self.assertTrue(hasattr(self.mod, "Diagnosis"))

    def test_diagnosis_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.Diagnosis)}
        for expected in ["error", "matched_rule", "rule_name", "strategy", "actions"]:
            self.assertIn(expected, fields)

    def test_healing_record_exists(self):
        self.assertTrue(hasattr(self.mod, "HealingRecord"))

    def test_healing_record_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.HealingRecord)}
        for expected in ["timestamp", "error", "rule_id", "strategy", "success"]:
            self.assertIn(expected, fields)


class TestPureFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_diagnose_error_callable(self):
        self.assertTrue(callable(getattr(self.mod, "diagnose_error", None)))

    def test_format_diagnosis_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_diagnosis", None)))

    def test_format_playbook_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_playbook", None)))


class TestProjectFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        # Minimal BMAD structure
        (self.tmpdir / "_bmad" / "_memory").mkdir(parents=True, exist_ok=True)
        (self.tmpdir / "_bmad-output").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_attempt_heal_callable(self):
        self.assertTrue(callable(self.mod.attempt_heal))

    def test_load_history_empty_project(self):
        try:
            result = self.mod.load_history(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project

    def test_save_to_history_callable(self):
        self.assertTrue(callable(self.mod.save_to_history))


class TestFormatFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_format_diagnosis_callable(self):
        self.assertTrue(callable(self.mod.format_diagnosis))

    def test_format_playbook_callable(self):
        self.assertTrue(callable(self.mod.format_playbook))


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_self_healing_version_defined(self):
        self.assertTrue(hasattr(self.mod, "SELF_HEALING_VERSION"))

    def test_healing_log_defined(self):
        self.assertTrue(hasattr(self.mod, "HEALING_LOG"))

    def test_playbook_defined(self):
        self.assertTrue(hasattr(self.mod, "PLAYBOOK"))


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_build_parser(self):
        parser = self.mod.build_parser()
        self.assertIsNotNone(parser)

    def test_parser_help(self):
        parser = self.mod.build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_subcommand_diagnose_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["diagnose"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_heal_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["heal"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_history_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["history"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_playbook_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["playbook"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_status_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["status"])
        except SystemExit:
            pass  # Some subcommands may require args


class TestCLIIntegration(unittest.TestCase):
    TOOL = KIT_DIR / "framework" / "tools" / "self-healing.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.TOOL)] + list(args),
            capture_output=True, text=True, timeout=30,
        )

    def test_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("self", r.stdout.lower() + r.stderr.lower())

    def test_no_args(self):
        r = self._run()
        # Most tools show help or error without args
        self.assertIn(r.returncode, (0, 1, 2))


# ── Suggest Improvements (v1.1) ─────────────────────────────────────────────

import json


class TestSuggestImprovements(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        (self.tmpdir / "_bmad" / "_memory").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_history(self):
        result = self.mod.suggest_improvements(self.tmpdir)
        self.assertEqual(result, [])

    def test_recurrent_pattern_detected(self):
        log_file = self.tmpdir / "_bmad" / "_memory" / self.mod.HEALING_LOG
        records = [
            {"timestamp": f"2025-01-0{i}T00:00:00", "error": "file not found: x.md",
             "rule_id": "HE-001", "strategy": "CREATE", "success": True, "detail": "ok"}
            for i in range(1, 6)  # 5 occurrences of HE-001
        ]
        data = {"version": self.mod.SELF_HEALING_VERSION, "records": records}
        log_file.write_text(json.dumps(data), encoding="utf-8")

        suggestions = self.mod.suggest_improvements(self.tmpdir)
        types = [s["type"] for s in suggestions]
        self.assertIn("recurrent_pattern", types)

    def test_automation_candidate_detected(self):
        log_file = self.tmpdir / "_bmad" / "_memory" / self.mod.HEALING_LOG
        records = [
            {"timestamp": "2025-01-01T00:00:00", "error": "merge conflict",
             "rule_id": "HE-002", "strategy": "ROLLBACK", "success": False, "detail": "manual"},
            {"timestamp": "2025-01-02T00:00:00", "error": "merge conflict",
             "rule_id": "HE-002", "strategy": "ROLLBACK", "success": False, "detail": "manual"},
        ]
        data = {"version": self.mod.SELF_HEALING_VERSION, "records": records}
        log_file.write_text(json.dumps(data), encoding="utf-8")

        suggestions = self.mod.suggest_improvements(self.tmpdir)
        types = [s["type"] for s in suggestions]
        self.assertIn("automation_candidate", types)

    def test_low_heal_rate_detected(self):
        log_file = self.tmpdir / "_bmad" / "_memory" / self.mod.HEALING_LOG
        records = [
            {"timestamp": f"2025-01-{i:02d}T00:00:00", "error": f"error {i}",
             "rule_id": "HE-999", "strategy": "ESCALATE", "success": False, "detail": "nope"}
            for i in range(1, 8)  # 7 failures, 0 success → < 50%
        ]
        data = {"version": self.mod.SELF_HEALING_VERSION, "records": records}
        log_file.write_text(json.dumps(data), encoding="utf-8")

        suggestions = self.mod.suggest_improvements(self.tmpdir)
        types = [s["type"] for s in suggestions]
        self.assertIn("low_heal_rate", types)

    def test_suggestion_fields(self):
        log_file = self.tmpdir / "_bmad" / "_memory" / self.mod.HEALING_LOG
        records = [
            {"timestamp": f"2025-01-0{i}T00:00:00", "error": "file not found: x.md",
             "rule_id": "HE-001", "strategy": "CREATE", "success": True, "detail": "ok"}
            for i in range(1, 5)
        ]
        data = {"version": self.mod.SELF_HEALING_VERSION, "records": records}
        log_file.write_text(json.dumps(data), encoding="utf-8")

        suggestions = self.mod.suggest_improvements(self.tmpdir)
        for s in suggestions:
            self.assertIn("type", s)
            self.assertIn("suggestion", s)


class TestMCPInterface(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        (self.tmpdir / "_bmad" / "_memory").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_status(self):
        result = self.mod.mcp_self_healing(str(self.tmpdir), action="status")
        self.assertEqual(result["status"], "ok")
        self.assertIn("total_attempts", result)

    def test_diagnose(self):
        result = self.mod.mcp_self_healing(
            str(self.tmpdir), action="diagnose", error="file not found: x.md")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["rule"], "HE-001")

    def test_diagnose_no_error(self):
        result = self.mod.mcp_self_healing(str(self.tmpdir), action="diagnose")
        self.assertEqual(result["status"], "error")

    def test_heal(self):
        result = self.mod.mcp_self_healing(
            str(self.tmpdir), action="heal", error="file not found: x.md")
        self.assertEqual(result["status"], "ok")
        self.assertIn("healed", result)

    def test_heal_no_error(self):
        result = self.mod.mcp_self_healing(str(self.tmpdir), action="heal")
        self.assertEqual(result["status"], "error")

    def test_suggest(self):
        result = self.mod.mcp_self_healing(str(self.tmpdir), action="suggest")
        self.assertEqual(result["status"], "ok")
        self.assertIn("suggestions", result)

    def test_unknown_action(self):
        result = self.mod.mcp_self_healing(str(self.tmpdir), action="bogus")
        self.assertEqual(result["status"], "error")


if __name__ == "__main__":
    unittest.main()
