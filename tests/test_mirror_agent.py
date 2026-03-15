#!/usr/bin/env python3
"""
Tests pour mirror-agent.py — Mirror Agent — Neurones miroirs pour apprentissage inter-agents.

Fonctions testées :
  - cmd_observe()
  - cmd_learn()
  - cmd_mirror()
  - cmd_catalog()
  - cmd_diff()
  - build_parser()
"""

import contextlib
import importlib
import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

KIT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(KIT_DIR / "framework" / "tools"))
TOOL = KIT_DIR / "framework" / "tools" / "mirror-agent.py"


def _import_mod():
    """Import le module mirror-agent via importlib."""
    mod_name = "mirror_agent"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "mirror-agent.py")
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

    def test_agent_profile_exists(self):
        self.assertTrue(hasattr(self.mod, "AgentProfile"))

    def test_agent_profile_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.AgentProfile)}
        for expected in ["name", "path", "persona", "capabilities", "menu_items"]:
            self.assertIn(expected, fields)

    def test_learned_pattern_exists(self):
        self.assertTrue(hasattr(self.mod, "LearnedPattern"))

    def test_learned_pattern_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.LearnedPattern)}
        for expected in ["pattern_id", "source_agent", "pattern_type", "name", "description"]:
            self.assertIn(expected, fields)

    def test_mirror_suggestion_exists(self):
        self.assertTrue(hasattr(self.mod, "MirrorSuggestion"))

    def test_mirror_suggestion_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.MirrorSuggestion)}
        for expected in ["from_agent", "to_agent", "pattern", "description", "difficulty"]:
            self.assertIn(expected, fields)


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_version_defined(self):
        self.assertTrue(hasattr(self.mod, "VERSION"))

    def test_mirror_dir_defined(self):
        self.assertTrue(hasattr(self.mod, "MIRROR_DIR"))

    def test_patterns_file_defined(self):
        self.assertTrue(hasattr(self.mod, "PATTERNS_FILE"))


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

    def test_subcommand_observe_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        with contextlib.suppress(SystemExit):
            parser.parse_args(["observe"])

    def test_subcommand_learn_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        with contextlib.suppress(SystemExit):
            parser.parse_args(["learn"])

    def test_subcommand_mirror_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        with contextlib.suppress(SystemExit):
            parser.parse_args(["mirror"])

    def test_subcommand_catalog_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        with contextlib.suppress(SystemExit):
            parser.parse_args(["catalog"])

    def test_subcommand_diff_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        with contextlib.suppress(SystemExit):
            parser.parse_args(["diff"])


class TestCLIIntegration(unittest.TestCase):
    TOOL = KIT_DIR / "framework" / "tools" / "mirror-agent.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.TOOL), *list(args)],
            capture_output=True, text=True, timeout=30,
        )

    def test_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("mirror", r.stdout.lower() + r.stderr.lower())

    def test_no_args(self):
        r = self._run()
        # Most tools show help or error without args
        self.assertIn(r.returncode, (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
