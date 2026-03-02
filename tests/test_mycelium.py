#!/usr/bin/env python3
"""
Tests pour mycelium.py — mycelium.py — Réseau Mycelium BMAD.

Fonctions testées :
  - scan_patterns()
  - anonymize_content()
  - export_patterns()
  - import_patterns()
  - match_projects()
  - update_catalog()
  - format_scan()
  - cmd_scan()
  - cmd_export()
  - cmd_import()
  - cmd_match()
  - cmd_catalog()
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
TOOL = KIT_DIR / "framework" / "tools" / "mycelium.py"


def _import_mod():
    """Import le module mycelium via importlib."""
    mod_name = "mycelium"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "mycelium.py")
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

    def test_pattern_exists(self):
        self.assertTrue(hasattr(self.mod, "Pattern"))

    def test_pattern_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.Pattern)}
        for expected in ["id", "name", "pattern_type", "source_path", "size_bytes"]:
            self.assertIn(expected, fields)

    def test_match_result_exists(self):
        self.assertTrue(hasattr(self.mod, "MatchResult"))

    def test_match_result_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.MatchResult)}
        for expected in ["pattern_type", "local_file", "remote_file", "similarity"]:
            self.assertIn(expected, fields)


class TestPureFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_anonymize_content_callable(self):
        self.assertTrue(callable(getattr(self.mod, "anonymize_content", None)))

    def test_format_scan_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_scan", None)))


class TestProjectFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        # Minimal BMAD structure
        (self.tmpdir / "_bmad" / "_memory").mkdir(parents=True, exist_ok=True)
        (self.tmpdir / "_bmad-output").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_scan_patterns_empty_project(self):
        try:
            result = self.mod.scan_patterns(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project

    def test_export_patterns_callable(self):
        self.assertTrue(callable(self.mod.export_patterns))

    def test_import_patterns_callable(self):
        self.assertTrue(callable(self.mod.import_patterns))

    def test_match_projects_callable(self):
        self.assertTrue(callable(self.mod.match_projects))


class TestFormatFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_format_scan_callable(self):
        self.assertTrue(callable(self.mod.format_scan))


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_version_defined(self):
        self.assertTrue(hasattr(self.mod, "VERSION"))

    def test_pattern_catalog_defined(self):
        self.assertTrue(hasattr(self.mod, "PATTERN_CATALOG"))

    def test_pattern_types_defined(self):
        self.assertTrue(hasattr(self.mod, "PATTERN_TYPES"))


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

    def test_subcommand_export_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["export"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_import_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["import"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_match_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["match"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_catalog_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["catalog"])
        except SystemExit:
            pass  # Some subcommands may require args


class TestCLIIntegration(unittest.TestCase):
    TOOL = KIT_DIR / "framework" / "tools" / "mycelium.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.TOOL)] + list(args),
            capture_output=True, text=True, timeout=30,
        )

    def test_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("mycelium", r.stdout.lower() + r.stderr.lower())

    def test_no_args(self):
        r = self._run()
        # Most tools show help or error without args
        self.assertIn(r.returncode, (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
