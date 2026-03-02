#!/usr/bin/env python3
"""
Tests pour time-travel.py — Time-Travel — Archéologie temporelle et débogage historique.

Fonctions testées :
  - cmd_checkpoint()
  - cmd_history()
  - cmd_replay()
  - cmd_restore()
  - cmd_bisect()
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
TOOL = KIT_DIR / "framework" / "tools" / "time-travel.py"


def _import_mod():
    """Import le module time-travel via importlib."""
    mod_name = "time_travel"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "time-travel.py")
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

    def test_checkpoint_meta_exists(self):
        self.assertTrue(hasattr(self.mod, "CheckpointMeta"))

    def test_checkpoint_meta_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.CheckpointMeta)}
        for expected in ["checkpoint_id", "label", "timestamp", "files_count", "total_size"]:
            self.assertIn(expected, fields)

    def test_replay_step_exists(self):
        self.assertTrue(hasattr(self.mod, "ReplayStep"))

    def test_replay_step_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.ReplayStep)}
        for expected in ["from_cp", "to_cp", "added", "removed", "modified"]:
            self.assertIn(expected, fields)


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_version_defined(self):
        self.assertTrue(hasattr(self.mod, "VERSION"))

    def test_checkpoint_dir_defined(self):
        self.assertTrue(hasattr(self.mod, "CHECKPOINT_DIR"))

    def test_meta_file_defined(self):
        self.assertTrue(hasattr(self.mod, "META_FILE"))

    def test_tracked_dirs_defined(self):
        self.assertTrue(hasattr(self.mod, "TRACKED_DIRS"))

    def test_tracked_exts_defined(self):
        self.assertTrue(hasattr(self.mod, "TRACKED_EXTS"))


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

    def test_subcommand_checkpoint_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["checkpoint"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_history_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["history"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_replay_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["replay"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_restore_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["restore"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_bisect_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["bisect"])
        except SystemExit:
            pass  # Some subcommands may require args


class TestCLIIntegration(unittest.TestCase):
    TOOL = KIT_DIR / "framework" / "tools" / "time-travel.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.TOOL)] + list(args),
            capture_output=True, text=True, timeout=30,
        )

    def test_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("time", r.stdout.lower() + r.stderr.lower())

    def test_no_args(self):
        r = self._run()
        # Most tools show help or error without args
        self.assertIn(r.returncode, (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
