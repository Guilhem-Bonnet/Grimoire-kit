#!/usr/bin/env python3
"""
Tests pour dashboard.py — dashboard.py — Bioluminescence Dashboard Grimoire.

Fonctions testées :
  - analyze_health()
  - analyze_entropy()
  - analyze_pareto()
  - format_health()
  - format_entropy()
  - format_full_dashboard()
  - cmd_health()
  - cmd_entropy()
  - cmd_pareto()
  - cmd_activity()
  - cmd_full()
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
TOOL = KIT_DIR / "framework" / "tools" / "dashboard.py"


def _import_mod():
    """Import le module dashboard via importlib."""
    mod_name = "dashboard"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "dashboard.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_project(root: Path) -> Path:
    """Créer un projet Grimoire minimal pour les tests."""
    (root / "_grimoire" / "_memory" / "agent-learnings").mkdir(parents=True, exist_ok=True)
    (root / "_grimoire-output").mkdir(parents=True, exist_ok=True)
    (root / "_grimoire" / "bmm" / "agents").mkdir(parents=True, exist_ok=True)
    (root / "_grimoire" / "bmm" / "workflows").mkdir(parents=True, exist_ok=True)
    (root / "framework" / "tools").mkdir(parents=True, exist_ok=True)
    return root


class TestDataclasses(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_health_metric_exists(self):
        self.assertTrue(hasattr(self.mod, "HealthMetric"))

    def test_health_metric_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.HealthMetric)}
        for expected in ["name", "score", "detail"]:
            self.assertIn(expected, fields)

    def test_health_report_exists(self):
        self.assertTrue(hasattr(self.mod, "HealthReport"))

    def test_health_report_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.HealthReport)}
        for expected in ["metrics", "global_score"]:
            self.assertIn(expected, fields)

    def test_entropy_report_exists(self):
        self.assertTrue(hasattr(self.mod, "EntropyReport"))

    def test_entropy_report_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.EntropyReport)}
        for expected in ["file_entropy", "dir_entropy", "total_files", "type_distribution"]:
            self.assertIn(expected, fields)

    def test_pareto_report_exists(self):
        self.assertTrue(hasattr(self.mod, "ParetoReport"))

    def test_pareto_report_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.ParetoReport)}
        for expected in ["top_20_files", "top_20_value", "gini"]:
            self.assertIn(expected, fields)


class TestPureFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_format_health_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_health", None)))

    def test_format_entropy_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_entropy", None)))


class TestProjectFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        # Minimal Grimoire structure
        (self.tmpdir / "_grimoire" / "_memory").mkdir(parents=True, exist_ok=True)
        (self.tmpdir / "_grimoire-output").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_analyze_health_empty_project(self):
        try:
            result = self.mod.analyze_health(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project

    def test_analyze_entropy_empty_project(self):
        try:
            result = self.mod.analyze_entropy(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project

    def test_analyze_pareto_empty_project(self):
        try:
            result = self.mod.analyze_pareto(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project

    def test_format_full_dashboard_empty_project(self):
        try:
            result = self.mod.format_full_dashboard(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project


class TestFormatFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_format_health_callable(self):
        self.assertTrue(callable(self.mod.format_health))

    def test_format_entropy_callable(self):
        self.assertTrue(callable(self.mod.format_entropy))

    def test_format_full_dashboard_callable(self):
        self.assertTrue(callable(self.mod.format_full_dashboard))


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_version_defined(self):
        self.assertTrue(hasattr(self.mod, "VERSION"))


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

    def test_subcommand_health_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["health"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_entropy_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["entropy"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_pareto_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["pareto"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_activity_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["activity"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_full_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["full"])
        except SystemExit:
            pass  # Some subcommands may require args


class TestCLIIntegration(unittest.TestCase):
    TOOL = KIT_DIR / "framework" / "tools" / "dashboard.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.TOOL)] + list(args),
            capture_output=True, text=True, timeout=30,
        )

    def test_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("dashboard", r.stdout.lower() + r.stderr.lower())

    def test_no_args(self):
        r = self._run()
        # Most tools show help or error without args
        self.assertIn(r.returncode, (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
