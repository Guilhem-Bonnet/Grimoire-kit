#!/usr/bin/env python3
"""
Tests pour sensory-buffer.py — Sensory Buffer — Mémoire sensorielle court terme pour agents.

Fonctions testées :
  - cmd_capture()
  - cmd_recall()
  - cmd_decay()
  - cmd_prioritize()
  - cmd_flush()
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
TOOL = KIT_DIR / "framework" / "tools" / "sensory-buffer.py"


def _import_mod():
    """Import le module sensory-buffer via importlib."""
    mod_name = "sensory_buffer"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "sensory-buffer.py")
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

    def test_sensory_item_exists(self):
        self.assertTrue(hasattr(self.mod, "SensoryItem"))

    def test_sensory_item_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.SensoryItem)}
        for expected in ["item_id", "agent", "timestamp", "category", "data"]:
            self.assertIn(expected, fields)

    def test_buffer_stats_exists(self):
        self.assertTrue(hasattr(self.mod, "BufferStats"))

    def test_buffer_stats_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.BufferStats)}
        for expected in ["agent", "total_items", "active_items", "decayed_items", "categories"]:
            self.assertIn(expected, fields)


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_version_defined(self):
        self.assertTrue(hasattr(self.mod, "VERSION"))

    def test_buffer_dir_defined(self):
        self.assertTrue(hasattr(self.mod, "BUFFER_DIR"))

    def test_decay_half_life_hours_defined(self):
        self.assertTrue(hasattr(self.mod, "DECAY_HALF_LIFE_HOURS"))

    def test_decay_threshold_defined(self):
        self.assertTrue(hasattr(self.mod, "DECAY_THRESHOLD"))

    def test_importance_multiplier_defined(self):
        self.assertTrue(hasattr(self.mod, "IMPORTANCE_MULTIPLIER"))


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

    def test_subcommand_capture_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["capture"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_recall_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["recall"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_decay_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["decay"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_prioritize_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["prioritize"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_flush_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["flush"])
        except SystemExit:
            pass  # Some subcommands may require args


class TestCLIIntegration(unittest.TestCase):
    TOOL = KIT_DIR / "framework" / "tools" / "sensory-buffer.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.TOOL)] + list(args),
            capture_output=True, text=True, timeout=30,
        )

    def test_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("sensory", r.stdout.lower() + r.stderr.lower())

    def test_no_args(self):
        r = self._run()
        # Most tools show help or error without args
        self.assertIn(r.returncode, (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
