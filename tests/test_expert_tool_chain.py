"""Tests for expert-tool-chain.py — Expert Tool Chain."""
from __future__ import annotations

import dataclasses
import importlib.util
import sys
import unittest
from pathlib import Path

TOOL_PATH = Path(__file__).resolve().parent.parent / "framework" / "tools" / "expert-tool-chain.py"


def _import_mod():
    spec = importlib.util.spec_from_file_location("expert_tool_chain", TOOL_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["expert_tool_chain"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_version_constant(self):
        self.assertTrue(hasattr(self.mod, "ETC_VERSION"))
        self.assertIsInstance(self.mod.ETC_VERSION, str)

    def test_etc_dir(self):
        self.assertTrue(hasattr(self.mod, "ETC_DIR"))

    def test_max_iterations(self):
        self.assertTrue(hasattr(self.mod, "MAX_ITERATIONS"))
        self.assertIsInstance(self.mod.MAX_ITERATIONS, int)
        self.assertGreater(self.mod.MAX_ITERATIONS, 0)

    def test_default_acceptance_threshold(self):
        self.assertTrue(hasattr(self.mod, "DEFAULT_ACCEPTANCE_THRESHOLD"))

    def test_expertise_profiles(self):
        self.assertTrue(hasattr(self.mod, "EXPERTISE_PROFILES"))
        self.assertIsInstance(self.mod.EXPERTISE_PROFILES, dict)
        self.assertGreater(len(self.mod.EXPERTISE_PROFILES), 0)


class TestIterationRecord(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_dataclass_exists(self):
        self.assertTrue(hasattr(self.mod, "IterationRecord"))
        self.assertTrue(dataclasses.is_dataclass(self.mod.IterationRecord))

    def test_fields(self):
        fields = {f.name for f in dataclasses.fields(self.mod.IterationRecord)}
        for name in ("iteration", "timestamp", "action_taken", "duration_seconds"):
            self.assertIn(name, fields)


class TestETCExecution(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_dataclass_exists(self):
        self.assertTrue(hasattr(self.mod, "ETCExecution"))
        self.assertTrue(dataclasses.is_dataclass(self.mod.ETCExecution))

    def test_fields(self):
        fields = {f.name for f in dataclasses.fields(self.mod.ETCExecution)}
        for name in ("execution_id", "profile", "brief", "status", "iterations"):
            self.assertIn(name, fields)


class TestCallables(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_get_expertise_context(self):
        self.assertTrue(callable(getattr(self.mod, "get_expertise_context", None)))

    def test_build_creation_prompt(self):
        self.assertTrue(callable(getattr(self.mod, "build_creation_prompt", None)))

    def test_plan_execution(self):
        self.assertTrue(callable(getattr(self.mod, "plan_execution", None)))

    def test_load_history(self):
        self.assertTrue(callable(getattr(self.mod, "load_history", None)))


if __name__ == "__main__":
    unittest.main()
