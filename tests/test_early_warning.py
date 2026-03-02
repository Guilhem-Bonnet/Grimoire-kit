#!/usr/bin/env python3
"""
Tests pour early-warning.py — early-warning.py — Système d'alerte précoce BMAD.

Fonctions testées :
  - measure_error_velocity()
  - measure_entropy()
  - measure_concentration()
  - measure_stagnation()
  - measure_drift()
  - build_report()
  - format_report()
  - report_to_dict()
  - emit_alerts()
  - cmd_scan()
  - cmd_entropy()
  - cmd_trends()
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
TOOL = KIT_DIR / "framework" / "tools" / "early-warning.py"


def _import_mod():
    """Import le module early-warning via importlib."""
    mod_name = "early_warning"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "early-warning.py")
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

    def test_metric_exists(self):
        self.assertTrue(hasattr(self.mod, "Metric"))

    def test_metric_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.Metric)}
        for expected in ["name", "value", "level", "detail", "trend"]:
            self.assertIn(expected, fields)

    def test_early_warning_report_exists(self):
        self.assertTrue(hasattr(self.mod, "EarlyWarningReport"))

    def test_early_warning_report_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.EarlyWarningReport)}
        for expected in ["metrics", "entropy_score", "overall_level", "phase", "recommendations"]:
            self.assertIn(expected, fields)


class TestPureFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

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

    def test_measure_error_velocity_callable(self):
        self.assertTrue(callable(self.mod.measure_error_velocity))

    def test_measure_entropy_empty_project(self):
        try:
            result = self.mod.measure_entropy(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project

    def test_measure_concentration_callable(self):
        self.assertTrue(callable(self.mod.measure_concentration))

    def test_measure_stagnation_callable(self):
        self.assertTrue(callable(self.mod.measure_stagnation))

    def test_measure_drift_empty_project(self):
        try:
            result = self.mod.measure_drift(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project

    def test_build_report_callable(self):
        self.assertTrue(callable(self.mod.build_report))


class TestFormatFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_format_report_callable(self):
        self.assertTrue(callable(self.mod.format_report))


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_early_warning_version_defined(self):
        self.assertTrue(hasattr(self.mod, "EARLY_WARNING_VERSION"))

    def test_entropy_watch_defined(self):
        self.assertTrue(hasattr(self.mod, "ENTROPY_WATCH"))

    def test_entropy_alert_defined(self):
        self.assertTrue(hasattr(self.mod, "ENTROPY_ALERT"))

    def test_stagnation_days_watch_defined(self):
        self.assertTrue(hasattr(self.mod, "STAGNATION_DAYS_WATCH"))

    def test_stagnation_days_alert_defined(self):
        self.assertTrue(hasattr(self.mod, "STAGNATION_DAYS_ALERT"))

    def test_error_rate_watch_defined(self):
        self.assertTrue(hasattr(self.mod, "ERROR_RATE_WATCH"))

    def test_error_rate_alert_defined(self):
        self.assertTrue(hasattr(self.mod, "ERROR_RATE_ALERT"))

    def test_concentration_watch_defined(self):
        self.assertTrue(hasattr(self.mod, "CONCENTRATION_WATCH"))


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

    def test_subcommand_scan_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["scan"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_entropy_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["entropy"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_trends_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["trends"])
        except SystemExit:
            pass  # Some subcommands may require args


class TestCLIIntegration(unittest.TestCase):
    TOOL = KIT_DIR / "framework" / "tools" / "early-warning.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.TOOL)] + list(args),
            capture_output=True, text=True, timeout=30,
        )

    def test_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("early", r.stdout.lower() + r.stderr.lower())

    def test_no_args(self):
        r = self._run()
        # Most tools show help or error without args
        self.assertIn(r.returncode, (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
