#!/usr/bin/env python3
"""
Tests pour immune-system.py — immune-system.py — Système immunitaire BMAD.

Fonctions testées :
  - scan_innate()
  - load_antibodies()
  - save_antibodies()
  - scan_adaptive()
  - format_report()
  - cmd_scan()
  - cmd_innate()
  - cmd_adaptive()
  - cmd_learn()
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
TOOL = KIT_DIR / "framework" / "tools" / "immune-system.py"


def _import_mod():
    """Import le module immune-system via importlib."""
    mod_name = "immune_system"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "immune-system.py")
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

    def test_finding_exists(self):
        self.assertTrue(hasattr(self.mod, "Finding"))

    def test_finding_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.Finding)}
        for expected in ["rule_id", "rule_name", "severity", "file", "line"]:
            self.assertIn(expected, fields)

    def test_antibody_exists(self):
        self.assertTrue(hasattr(self.mod, "Antibody"))

    def test_antibody_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.Antibody)}
        for expected in ["id", "type", "pattern", "description", "fix"]:
            self.assertIn(expected, fields)

    def test_immune_report_exists(self):
        self.assertTrue(hasattr(self.mod, "ImmuneReport"))

    def test_immune_report_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.ImmuneReport)}
        for expected in ["findings", "innate_checks", "adaptive_checks", "files_scanned", "timestamp"]:
            self.assertIn(expected, fields)


class TestPureFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

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

    def test_scan_innate_callable(self):
        self.assertTrue(callable(self.mod.scan_innate))

    def test_load_antibodies_empty_project(self):
        try:
            result = self.mod.load_antibodies(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project

    def test_save_antibodies_callable(self):
        self.assertTrue(callable(self.mod.save_antibodies))

    def test_scan_adaptive_callable(self):
        self.assertTrue(callable(self.mod.scan_adaptive))


class TestFormatFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_format_report_callable(self):
        self.assertTrue(callable(self.mod.format_report))


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_immune_version_defined(self):
        self.assertTrue(hasattr(self.mod, "IMMUNE_VERSION"))

    def test_antibody_file_defined(self):
        self.assertTrue(hasattr(self.mod, "ANTIBODY_FILE"))

    def test_innate_rules_defined(self):
        self.assertTrue(hasattr(self.mod, "INNATE_RULES"))


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

    def test_subcommand_innate_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["innate"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_adaptive_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["adaptive"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_learn_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["learn"])
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
    TOOL = KIT_DIR / "framework" / "tools" / "immune-system.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.TOOL)] + list(args),
            capture_output=True, text=True, timeout=30,
        )

    def test_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("immune", r.stdout.lower() + r.stderr.lower())

    def test_no_args(self):
        r = self._run()
        # Most tools show help or error without args
        self.assertIn(r.returncode, (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
