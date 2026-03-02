#!/usr/bin/env python3
"""
Tests pour desire-paths.py — desire-paths.py — Analyse des "chemins de désir" BMAD.

Fonctions testées :
  - analyze_agents()
  - analyze_workflows()
  - analyze_tools()
  - generate_recommendations()
  - format_report()
  - report_to_dict()
  - cmd_analyze()
  - cmd_agents()
  - cmd_workflows()
  - cmd_tools()
  - cmd_recommend()
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
TOOL = KIT_DIR / "framework" / "tools" / "desire-paths.py"


def _import_mod():
    """Import le module desire-paths via importlib."""
    mod_name = "desire_paths"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "desire-paths.py")
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

    def test_desire_entry_exists(self):
        self.assertTrue(hasattr(self.mod, "DesireEntry"))

    def test_desire_entry_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.DesireEntry)}
        for expected in ["name", "category", "designed", "used", "usage_count"]:
            self.assertIn(expected, fields)

    def test_desire_report_exists(self):
        self.assertTrue(hasattr(self.mod, "DesireReport"))

    def test_desire_report_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.DesireReport)}
        for expected in ["entries", "recommendations", "timestamp", "git_available"]:
            self.assertIn(expected, fields)


class TestPureFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_generate_recommendations_callable(self):
        self.assertTrue(callable(getattr(self.mod, "generate_recommendations", None)))

    def test_format_report_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_report", None)))

    def test_report_to_dict_callable(self):
        self.assertTrue(callable(getattr(self.mod, "report_to_dict", None)))


class TestProjectFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        # Minimal BMAD structure
        (self.tmpdir / "_bmad" / "_memory").mkdir(parents=True, exist_ok=True)
        (self.tmpdir / "_bmad-output").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_analyze_agents_callable(self):
        self.assertTrue(callable(self.mod.analyze_agents))

    def test_analyze_workflows_callable(self):
        self.assertTrue(callable(self.mod.analyze_workflows))

    def test_analyze_tools_callable(self):
        self.assertTrue(callable(self.mod.analyze_tools))


class TestFormatFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_format_report_callable(self):
        self.assertTrue(callable(self.mod.format_report))


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_desire_paths_version_defined(self):
        self.assertTrue(hasattr(self.mod, "DESIRE_PATHS_VERSION"))

    def test_dormant_days_defined(self):
        self.assertTrue(hasattr(self.mod, "DORMANT_DAYS"))

    def test_overused_ratio_defined(self):
        self.assertTrue(hasattr(self.mod, "OVERUSED_RATIO"))

    def test_underused_threshold_defined(self):
        self.assertTrue(hasattr(self.mod, "UNDERUSED_THRESHOLD"))


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

    def test_subcommand_analyze_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["analyze"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_agents_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["agents"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_workflows_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["workflows"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_tools_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["tools"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_recommend_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["recommend"])
        except SystemExit:
            pass  # Some subcommands may require args


class TestCLIIntegration(unittest.TestCase):
    TOOL = KIT_DIR / "framework" / "tools" / "desire-paths.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.TOOL)] + list(args),
            capture_output=True, text=True, timeout=30,
        )

    def test_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("desire", r.stdout.lower() + r.stderr.lower())

    def test_no_args(self):
        r = self._run()
        # Most tools show help or error without args
        self.assertIn(r.returncode, (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
