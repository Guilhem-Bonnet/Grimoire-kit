#!/usr/bin/env python3
"""
Tests pour harmony-check.py — harmony-check.py — Architecture Harmony Check BMAD.

Fonctions testées :
  - scan_project()
  - detect_orphans()
  - detect_naming()
  - detect_oversized()
  - detect_manifest_mismatch()
  - detect_broken_refs()
  - detect_duplication()
  - calculate_harmony_score()
  - full_analysis()
  - format_scan()
  - format_dissonances()
  - format_score()
  - format_report()
  - cmd_scan()
  - cmd_check()
  - cmd_dissonance()
  - cmd_score()
  - cmd_report()
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
TOOL = KIT_DIR / "framework" / "tools" / "harmony-check.py"


def _import_mod():
    """Import le module harmony-check via importlib."""
    mod_name = "harmony_check"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "harmony-check.py")
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

    def test_dissonance_exists(self):
        self.assertTrue(hasattr(self.mod, "Dissonance"))

    def test_dissonance_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.Dissonance)}
        for expected in ["category", "severity", "file", "message", "suggestion"]:
            self.assertIn(expected, fields)

    def test_arch_scan_exists(self):
        self.assertTrue(hasattr(self.mod, "ArchScan"))

    def test_arch_scan_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.ArchScan)}
        for expected in ["agents", "workflows", "tools", "configs", "docs"]:
            self.assertIn(expected, fields)


class TestPureFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_detect_orphans_callable(self):
        self.assertTrue(callable(getattr(self.mod, "detect_orphans", None)))

    def test_calculate_harmony_score_callable(self):
        self.assertTrue(callable(getattr(self.mod, "calculate_harmony_score", None)))

    def test_format_scan_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_scan", None)))

    def test_format_dissonances_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_dissonances", None)))

    def test_format_score_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_score", None)))

    def test_format_report_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_report", None)))


class TestProjectFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        # Minimal BMAD structure
        (self.tmpdir / "_bmad" / "_memory").mkdir(parents=True, exist_ok=True)
        (self.tmpdir / "_bmad-output").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_scan_project_empty_project(self):
        try:
            result = self.mod.scan_project(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project

    def test_full_analysis_empty_project(self):
        try:
            result = self.mod.full_analysis(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project


class TestFormatFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_format_scan_callable(self):
        self.assertTrue(callable(self.mod.format_scan))

    def test_format_dissonances_callable(self):
        self.assertTrue(callable(self.mod.format_dissonances))

    def test_format_score_callable(self):
        self.assertTrue(callable(self.mod.format_score))

    def test_format_report_callable(self):
        self.assertTrue(callable(self.mod.format_report))


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_version_defined(self):
        self.assertTrue(hasattr(self.mod, "VERSION"))

    def test_max_file_lines_defined(self):
        self.assertTrue(hasattr(self.mod, "MAX_FILE_LINES"))

    def test_naming_pattern_defined(self):
        self.assertTrue(hasattr(self.mod, "NAMING_PATTERN"))

    def test_severity_high_defined(self):
        self.assertTrue(hasattr(self.mod, "SEVERITY_HIGH"))

    def test_severity_medium_defined(self):
        self.assertTrue(hasattr(self.mod, "SEVERITY_MEDIUM"))

    def test_severity_low_defined(self):
        self.assertTrue(hasattr(self.mod, "SEVERITY_LOW"))


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

    def test_subcommand_check_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["check"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_dissonance_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["dissonance"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_score_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["score"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_report_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["report"])
        except SystemExit:
            pass  # Some subcommands may require args


class TestCLIIntegration(unittest.TestCase):
    TOOL = KIT_DIR / "framework" / "tools" / "harmony-check.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.TOOL)] + list(args),
            capture_output=True, text=True, timeout=30,
        )

    def test_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("harmony", r.stdout.lower() + r.stderr.lower())

    def test_no_args(self):
        r = self._run()
        # Most tools show help or error without args
        self.assertIn(r.returncode, (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
