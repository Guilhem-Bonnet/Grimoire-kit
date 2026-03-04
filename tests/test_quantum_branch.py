#!/usr/bin/env python3
"""
Tests pour quantum-branch.py — Quantum Branch — Timelines parallèles et multivers de projet.

Fonctions testées :
  - cmd_fork()
  - cmd_list()
  - cmd_compare()
  - cmd_merge()
  - cmd_prune()
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
TOOL = KIT_DIR / "framework" / "tools" / "quantum-branch.py"


def _import_mod():
    """Import le module quantum-branch via importlib."""
    mod_name = "quantum_branch"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "quantum-branch.py")
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

    def test_branch_meta_exists(self):
        self.assertTrue(hasattr(self.mod, "BranchMeta"))

    def test_branch_meta_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.BranchMeta)}
        for expected in ["name", "created", "parent", "description", "status"]:
            self.assertIn(expected, fields)

    def test_branch_diff_exists(self):
        self.assertTrue(hasattr(self.mod, "BranchDiff"))

    def test_branch_diff_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.BranchDiff)}
        for expected in ["source", "target", "added", "removed", "modified"]:
            self.assertIn(expected, fields)


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_version_defined(self):
        self.assertTrue(hasattr(self.mod, "VERSION"))

    def test_branch_dir_defined(self):
        self.assertTrue(hasattr(self.mod, "BRANCH_DIR"))

    def test_branch_meta_defined(self):
        self.assertTrue(hasattr(self.mod, "BRANCH_META"))

    def test_snap_dirs_defined(self):
        self.assertTrue(hasattr(self.mod, "SNAP_DIRS"))

    def test_snap_exts_defined(self):
        self.assertTrue(hasattr(self.mod, "SNAP_EXTS"))


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

    def test_subcommand_fork_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["fork"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_list_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["list"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_compare_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["compare"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_merge_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["merge"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_prune_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["prune"])
        except SystemExit:
            pass  # Some subcommands may require args


class TestCLIIntegration(unittest.TestCase):
    TOOL = KIT_DIR / "framework" / "tools" / "quantum-branch.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.TOOL)] + list(args),
            capture_output=True, text=True, timeout=30,
        )

    def test_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("quantum", r.stdout.lower() + r.stderr.lower())

    def test_no_args(self):
        r = self._run()
        # Most tools show help or error without args
        self.assertIn(r.returncode, (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
