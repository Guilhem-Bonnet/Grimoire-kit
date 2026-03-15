"""Tests for rnd_engine.py — R&D Engine."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

TOOL_PATH = Path(__file__).resolve().parent.parent / "framework" / "tools" / "rnd_engine.py"
CORE_PATH = Path(__file__).resolve().parent.parent / "framework" / "tools" / "rnd_core.py"
HARVEST_PATH = Path(__file__).resolve().parent.parent / "framework" / "tools" / "rnd_harvest.py"


def _import_core():
    spec = importlib.util.spec_from_file_location("rnd_core", CORE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["rnd_core"] = mod
    spec.loader.exec_module(mod)
    return mod


def _import_harvest():
    _import_core()
    spec = importlib.util.spec_from_file_location("rnd_harvest", HARVEST_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["rnd_harvest"] = mod
    spec.loader.exec_module(mod)
    return mod


def _import_mod():
    # rnd_engine imports from rnd_core and rnd_harvest — ensure both are loaded
    _import_harvest()
    spec = importlib.util.spec_from_file_location("rnd_engine", TOOL_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["rnd_engine"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestCallables(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_evaluate(self):
        self.assertTrue(callable(getattr(self.mod, "evaluate", None)))

    def test_challenge(self):
        self.assertTrue(callable(getattr(self.mod, "challenge", None)))

    def test_simulate(self):
        self.assertTrue(callable(getattr(self.mod, "simulate", None)))

    def test_check_quality_gates(self):
        self.assertTrue(callable(getattr(self.mod, "check_quality_gates", None)))

    def test_select_winners(self):
        self.assertTrue(callable(getattr(self.mod, "select_winners", None)))

    def test_update_policy(self):
        self.assertTrue(callable(getattr(self.mod, "update_policy", None)))

    def test_check_convergence(self):
        self.assertTrue(callable(getattr(self.mod, "check_convergence", None)))


if __name__ == "__main__":
    unittest.main()
