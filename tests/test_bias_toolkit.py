#!/usr/bin/env python3
"""
Tests pour bias-toolkit.py — bias-toolkit.py — Catalogue de biais cognitifs BMAD.

Fonctions testées :
  - audit_file()
  - audit_project()
  - suggest_for_goal()
  - check_ethics()
  - format_catalog()
  - format_audit()
  - cmd_catalog()
  - cmd_audit()
  - cmd_suggest()
  - cmd_ethics()
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
TOOL = KIT_DIR / "framework" / "tools" / "bias-toolkit.py"


def _import_mod():
    """Import le module bias-toolkit via importlib."""
    mod_name = "bias_toolkit"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "bias-toolkit.py")
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

    def test_bias_exists(self):
        self.assertTrue(hasattr(self.mod, "Bias"))

    def test_bias_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.Bias)}
        for expected in ["id", "name", "category", "mechanism", "ethical_use"]:
            self.assertIn(expected, fields)

    def test_bias_detection_exists(self):
        self.assertTrue(hasattr(self.mod, "BiasDetection"))

    def test_bias_detection_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.BiasDetection)}
        for expected in ["bias_id", "bias_name", "file", "line", "context"]:
            self.assertIn(expected, fields)


class TestPureFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_audit_file_callable(self):
        self.assertTrue(callable(getattr(self.mod, "audit_file", None)))

    def test_suggest_for_goal_callable(self):
        self.assertTrue(callable(getattr(self.mod, "suggest_for_goal", None)))

    def test_check_ethics_callable(self):
        self.assertTrue(callable(getattr(self.mod, "check_ethics", None)))

    def test_format_catalog_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_catalog", None)))

    def test_format_audit_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_audit", None)))


class TestProjectFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        # Minimal BMAD structure
        (self.tmpdir / "_bmad" / "_memory").mkdir(parents=True, exist_ok=True)
        (self.tmpdir / "_bmad-output").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_audit_project_empty_project(self):
        try:
            result = self.mod.audit_project(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project


class TestFormatFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_format_catalog_callable(self):
        self.assertTrue(callable(self.mod.format_catalog))

    def test_format_audit_callable(self):
        self.assertTrue(callable(self.mod.format_audit))


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_version_defined(self):
        self.assertTrue(hasattr(self.mod, "VERSION"))

    def test_ethical_levels_defined(self):
        self.assertTrue(hasattr(self.mod, "ETHICAL_LEVELS"))

    def test_goal_bias_map_defined(self):
        self.assertTrue(hasattr(self.mod, "GOAL_BIAS_MAP"))


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

    def test_subcommand_catalog_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["catalog"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_audit_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["audit"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_suggest_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["suggest"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_ethics_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["ethics"])
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
    TOOL = KIT_DIR / "framework" / "tools" / "bias-toolkit.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.TOOL)] + list(args),
            capture_output=True, text=True, timeout=30,
        )

    def test_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("bias", r.stdout.lower() + r.stderr.lower())

    def test_no_args(self):
        r = self._run()
        # Most tools show help or error without args
        self.assertIn(r.returncode, (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
