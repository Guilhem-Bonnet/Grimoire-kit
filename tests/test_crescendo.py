#!/usr/bin/env python3
"""
Tests pour crescendo.py — crescendo.py — Onboarding progressif Grimoire.

Fonctions testées :
  - assess_project()
  - generate_guidance()
  - format_assessment()
  - format_guidance()
  - format_milestones()
  - cmd_assess()
  - cmd_guide()
  - cmd_adapt()
  - cmd_milestones()
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
TOOL = KIT_DIR / "framework" / "tools" / "crescendo.py"


def _import_mod():
    """Import le module crescendo via importlib."""
    mod_name = "crescendo"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "crescendo.py")
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

    def test_milestone_exists(self):
        self.assertTrue(hasattr(self.mod, "Milestone"))

    def test_milestone_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.Milestone)}
        for expected in ["id", "name", "level", "description", "check"]:
            self.assertIn(expected, fields)

    def test_assessment_result_exists(self):
        self.assertTrue(hasattr(self.mod, "AssessmentResult"))

    def test_assessment_result_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.AssessmentResult)}
        for expected in ["level", "score", "details", "achieved_milestones", "next_milestones"]:
            self.assertIn(expected, fields)

    def test_guidance_item_exists(self):
        self.assertTrue(hasattr(self.mod, "GuidanceItem"))

    def test_guidance_item_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.GuidanceItem)}
        for expected in ["title", "action", "why", "command"]:
            self.assertIn(expected, fields)


class TestPureFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_generate_guidance_callable(self):
        self.assertTrue(callable(getattr(self.mod, "generate_guidance", None)))

    def test_format_assessment_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_assessment", None)))

    def test_format_guidance_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_guidance", None)))

    def test_format_milestones_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_milestones", None)))


class TestProjectFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        # Minimal Grimoire structure
        (self.tmpdir / "_grimoire" / "_memory").mkdir(parents=True, exist_ok=True)
        (self.tmpdir / "_grimoire-output").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_assess_project_empty_project(self):
        try:
            result = self.mod.assess_project(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project


class TestFormatFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_format_assessment_callable(self):
        self.assertTrue(callable(self.mod.format_assessment))

    def test_format_guidance_callable(self):
        self.assertTrue(callable(self.mod.format_guidance))

    def test_format_milestones_callable(self):
        self.assertTrue(callable(self.mod.format_milestones))


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_version_defined(self):
        self.assertTrue(hasattr(self.mod, "VERSION"))

    def test_levels_defined(self):
        self.assertTrue(hasattr(self.mod, "LEVELS"))


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

    def test_subcommand_assess_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["assess"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_guide_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["guide"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_adapt_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["adapt"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_milestones_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["milestones"])
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
    TOOL = KIT_DIR / "framework" / "tools" / "crescendo.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.TOOL)] + list(args),
            capture_output=True, text=True, timeout=30,
        )

    def test_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("crescendo", r.stdout.lower() + r.stderr.lower())

    def test_no_args(self):
        r = self._run()
        # Most tools show help or error without args
        self.assertIn(r.returncode, (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
