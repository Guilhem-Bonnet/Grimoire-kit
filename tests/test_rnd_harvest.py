"""Tests for rnd_harvest.py — R&D Harvest."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

TOOL_PATH = Path(__file__).resolve().parent.parent / "framework" / "tools" / "rnd_harvest.py"
CORE_PATH = Path(__file__).resolve().parent.parent / "framework" / "tools" / "rnd_core.py"


def _import_core():
    spec = importlib.util.spec_from_file_location("rnd_core", CORE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["rnd_core"] = mod
    spec.loader.exec_module(mod)
    return mod


def _import_mod():
    # rnd_harvest imports from rnd_core — ensure it's loaded first
    _import_core()
    spec = importlib.util.spec_from_file_location("rnd_harvest", TOOL_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["rnd_harvest"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestCallables(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_harvest_from_dream(self):
        self.assertTrue(callable(getattr(self.mod, "_harvest_from_dream", None)))

    def test_harvest_from_oracle(self):
        self.assertTrue(callable(getattr(self.mod, "_harvest_from_oracle", None)))

    def test_harvest_from_early_warning(self):
        self.assertTrue(callable(getattr(self.mod, "_harvest_from_early_warning", None)))

    def test_harvest_from_harmony(self):
        self.assertTrue(callable(getattr(self.mod, "_harvest_from_harmony", None)))

    def test_harvest_from_incubator(self):
        self.assertTrue(callable(getattr(self.mod, "_harvest_from_incubator", None)))

    def test_harvest_from_stigmergy(self):
        self.assertTrue(callable(getattr(self.mod, "_harvest_from_stigmergy", None)))

    def test_harvest_from_project_scan(self):
        self.assertTrue(callable(getattr(self.mod, "_harvest_from_project_scan", None)))

    def test_classify_domain(self):
        self.assertTrue(callable(getattr(self.mod, "_classify_domain", None)))

    def test_classify_action(self):
        self.assertTrue(callable(getattr(self.mod, "_classify_action", None)))

    def test_generate_synthetic_ideas(self):
        self.assertTrue(callable(getattr(self.mod, "_generate_synthetic_ideas", None)))

    def test_mutate_past_winners(self):
        self.assertTrue(callable(getattr(self.mod, "_mutate_past_winners", None)))

    def test_gap_driven_ideas(self):
        self.assertTrue(callable(getattr(self.mod, "_gap_driven_ideas", None)))


if __name__ == "__main__":
    unittest.main()
