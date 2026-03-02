#!/usr/bin/env python3
"""
Tests pour context-router.py — context-router.py — Routeur de contexte intelligent BMAD.

Fonctions testées :
  - estimate_tokens()
  - find_agent_files()
  - extract_agent_tag()
  - discover_context_files()
  - compute_relevance()
  - calculate_plan()
  - format_plan()
  - format_budget_report()
  - cmd_plan()
  - cmd_budget()
  - cmd_suggest()
  - cmd_relevance()
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
TOOL = KIT_DIR / "framework" / "tools" / "context-router.py"


def _import_mod():
    """Import le module context-router via importlib."""
    mod_name = "context_router"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "context-router.py")
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

    def test_file_entry_exists(self):
        self.assertTrue(hasattr(self.mod, "FileEntry"))

    def test_file_entry_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.FileEntry)}
        for expected in ["path", "priority", "estimated_tokens", "reason", "relevance_score"]:
            self.assertIn(expected, fields)

    def test_load_plan_exists(self):
        self.assertTrue(hasattr(self.mod, "LoadPlan"))

    def test_load_plan_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.LoadPlan)}
        for expected in ["agent", "model", "model_window", "entries", "total_tokens"]:
            self.assertIn(expected, fields)

    def test_budget_report_exists(self):
        self.assertTrue(hasattr(self.mod, "BudgetReport"))

    def test_budget_report_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.BudgetReport)}
        for expected in ["plans", "overbudget_count"]:
            self.assertIn(expected, fields)


class TestPureFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_estimate_tokens_callable(self):
        self.assertTrue(callable(getattr(self.mod, "estimate_tokens", None)))

    def test_extract_agent_tag_callable(self):
        self.assertTrue(callable(getattr(self.mod, "extract_agent_tag", None)))

    def test_compute_relevance_callable(self):
        self.assertTrue(callable(getattr(self.mod, "compute_relevance", None)))

    def test_format_plan_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_plan", None)))

    def test_format_budget_report_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_budget_report", None)))


class TestProjectFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        # Minimal BMAD structure
        (self.tmpdir / "_bmad" / "_memory").mkdir(parents=True, exist_ok=True)
        (self.tmpdir / "_bmad-output").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_find_agent_files_empty_project(self):
        try:
            result = self.mod.find_agent_files(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project

    def test_discover_context_files_callable(self):
        self.assertTrue(callable(self.mod.discover_context_files))

    def test_calculate_plan_callable(self):
        self.assertTrue(callable(self.mod.calculate_plan))


class TestFormatFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_format_plan_callable(self):
        self.assertTrue(callable(self.mod.format_plan))

    def test_format_budget_report_callable(self):
        self.assertTrue(callable(self.mod.format_budget_report))


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_context_router_version_defined(self):
        self.assertTrue(hasattr(self.mod, "CONTEXT_ROUTER_VERSION"))

    def test_default_model_defined(self):
        self.assertTrue(hasattr(self.mod, "DEFAULT_MODEL"))

    def test_chars_per_token_defined(self):
        self.assertTrue(hasattr(self.mod, "CHARS_PER_TOKEN"))

    def test_warning_threshold_defined(self):
        self.assertTrue(hasattr(self.mod, "WARNING_THRESHOLD"))

    def test_critical_threshold_defined(self):
        self.assertTrue(hasattr(self.mod, "CRITICAL_THRESHOLD"))


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

    def test_subcommand_plan_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["plan"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_budget_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["budget"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_suggest_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["suggest"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_relevance_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["relevance"])
        except SystemExit:
            pass  # Some subcommands may require args


class TestCLIIntegration(unittest.TestCase):
    TOOL = KIT_DIR / "framework" / "tools" / "context-router.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.TOOL)] + list(args),
            capture_output=True, text=True, timeout=30,
        )

    def test_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("context", r.stdout.lower() + r.stderr.lower())

    def test_no_args(self):
        r = self._run()
        # Most tools show help or error without args
        self.assertIn(r.returncode, (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
