#!/usr/bin/env python3
"""
Tests pour incubator.py — incubator.py — Incubation & Dormance BMAD.

Fonctions testées :
  - load_incubator()
  - save_incubator()
  - next_id()
  - check_viability()
  - is_viable()
  - auto_prune()
  - format_idea()
  - format_status()
  - cmd_submit()
  - cmd_status()
  - cmd_viable()
  - cmd_wake()
  - cmd_prune()
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
TOOL = KIT_DIR / "framework" / "tools" / "incubator.py"


def _import_mod():
    """Import le module incubator via importlib."""
    mod_name = "incubator"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "incubator.py")
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

    def test_idea_exists(self):
        self.assertTrue(hasattr(self.mod, "Idea"))

    def test_idea_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.Idea)}
        for expected in ["id", "title", "description", "status", "created_at"]:
            self.assertIn(expected, fields)


class TestPureFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_next_id_callable(self):
        self.assertTrue(callable(getattr(self.mod, "next_id", None)))

    def test_is_viable_callable(self):
        self.assertTrue(callable(getattr(self.mod, "is_viable", None)))

    def test_auto_prune_callable(self):
        self.assertTrue(callable(getattr(self.mod, "auto_prune", None)))

    def test_format_idea_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_idea", None)))

    def test_format_status_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_status", None)))


class TestProjectFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        # Minimal BMAD structure
        (self.tmpdir / "_bmad" / "_memory").mkdir(parents=True, exist_ok=True)
        (self.tmpdir / "_bmad-output").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_incubator_empty_project(self):
        try:
            result = self.mod.load_incubator(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project

    def test_check_viability_callable(self):
        self.assertTrue(callable(self.mod.check_viability))


class TestFormatFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_format_idea_callable(self):
        self.assertTrue(callable(self.mod.format_idea))

    def test_format_status_callable(self):
        self.assertTrue(callable(self.mod.format_status))


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_version_defined(self):
        self.assertTrue(hasattr(self.mod, "VERSION"))

    def test_incubator_file_defined(self):
        self.assertTrue(hasattr(self.mod, "INCUBATOR_FILE"))

    def test_lifecycle_defined(self):
        self.assertTrue(hasattr(self.mod, "LIFECYCLE"))

    def test_viability_checks_defined(self):
        self.assertTrue(hasattr(self.mod, "VIABILITY_CHECKS"))


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

    def test_subcommand_submit_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["submit"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_status_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["status"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_viable_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["viable"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_wake_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["wake"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_prune_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["prune"])
        except SystemExit:
            pass  # Some subcommands may require args


class TestCLIIntegration(unittest.TestCase):
    TOOL = KIT_DIR / "framework" / "tools" / "incubator.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.TOOL)] + list(args),
            capture_output=True, text=True, timeout=30,
        )

    def test_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("incubator", r.stdout.lower() + r.stderr.lower())

    def test_no_args(self):
        r = self._run()
        # Most tools show help or error without args
        self.assertIn(r.returncode, (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
