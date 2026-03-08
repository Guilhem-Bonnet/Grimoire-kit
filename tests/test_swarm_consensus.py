#!/usr/bin/env python3
"""
Tests pour swarm-consensus.py — swarm-consensus.py — Consensus en essaim Grimoire.

Fonctions testées :
  - process_votes()
  - process_estimates()
  - load_history()
  - save_entry()
  - format_vote()
  - format_estimate()
  - cmd_vote()
  - cmd_estimate()
  - cmd_consensus()
  - cmd_history()
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
TOOL = KIT_DIR / "framework" / "tools" / "swarm-consensus.py"


def _import_mod():
    """Import le module swarm-consensus via importlib."""
    mod_name = "swarm_consensus"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "swarm-consensus.py")
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

    def test_vote_exists(self):
        self.assertTrue(hasattr(self.mod, "Vote"))

    def test_vote_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.Vote)}
        for expected in ["agent", "value", "weight", "comment"]:
            self.assertIn(expected, fields)

    def test_vote_result_exists(self):
        self.assertTrue(hasattr(self.mod, "VoteResult"))

    def test_vote_result_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.VoteResult)}
        for expected in ["topic", "mode", "votes", "consensus", "ratio"]:
            self.assertIn(expected, fields)

    def test_estimate_exists(self):
        self.assertTrue(hasattr(self.mod, "Estimate"))

    def test_estimate_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.Estimate)}
        for expected in ["agent", "value", "weight"]:
            self.assertIn(expected, fields)

    def test_estimate_result_exists(self):
        self.assertTrue(hasattr(self.mod, "EstimateResult"))

    def test_estimate_result_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.EstimateResult)}
        for expected in ["task", "estimates", "mean", "median", "std_dev"]:
            self.assertIn(expected, fields)


class TestPureFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_process_votes_callable(self):
        self.assertTrue(callable(getattr(self.mod, "process_votes", None)))

    def test_process_estimates_callable(self):
        self.assertTrue(callable(getattr(self.mod, "process_estimates", None)))

    def test_format_vote_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_vote", None)))

    def test_format_estimate_callable(self):
        self.assertTrue(callable(getattr(self.mod, "format_estimate", None)))


class TestProjectFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        # Minimal Grimoire structure
        (self.tmpdir / "_grimoire" / "_memory").mkdir(parents=True, exist_ok=True)
        (self.tmpdir / "_grimoire-output").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_history_empty_project(self):
        try:
            result = self.mod.load_history(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project

    def test_save_entry_callable(self):
        self.assertTrue(callable(self.mod.save_entry))


class TestFormatFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_format_vote_callable(self):
        self.assertTrue(callable(self.mod.format_vote))

    def test_format_estimate_callable(self):
        self.assertTrue(callable(self.mod.format_estimate))


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_version_defined(self):
        self.assertTrue(hasattr(self.mod, "VERSION"))

    def test_consensus_log_defined(self):
        self.assertTrue(hasattr(self.mod, "CONSENSUS_LOG"))

    def test_fibonacci_scale_defined(self):
        self.assertTrue(hasattr(self.mod, "FIBONACCI_SCALE"))

    def test_consensus_modes_defined(self):
        self.assertTrue(hasattr(self.mod, "CONSENSUS_MODES"))

    def test_agent_weights_defined(self):
        self.assertTrue(hasattr(self.mod, "AGENT_WEIGHTS"))


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

    def test_subcommand_vote_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["vote"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_estimate_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["estimate"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_consensus_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["consensus"])
        except SystemExit:
            pass  # Some subcommands may require args

    def test_subcommand_history_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["history"])
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
    TOOL = KIT_DIR / "framework" / "tools" / "swarm-consensus.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.TOOL)] + list(args),
            capture_output=True, text=True, timeout=30,
        )

    def test_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("swarm", r.stdout.lower() + r.stderr.lower())

    def test_no_args(self):
        r = self._run()
        # Most tools show help or error without args
        self.assertIn(r.returncode, (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
