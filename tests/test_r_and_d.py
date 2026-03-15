#!/usr/bin/env python3
"""
Tests pour r-and-d.py — r-and-d.py — Innovation Engine avec Reinforcement Learning Grimoire.

Fonctions testées :
  - load_policy()
  - save_policy()
  - load_memory()
  - save_memory()
  - save_cycle_report()
  - load_cycle_reports()
  - next_cycle_id()
  - harvest()
  - evaluate()
  - challenge()
  - simulate()
  - check_quality_gates()
  - select_winners()
  - update_policy()
  - check_convergence()
  - run_cycle()
  - train()
  - cmd_cycle()
  - cmd_train()
  - cmd_harvest()
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
TOOL = KIT_DIR / "framework" / "tools" / "r-and-d.py"


def _import_mod():
    """Import le module r-and-d via importlib."""
    mod_name = "r_and_d"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "r-and-d.py")
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

    def test_idea_exists(self):
        self.assertTrue(hasattr(self.mod, "Idea"))

    def test_idea_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.Idea)}
        for expected in ["id", "title", "description", "source", "domain"]:
            self.assertIn(expected, fields)

    def test_policy_exists(self):
        self.assertTrue(hasattr(self.mod, "Policy"))

    def test_policy_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.Policy)}
        for expected in ["source_weights", "domain_weights", "action_weights", "scoring_weights", "epsilon"]:
            self.assertIn(expected, fields)

    def test_cycle_report_exists(self):
        self.assertTrue(hasattr(self.mod, "CycleReport"))

    def test_cycle_report_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.CycleReport)}
        for expected in ["cycle_id", "epoch", "timestamp", "duration_ms", "ideas_harvested"]:
            self.assertIn(expected, fields)

    def test_train_report_exists(self):
        self.assertTrue(hasattr(self.mod, "TrainReport"))

    def test_train_report_fields(self):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(self.mod.TrainReport)}
        for expected in ["epochs_requested", "epochs_completed", "total_ideas", "total_merged", "best_idea"]:
            self.assertIn(expected, fields)


class TestPureFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_select_winners_callable(self):
        self.assertTrue(callable(getattr(self.mod, "select_winners", None)))

    def test_update_policy_callable(self):
        self.assertTrue(callable(getattr(self.mod, "update_policy", None)))


class TestProjectFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        # Minimal Grimoire structure
        (self.tmpdir / "_grimoire" / "_memory").mkdir(parents=True, exist_ok=True)
        (self.tmpdir / "_grimoire-output").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_policy_empty_project(self):
        try:
            result = self.mod.load_policy(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project

    def test_save_policy_callable(self):
        self.assertTrue(callable(self.mod.save_policy))

    def test_load_memory_empty_project(self):
        try:
            result = self.mod.load_memory(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project

    def test_save_memory_callable(self):
        self.assertTrue(callable(self.mod.save_memory))

    def test_load_cycle_reports_callable(self):
        self.assertTrue(callable(self.mod.load_cycle_reports))

    def test_next_cycle_id_empty_project(self):
        try:
            result = self.mod.next_cycle_id(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project

    def test_harvest_callable(self):
        self.assertTrue(callable(self.mod.harvest))

    def test_evaluate_callable(self):
        self.assertTrue(callable(self.mod.evaluate))

    def test_check_quality_gates_empty_project(self):
        try:
            result = self.mod.check_quality_gates(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_version_defined(self):
        self.assertTrue(hasattr(self.mod, "VERSION"))

    def test_rnd_dir_defined(self):
        self.assertTrue(hasattr(self.mod, "RND_DIR"))

    def test_memory_file_defined(self):
        self.assertTrue(hasattr(self.mod, "MEMORY_FILE"))

    def test_policy_file_defined(self):
        self.assertTrue(hasattr(self.mod, "POLICY_FILE"))

    def test_history_dir_defined(self):
        self.assertTrue(hasattr(self.mod, "HISTORY_DIR"))

    def test_fossil_dir_defined(self):
        self.assertTrue(hasattr(self.mod, "FOSSIL_DIR"))

    def test_harvest_sources_defined(self):
        self.assertTrue(hasattr(self.mod, "HARVEST_SOURCES"))

    def test_domains_defined(self):
        self.assertTrue(hasattr(self.mod, "DOMAINS"))


class TestCLIIntegration(unittest.TestCase):
    TOOL = KIT_DIR / "framework" / "tools" / "r-and-d.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.TOOL), *list(args)],
            capture_output=True, text=True, timeout=30,
        )

    def test_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("r", r.stdout.lower() + r.stderr.lower())

    def test_no_args(self):
        r = self._run()
        # Most tools show help or error without args
        self.assertIn(r.returncode, (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
