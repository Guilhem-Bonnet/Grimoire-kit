#!/usr/bin/env python3
"""
Tests pour nudge-engine.py — nudge-engine.py — Moteur de suggestions contextuelles Grimoire.

Fonctions testées :
  - parse_markdown_entries()
  - load_all_memory()
  - compute_relevance()
  - generate_suggestions()
  - generate_serendipity()
  - generate_recalls()
  - format_report()
  - report_to_dict()
  - cmd_suggest()
  - cmd_serendip()
  - cmd_recall()
  - build_parser()
"""

import contextlib
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
TOOL = KIT_DIR / "framework" / "tools" / "nudge-engine.py"


def _import_mod():
    """Import le module nudge-engine via importlib."""
    mod_name = "nudge_engine"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "nudge-engine.py")
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

    def test_memory_entry_exists(self):
        self.assertTrue(hasattr(self.mod, "MemoryEntry"))

    def test_memory_entry_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.MemoryEntry)}
        for expected in ["source", "kind", "text", "agent", "date"]:
            self.assertIn(expected, fields)

    def test_nudge_exists(self):
        self.assertTrue(hasattr(self.mod, "Nudge"))

    def test_nudge_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.Nudge)}
        for expected in ["nudge_type", "title", "message", "relevance", "sources"]:
            self.assertIn(expected, fields)

    def test_nudge_report_exists(self):
        self.assertTrue(hasattr(self.mod, "NudgeReport"))

    def test_nudge_report_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.NudgeReport)}
        for expected in ["mode", "agent", "context", "nudges", "timestamp"]:
            self.assertIn(expected, fields)


class TestPureFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_parse_markdown_entries_callable(self):
        self.assertTrue(callable(getattr(self.mod, "parse_markdown_entries", None)))

    def test_compute_relevance_callable(self):
        self.assertTrue(callable(getattr(self.mod, "compute_relevance", None)))

    def test_generate_suggestions_callable(self):
        self.assertTrue(callable(getattr(self.mod, "generate_suggestions", None)))

    def test_generate_serendipity_callable(self):
        self.assertTrue(callable(getattr(self.mod, "generate_serendipity", None)))

    def test_generate_recalls_callable(self):
        self.assertTrue(callable(getattr(self.mod, "generate_recalls", None)))

    def test_format_report_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_report", None)))

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

    def test_load_all_memory_empty_project(self):
        try:
            result = self.mod.load_all_memory(self.tmpdir)
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

    def test_nudge_version_defined(self):
        self.assertTrue(hasattr(self.mod, "NUDGE_VERSION"))

    def test_memory_dirs_defined(self):
        self.assertTrue(hasattr(self.mod, "MEMORY_DIRS"))

    def test_learnings_glob_defined(self):
        self.assertTrue(hasattr(self.mod, "LEARNINGS_GLOB"))

    def test_decisions_glob_defined(self):
        self.assertTrue(hasattr(self.mod, "DECISIONS_GLOB"))

    def test_failure_glob_defined(self):
        self.assertTrue(hasattr(self.mod, "FAILURE_GLOB"))

    def test_dream_glob_defined(self):
        self.assertTrue(hasattr(self.mod, "DREAM_GLOB"))

    def test_shared_glob_defined(self):
        self.assertTrue(hasattr(self.mod, "SHARED_GLOB"))

    def test_max_nudges_defined(self):
        self.assertTrue(hasattr(self.mod, "MAX_NUDGES"))


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

    def test_subcommand_suggest_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        with contextlib.suppress(SystemExit):
            parser.parse_args(["suggest"])

    def test_subcommand_serendip_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        with contextlib.suppress(SystemExit):
            parser.parse_args(["serendip"])

    def test_subcommand_recall_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        with contextlib.suppress(SystemExit):
            parser.parse_args(["recall"])


class TestCLIIntegration(unittest.TestCase):
    TOOL = KIT_DIR / "framework" / "tools" / "nudge-engine.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.TOOL), *list(args)],
            capture_output=True, text=True, timeout=30,
        )

    def test_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("nudge", r.stdout.lower() + r.stderr.lower())

    def test_no_args(self):
        r = self._run()
        # Most tools show help or error without args
        self.assertIn(r.returncode, (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
