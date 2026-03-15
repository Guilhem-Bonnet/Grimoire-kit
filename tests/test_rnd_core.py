"""Tests for rnd_core.py — R&D Core Data Structures."""
from __future__ import annotations

import dataclasses
import importlib.util
import sys
import unittest
from pathlib import Path

TOOL_PATH = Path(__file__).resolve().parent.parent / "framework" / "tools" / "rnd_core.py"


def _import_mod():
    spec = importlib.util.spec_from_file_location("rnd_core", TOOL_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["rnd_core"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_version(self):
        self.assertTrue(hasattr(self.mod, "VERSION"))

    def test_rnd_dir(self):
        self.assertTrue(hasattr(self.mod, "RND_DIR"))

    def test_domains(self):
        self.assertTrue(hasattr(self.mod, "DOMAINS"))
        self.assertIsInstance(self.mod.DOMAINS, (list, tuple))
        self.assertGreater(len(self.mod.DOMAINS), 0)

    def test_actions(self):
        self.assertTrue(hasattr(self.mod, "ACTIONS"))
        self.assertIsInstance(self.mod.ACTIONS, (list, tuple))
        self.assertGreater(len(self.mod.ACTIONS), 0)

    def test_harvest_sources(self):
        self.assertTrue(hasattr(self.mod, "HARVEST_SOURCES"))

    def test_scoring_dims(self):
        self.assertTrue(hasattr(self.mod, "SCORING_DIMS"))

    def test_thresholds(self):
        self.assertTrue(hasattr(self.mod, "GO_THRESHOLD"))
        self.assertTrue(hasattr(self.mod, "CONDITIONAL_THRESHOLD"))
        self.assertGreater(self.mod.GO_THRESHOLD, self.mod.CONDITIONAL_THRESHOLD)

    def test_budget_and_epochs(self):
        self.assertTrue(hasattr(self.mod, "DEFAULT_BUDGET"))
        self.assertTrue(hasattr(self.mod, "DEFAULT_EPOCHS"))
        self.assertGreater(self.mod.DEFAULT_BUDGET, 0)
        self.assertGreater(self.mod.DEFAULT_EPOCHS, 0)


class TestIdeaDataclass(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_dataclass_exists(self):
        self.assertTrue(hasattr(self.mod, "Idea"))
        self.assertTrue(dataclasses.is_dataclass(self.mod.Idea))

    def test_key_fields(self):
        fields = {f.name for f in dataclasses.fields(self.mod.Idea)}
        for name in ("id", "title", "description", "source", "domain", "action",
                      "scores", "total_score", "implemented", "merged"):
            self.assertIn(name, fields)


class TestPolicyDataclass(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_dataclass_exists(self):
        self.assertTrue(hasattr(self.mod, "Policy"))
        self.assertTrue(dataclasses.is_dataclass(self.mod.Policy))

    def test_key_fields(self):
        fields = {f.name for f in dataclasses.fields(self.mod.Policy)}
        for name in ("source_weights", "domain_weights", "action_weights",
                      "scoring_weights", "epsilon", "learning_rate"):
            self.assertIn(name, fields)


class TestCycleReport(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_dataclass_exists(self):
        self.assertTrue(hasattr(self.mod, "CycleReport"))
        self.assertTrue(dataclasses.is_dataclass(self.mod.CycleReport))

    def test_key_fields(self):
        fields = {f.name for f in dataclasses.fields(self.mod.CycleReport)}
        for name in ("cycle_id", "epoch", "timestamp", "ideas_harvested",
                      "ideas_evaluated", "ideas_merged", "avg_reward"):
            self.assertIn(name, fields)


class TestTrainReport(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_dataclass_exists(self):
        self.assertTrue(hasattr(self.mod, "TrainReport"))
        self.assertTrue(dataclasses.is_dataclass(self.mod.TrainReport))

    def test_key_fields(self):
        fields = {f.name for f in dataclasses.fields(self.mod.TrainReport)}
        for name in ("epochs_requested", "epochs_completed", "total_ideas",
                      "total_merged", "best_idea", "reward_curve"):
            self.assertIn(name, fields)


class TestCoreCallables(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_load_policy(self):
        self.assertTrue(callable(getattr(self.mod, "load_policy", None)))

    def test_save_policy(self):
        self.assertTrue(callable(getattr(self.mod, "save_policy", None)))

    def test_load_memory(self):
        self.assertTrue(callable(getattr(self.mod, "load_memory", None)))

    def test_save_memory(self):
        self.assertTrue(callable(getattr(self.mod, "save_memory", None)))

    def test_next_cycle_id(self):
        self.assertTrue(callable(getattr(self.mod, "next_cycle_id", None)))

    def test_save_cycle_report(self):
        self.assertTrue(callable(getattr(self.mod, "save_cycle_report", None)))

    def test_load_cycle_reports(self):
        self.assertTrue(callable(getattr(self.mod, "load_cycle_reports", None)))


if __name__ == "__main__":
    unittest.main()
