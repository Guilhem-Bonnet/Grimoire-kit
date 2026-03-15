"""Tests for agent-test.py — Agent Test System."""
from __future__ import annotations

import dataclasses
import importlib.util
import sys
import unittest
from pathlib import Path

TOOL_PATH = Path(__file__).resolve().parent.parent / "framework" / "tools" / "agent-test.py"


def _import_mod():
    spec = importlib.util.spec_from_file_location("agent_test", TOOL_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["agent_test"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_version_constant(self):
        self.assertTrue(hasattr(self.mod, "AGENT_TEST_VERSION"))
        self.assertIsInstance(self.mod.AGENT_TEST_VERSION, str)

    def test_test_dir(self):
        self.assertTrue(hasattr(self.mod, "TEST_DIR"))

    def test_history_file(self):
        self.assertTrue(hasattr(self.mod, "HISTORY_FILE"))


class TestCaseDataclass(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_dataclass_exists(self):
        self.assertTrue(hasattr(self.mod, "TestCase"))
        self.assertTrue(dataclasses.is_dataclass(self.mod.TestCase))

    def test_fields(self):
        fields = {f.name for f in dataclasses.fields(self.mod.TestCase)}
        for name in ("test_id", "category", "name", "description", "severity",
                      "prompt", "expected_traits", "forbidden_traits"):
            self.assertIn(name, fields)


class TestResultDataclass(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_dataclass_exists(self):
        self.assertTrue(hasattr(self.mod, "TestResult"))
        self.assertTrue(dataclasses.is_dataclass(self.mod.TestResult))

    def test_fields(self):
        fields = {f.name for f in dataclasses.fields(self.mod.TestResult)}
        for name in ("test_id", "category", "name", "passed", "score", "details", "evidence", "feedback"):
            self.assertIn(name, fields)


class TestSuiteResultDataclass(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_dataclass_exists(self):
        self.assertTrue(hasattr(self.mod, "TestSuiteResult"))
        self.assertTrue(dataclasses.is_dataclass(self.mod.TestSuiteResult))

    def test_fields(self):
        fields = {f.name for f in dataclasses.fields(self.mod.TestSuiteResult)}
        for name in ("suite_id", "agent_name", "agent_file", "timestamp", "suite_type",
                      "total_tests", "passed", "failed", "score", "grade", "results"):
            self.assertIn(name, fields)


class TestBenchResult(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_dataclass_exists(self):
        self.assertTrue(hasattr(self.mod, "BenchResult"))
        self.assertTrue(dataclasses.is_dataclass(self.mod.BenchResult))


class TestCallables(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_parse_agent_for_tests(self):
        self.assertTrue(callable(getattr(self.mod, "parse_agent_for_tests", None)))

    def test_generate_test_suite(self):
        self.assertTrue(callable(getattr(self.mod, "generate_test_suite", None)))

    def test_evaluate_test_static(self):
        self.assertTrue(callable(getattr(self.mod, "evaluate_test_static", None)))

    def test_run_test_suite(self):
        self.assertTrue(callable(getattr(self.mod, "run_test_suite", None)))

    def test_run_benchmark(self):
        self.assertTrue(callable(getattr(self.mod, "run_benchmark", None)))

    def test_main(self):
        self.assertTrue(callable(getattr(self.mod, "main", None)))


if __name__ == "__main__":
    unittest.main()
