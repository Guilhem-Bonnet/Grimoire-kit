"""Tests for agent-watch.py — Agent Drift Monitor."""
from __future__ import annotations

import dataclasses
import importlib.util
import sys
import unittest
from pathlib import Path

TOOL_PATH = Path(__file__).resolve().parent.parent / "framework" / "tools" / "agent-watch.py"


def _import_mod():
    spec = importlib.util.spec_from_file_location("agent_watch", TOOL_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["agent_watch"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_version_constant(self):
        self.assertTrue(hasattr(self.mod, "AGENT_WATCH_VERSION"))
        self.assertIsInstance(self.mod.AGENT_WATCH_VERSION, str)

    def test_watch_dir(self):
        self.assertTrue(hasattr(self.mod, "WATCH_DIR"))

    def test_drift_thresholds(self):
        self.assertTrue(hasattr(self.mod, "DRIFT_THRESHOLD_LOW"))
        self.assertTrue(hasattr(self.mod, "DRIFT_THRESHOLD_MED"))
        self.assertTrue(hasattr(self.mod, "DRIFT_THRESHOLD_HIGH"))
        self.assertLess(self.mod.DRIFT_THRESHOLD_LOW, self.mod.DRIFT_THRESHOLD_MED)
        self.assertLess(self.mod.DRIFT_THRESHOLD_MED, self.mod.DRIFT_THRESHOLD_HIGH)


class TestAgentFingerprint(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_dataclass_exists(self):
        self.assertTrue(hasattr(self.mod, "AgentFingerprint"))
        self.assertTrue(dataclasses.is_dataclass(self.mod.AgentFingerprint))

    def test_key_fields(self):
        fields = {f.name for f in dataclasses.fields(self.mod.AgentFingerprint)}
        for name in ("agent_name", "agent_file", "timestamp", "file_hash",
                      "has_persona", "num_capabilities", "total_lines"):
            self.assertIn(name, fields)


class TestDriftVector(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_dataclass_exists(self):
        self.assertTrue(hasattr(self.mod, "DriftVector"))
        self.assertTrue(dataclasses.is_dataclass(self.mod.DriftVector))

    def test_fields(self):
        fields = {f.name for f in dataclasses.fields(self.mod.DriftVector)}
        for name in ("dimension", "baseline_value", "current_value", "drift_score", "severity"):
            self.assertIn(name, fields)


class TestDriftReport(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_dataclass_exists(self):
        self.assertTrue(hasattr(self.mod, "DriftReport"))
        self.assertTrue(dataclasses.is_dataclass(self.mod.DriftReport))

    def test_fields(self):
        fields = {f.name for f in dataclasses.fields(self.mod.DriftReport)}
        for name in ("agent_name", "agent_file", "timestamp", "overall_drift", "severity", "vectors"):
            self.assertIn(name, fields)


class TestCallables(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_fingerprint_agent(self):
        self.assertTrue(callable(getattr(self.mod, "fingerprint_agent", None)))

    def test_save_baseline(self):
        self.assertTrue(callable(getattr(self.mod, "save_baseline", None)))

    def test_load_baseline(self):
        self.assertTrue(callable(getattr(self.mod, "load_baseline", None)))

    def test_compute_drift(self):
        self.assertTrue(callable(getattr(self.mod, "compute_drift", None)))

    def test_main(self):
        self.assertTrue(callable(getattr(self.mod, "main", None)))


if __name__ == "__main__":
    unittest.main()
