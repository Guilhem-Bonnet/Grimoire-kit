#!/usr/bin/env python3
"""
Tests pour preflight-check.py — preflight-check.py — Vérification pré-exécution Grimoire.

Fonctions testées :
  - check_grimoire_structure()
  - check_tools_available()
  - check_git_state()
  - check_memory_state()
  - check_story_readiness()
  - check_wuwei()
  - run_all_checks()
  - format_report()
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
TOOL = KIT_DIR / "framework" / "tools" / "preflight-check.py"


def _import_mod():
    """Import le module preflight-check via importlib."""
    mod_name = "preflight_check"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "preflight-check.py")
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

    def test_check_exists(self):
        self.assertTrue(hasattr(self.mod, "Check"))

    def test_check_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.Check)}
        for expected in ["name", "severity", "message", "fix_hint", "auto_fixable"]:
            self.assertIn(expected, fields)

    def test_preflight_report_exists(self):
        self.assertTrue(hasattr(self.mod, "PreflightReport"))

    def test_preflight_report_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.PreflightReport)}
        for expected in ["agent", "story", "checks", "timestamp"]:
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
        # Minimal Grimoire structure
        (self.tmpdir / "_grimoire" / "_memory").mkdir(parents=True, exist_ok=True)
        (self.tmpdir / "_grimoire-output").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_check_grimoire_structure_empty_project(self):
        try:
            result = self.mod.check_grimoire_structure(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project

    def test_check_tools_available_empty_project(self):
        try:
            result = self.mod.check_tools_available(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project

    def test_check_git_state_empty_project(self):
        try:
            result = self.mod.check_git_state(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project

    def test_check_memory_state_empty_project(self):
        try:
            result = self.mod.check_memory_state(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project

    def test_check_story_readiness_callable(self):
        self.assertTrue(callable(self.mod.check_story_readiness))

    def test_check_wuwei_callable(self):
        self.assertTrue(callable(self.mod.check_wuwei))

    def test_run_all_checks_callable(self):
        self.assertTrue(callable(self.mod.run_all_checks))


class TestFormatFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_format_report_callable(self):
        self.assertTrue(callable(self.mod.format_report))


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_preflight_version_defined(self):
        self.assertTrue(hasattr(self.mod, "PREFLIGHT_VERSION"))


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


class TestCLIIntegration(unittest.TestCase):
    TOOL = KIT_DIR / "framework" / "tools" / "preflight-check.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.TOOL), *list(args)],
            capture_output=True, text=True, timeout=30,
        )

    def test_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("preflight", r.stdout.lower() + r.stderr.lower())

    def test_no_args(self):
        r = self._run()
        # Most tools show help or error without args
        self.assertIn(r.returncode, (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
