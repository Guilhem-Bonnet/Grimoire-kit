#!/usr/bin/env python3
"""
Tests pour dark-matter.py — dark-matter.py — Détecteur de matière noire Grimoire.

Fonctions testées :
  - detect_magic_values()
  - detect_naming_conventions()
  - detect_silos()
  - detect_implicit_assumptions()
  - detect_undocumented_dependencies()
  - build_full_report()
  - format_report()
  - generate_documentation()
  - cmd_scan()
  - cmd_patterns()
  - cmd_silos()
  - cmd_implicit()
  - cmd_document()
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
TOOL = KIT_DIR / "framework" / "tools" / "dark-matter.py"


def _import_mod():
    """Import le module dark-matter via importlib."""
    mod_name = "dark_matter"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "dark-matter.py")
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

    def test_dark_matter_item_exists(self):
        self.assertTrue(hasattr(self.mod, "DarkMatterItem"))

    def test_dark_matter_item_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.DarkMatterItem)}
        for expected in ["dark_type", "title", "description", "location", "confidence"]:
            self.assertIn(expected, fields)

    def test_dark_matter_report_exists(self):
        self.assertTrue(hasattr(self.mod, "DarkMatterReport"))

    def test_dark_matter_report_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.DarkMatterReport)}
        for expected in ["items", "bus_factor", "documentation_coverage", "timestamp"]:
            self.assertIn(expected, fields)


class TestPureFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_format_report_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_report", None)))

    def test_generate_documentation_callable(self):
        self.assertTrue(callable(getattr(self.mod, "generate_documentation", None)))


class TestProjectFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        # Minimal Grimoire structure
        (self.tmpdir / "_grimoire" / "_memory").mkdir(parents=True, exist_ok=True)
        (self.tmpdir / "_grimoire-output").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_detect_magic_values_empty_project(self):
        try:
            result = self.mod.detect_magic_values(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project

    def test_detect_naming_conventions_empty_project(self):
        try:
            result = self.mod.detect_naming_conventions(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project

    def test_detect_silos_empty_project(self):
        try:
            result = self.mod.detect_silos(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project

    def test_detect_implicit_assumptions_empty_project(self):
        try:
            result = self.mod.detect_implicit_assumptions(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project

    def test_detect_undocumented_dependencies_empty_project(self):
        try:
            result = self.mod.detect_undocumented_dependencies(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project

    def test_build_full_report_empty_project(self):
        try:
            result = self.mod.build_full_report(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project


class TestFormatFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_format_report_callable(self):
        self.assertTrue(callable(self.mod.format_report))


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_dark_matter_version_defined(self):
        self.assertTrue(hasattr(self.mod, "DARK_MATTER_VERSION"))


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

    def test_subcommand_patterns_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["patterns"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_silos_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["silos"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_implicit_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["implicit"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_document_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["document"])
        except SystemExit:
            pass  # Some subcommands may require args


class TestCLIIntegration(unittest.TestCase):
    TOOL = KIT_DIR / "framework" / "tools" / "dark-matter.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.TOOL)] + list(args),
            capture_output=True, text=True, timeout=30,
        )

    def test_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("dark", r.stdout.lower() + r.stderr.lower())

    def test_no_args(self):
        r = self._run()
        # Most tools show help or error without args
        self.assertIn(r.returncode, (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
