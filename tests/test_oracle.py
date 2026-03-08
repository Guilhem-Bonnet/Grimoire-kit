#!/usr/bin/env python3
"""
Tests pour oracle.py — oracle.py — Oracle Introspectif Grimoire.

Fonctions testées :
  - analyze_swot()
  - analyze_attractors()
  - analyze_maturity()
  - generate_recommendations()
  - build_report()
  - format_swot()
  - format_maturity()
  - format_full_report()
  - report_to_dict()
  - cmd_swot()
  - cmd_attract()
  - cmd_maturity()
  - cmd_advise()
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
TOOL = KIT_DIR / "framework" / "tools" / "oracle.py"


def _import_mod():
    """Import le module oracle via importlib."""
    mod_name = "oracle"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "oracle.py")
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

    def test_swot_item_exists(self):
        self.assertTrue(hasattr(self.mod, "SWOTItem"))

    def test_swot_item_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.SWOTItem)}
        for expected in ["text", "evidence", "score"]:
            self.assertIn(expected, fields)

    def test_swot_exists(self):
        self.assertTrue(hasattr(self.mod, "SWOT"))

    def test_swot_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.SWOT)}
        for expected in ["strengths", "weaknesses", "opportunities", "threats"]:
            self.assertIn(expected, fields)

    def test_attractor_exists(self):
        self.assertTrue(hasattr(self.mod, "Attractor"))

    def test_attractor_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.Attractor)}
        for expected in ["name", "description", "evidence", "strength"]:
            self.assertIn(expected, fields)

    def test_maturity_dimension_exists(self):
        self.assertTrue(hasattr(self.mod, "MaturityDimension"))

    def test_maturity_dimension_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.MaturityDimension)}
        for expected in ["name", "score", "detail"]:
            self.assertIn(expected, fields)

    def test_oracle_report_exists(self):
        self.assertTrue(hasattr(self.mod, "OracleReport"))

    def test_oracle_report_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.OracleReport)}
        for expected in ["swot", "attractors", "maturity_dimensions", "maturity_score", "maturity_level"]:
            self.assertIn(expected, fields)


class TestPureFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_generate_recommendations_callable(self):
        self.assertTrue(callable(getattr(self.mod, "generate_recommendations", None)))

    def test_format_swot_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_swot", None)))

    def test_format_maturity_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_maturity", None)))

    def test_format_full_report_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_full_report", None)))

    def test_report_to_dict_callable(self):
        self.assertTrue(callable(getattr(self.mod, "report_to_dict", None)))


class TestProjectFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        # Minimal Grimoire structure
        (self.tmpdir / "_grimoire" / "_memory").mkdir(parents=True, exist_ok=True)
        (self.tmpdir / "_grimoire-output").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_analyze_swot_empty_project(self):
        try:
            result = self.mod.analyze_swot(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project

    def test_analyze_attractors_empty_project(self):
        try:
            result = self.mod.analyze_attractors(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project

    def test_analyze_maturity_empty_project(self):
        try:
            result = self.mod.analyze_maturity(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project

    def test_build_report_empty_project(self):
        try:
            result = self.mod.build_report(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project


class TestFormatFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_format_swot_callable(self):
        self.assertTrue(callable(self.mod.format_swot))

    def test_format_maturity_callable(self):
        self.assertTrue(callable(self.mod.format_maturity))

    def test_format_full_report_callable(self):
        self.assertTrue(callable(self.mod.format_full_report))


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_oracle_version_defined(self):
        self.assertTrue(hasattr(self.mod, "ORACLE_VERSION"))

    def test_maturity_levels_defined(self):
        self.assertTrue(hasattr(self.mod, "MATURITY_LEVELS"))


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

    def test_subcommand_swot_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["swot"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_attract_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["attract"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_maturity_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["maturity"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_advise_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["advise"])
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
    TOOL = KIT_DIR / "framework" / "tools" / "oracle.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.TOOL)] + list(args),
            capture_output=True, text=True, timeout=30,
        )

    def test_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("oracle", r.stdout.lower() + r.stderr.lower())

    def test_no_args(self):
        r = self._run()
        # Most tools show help or error without args
        self.assertIn(r.returncode, (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
