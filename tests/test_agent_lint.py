"""Tests for agent-lint.py — Agent Linter."""
from __future__ import annotations

import dataclasses
import importlib.util
import sys
import unittest
from pathlib import Path

TOOL_PATH = Path(__file__).resolve().parent.parent / "framework" / "tools" / "agent-lint.py"


def _import_mod():
    spec = importlib.util.spec_from_file_location("agent_lint", TOOL_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["agent_lint"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_severity_class(self):
        self.assertTrue(hasattr(self.mod, "Severity"))


class TestFinding(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_dataclass_exists(self):
        self.assertTrue(hasattr(self.mod, "Finding"))
        self.assertTrue(dataclasses.is_dataclass(self.mod.Finding))

    def test_fields(self):
        fields = {f.name for f in dataclasses.fields(self.mod.Finding)}
        for name in ("severity", "rule", "message"):
            self.assertIn(name, fields)


class TestLintReport(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_dataclass_exists(self):
        self.assertTrue(hasattr(self.mod, "LintReport"))
        self.assertTrue(dataclasses.is_dataclass(self.mod.LintReport))


class TestCallables(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_discover_agents(self):
        self.assertTrue(callable(getattr(self.mod, "discover_agents", None)))

    def test_main(self):
        self.assertTrue(callable(getattr(self.mod, "main", None)))


if __name__ == "__main__":
    unittest.main()
