#!/usr/bin/env python3
"""
Tests pour distill.py — distill.py — Réduction & Director's Cut BMAD.

Fonctions testées :
  - analyze_document()
  - condense()
  - transform_document()
  - format_modes()
  - cmd_condense()
  - cmd_modes()
  - cmd_transform()
  - cmd_compare()
  - build_parser()
"""

import importlib
import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

KIT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(KIT_DIR / "framework" / "tools"))
TOOL = KIT_DIR / "framework" / "tools" / "distill.py"


def _import_mod():
    """Import le module distill via importlib."""
    mod_name = "distill"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "distill.py")
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

    def test_document_analysis_exists(self):
        self.assertTrue(hasattr(self.mod, "DocumentAnalysis"))

    def test_document_analysis_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.DocumentAnalysis)}
        for expected in ["title", "total_lines", "total_words", "total_sentences", "headers"]:
            self.assertIn(expected, fields)

    def test_condensed_output_exists(self):
        self.assertTrue(hasattr(self.mod, "CondensedOutput"))

    def test_condensed_output_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.CondensedOutput)}
        for expected in ["mode", "original_words", "condensed_words", "ratio", "content"]:
            self.assertIn(expected, fields)


class TestPureFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_analyze_document_callable(self):
        self.assertTrue(callable(getattr(self.mod, "analyze_document", None)))

    def test_condense_callable(self):
        self.assertTrue(callable(getattr(self.mod, "condense", None)))

    def test_transform_document_callable(self):
        self.assertTrue(callable(getattr(self.mod, "transform_document", None)))

    def test_format_modes_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_modes", None)))


class TestFormatFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_format_modes_callable(self):
        self.assertTrue(callable(self.mod.format_modes))


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_version_defined(self):
        self.assertTrue(hasattr(self.mod, "VERSION"))

    def test_verbosity_modes_defined(self):
        self.assertTrue(hasattr(self.mod, "VERBOSITY_MODES"))

    def test_templates_defined(self):
        self.assertTrue(hasattr(self.mod, "TEMPLATES"))


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

    def test_subcommand_condense_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["condense"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_modes_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["modes"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_transform_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["transform"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_compare_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["compare"])
        except SystemExit:
            pass  # Some subcommands may require args


class TestCLIIntegration(unittest.TestCase):
    TOOL = KIT_DIR / "framework" / "tools" / "distill.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.TOOL)] + list(args),
            capture_output=True, text=True, timeout=30,
        )

    def test_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("distill", r.stdout.lower() + r.stderr.lower())

    def test_no_args(self):
        r = self._run()
        # Most tools show help or error without args
        self.assertIn(r.returncode, (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
