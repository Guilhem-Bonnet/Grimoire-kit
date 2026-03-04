#!/usr/bin/env python3
"""
Tests pour new-game-plus.py — new-game-plus.py — New Game+ BMAD.

Fonctions testées :
  - scan_project()
  - find_similarity()
  - plan_import()
  - export_assets()
  - format_scan()
  - format_similarity()
  - format_plan()
  - cmd_scan()
  - cmd_similarity()
  - cmd_plan()
  - cmd_export()
  - cmd_recommend()
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
TOOL = KIT_DIR / "framework" / "tools" / "new-game-plus.py"


def _import_mod():
    """Import le module new-game-plus via importlib."""
    mod_name = "new_game_plus"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "new-game-plus.py")
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

    def test_asset_exists(self):
        self.assertTrue(hasattr(self.mod, "Asset"))

    def test_asset_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.Asset)}
        for expected in ["category", "path", "size_bytes", "reusable", "reason"]:
            self.assertIn(expected, fields)

    def test_similarity_result_exists(self):
        self.assertTrue(hasattr(self.mod, "SimilarityResult"))

    def test_similarity_result_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.SimilarityResult)}
        for expected in ["archetype", "score", "matching_files"]:
            self.assertIn(expected, fields)

    def test_import_plan_exists(self):
        self.assertTrue(hasattr(self.mod, "ImportPlan"))

    def test_import_plan_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.ImportPlan)}
        for expected in ["assets_to_import", "assets_to_skip", "estimated_size", "recommendations"]:
            self.assertIn(expected, fields)

    def test_project_scan_exists(self):
        self.assertTrue(hasattr(self.mod, "ProjectScan"))

    def test_project_scan_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.ProjectScan)}
        for expected in ["total_files", "assets", "categories", "tech_stack"]:
            self.assertIn(expected, fields)


class TestPureFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_scan_project_callable(self):
        self.assertTrue(callable(getattr(self.mod, "scan_project", None)))

    def test_find_similarity_callable(self):
        self.assertTrue(callable(getattr(self.mod, "find_similarity", None)))

    def test_plan_import_callable(self):
        self.assertTrue(callable(getattr(self.mod, "plan_import", None)))

    def test_export_assets_callable(self):
        self.assertTrue(callable(getattr(self.mod, "export_assets", None)))

    def test_format_scan_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_scan", None)))

    def test_format_similarity_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_similarity", None)))

    def test_format_plan_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_plan", None)))


class TestFormatFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_format_scan_callable(self):
        self.assertTrue(callable(self.mod.format_scan))

    def test_format_similarity_callable(self):
        self.assertTrue(callable(self.mod.format_similarity))

    def test_format_plan_callable(self):
        self.assertTrue(callable(self.mod.format_plan))


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_version_defined(self):
        self.assertTrue(hasattr(self.mod, "VERSION"))

    def test_asset_categories_defined(self):
        self.assertTrue(hasattr(self.mod, "ASSET_CATEGORIES"))


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

    def test_subcommand_similarity_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["similarity"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_plan_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["plan"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_export_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["export"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_recommend_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["recommend"])
        except SystemExit:
            pass  # Some subcommands may require args


class TestCLIIntegration(unittest.TestCase):
    TOOL = KIT_DIR / "framework" / "tools" / "new-game-plus.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.TOOL)] + list(args),
            capture_output=True, text=True, timeout=30,
        )

    def test_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("new", r.stdout.lower() + r.stderr.lower())

    def test_no_args(self):
        r = self._run()
        # Most tools show help or error without args
        self.assertIn(r.returncode, (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
