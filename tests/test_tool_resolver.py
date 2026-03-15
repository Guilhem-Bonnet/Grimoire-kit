"""Tests for tool-resolver.py — Tool Resolver."""
from __future__ import annotations

import dataclasses
import importlib.util
import sys
import unittest
from pathlib import Path

TOOL_PATH = Path(__file__).resolve().parent.parent / "framework" / "tools" / "tool-resolver.py"


def _import_mod():
    spec = importlib.util.spec_from_file_location("tool_resolver", TOOL_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tool_resolver"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_version_constant(self):
        self.assertTrue(hasattr(self.mod, "TOOL_RESOLVER_VERSION"))
        self.assertIsInstance(self.mod.TOOL_RESOLVER_VERSION, str)

    def test_cache_dir(self):
        self.assertTrue(hasattr(self.mod, "CACHE_DIR"))

    def test_capability_catalog(self):
        self.assertTrue(hasattr(self.mod, "CAPABILITY_CATALOG"))
        self.assertIsInstance(self.mod.CAPABILITY_CATALOG, dict)
        self.assertGreater(len(self.mod.CAPABILITY_CATALOG), 0)

    def test_intent_patterns(self):
        self.assertTrue(hasattr(self.mod, "INTENT_PATTERNS"))
        self.assertIsInstance(self.mod.INTENT_PATTERNS, (list, dict))
        self.assertGreater(len(self.mod.INTENT_PATTERNS), 0)


class TestToolCandidate(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_dataclass_exists(self):
        self.assertTrue(hasattr(self.mod, "ToolCandidate"))
        self.assertTrue(dataclasses.is_dataclass(self.mod.ToolCandidate))

    def test_fields(self):
        fields = {f.name for f in dataclasses.fields(self.mod.ToolCandidate)}
        for name in ("provider_id", "provider_type", "name", "capability", "priority", "available"):
            self.assertIn(name, fields)


class TestProvisionAction(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_dataclass_exists(self):
        self.assertTrue(hasattr(self.mod, "ProvisionAction"))
        self.assertTrue(dataclasses.is_dataclass(self.mod.ProvisionAction))

    def test_fields(self):
        fields = {f.name for f in dataclasses.fields(self.mod.ProvisionAction)}
        for name in ("provider_id", "method", "safe", "requires_confirmation"):
            self.assertIn(name, fields)


class TestResolutionPlan(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_dataclass_exists(self):
        self.assertTrue(hasattr(self.mod, "ResolutionPlan"))
        self.assertTrue(dataclasses.is_dataclass(self.mod.ResolutionPlan))

    def test_fields(self):
        fields = {f.name for f in dataclasses.fields(self.mod.ResolutionPlan)}
        for name in ("intent", "timestamp", "matched_capabilities", "candidates", "recommended"):
            self.assertIn(name, fields)


class TestCallables(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_check_command(self):
        self.assertTrue(callable(getattr(self.mod, "_check_command", None)))

    def test_check_python_import(self):
        self.assertTrue(callable(getattr(self.mod, "_check_python_import", None)))

    def test_check_grimoire_tool(self):
        self.assertTrue(callable(getattr(self.mod, "_check_grimoire_tool", None)))

    def test_check_provider_availability(self):
        self.assertTrue(callable(getattr(self.mod, "check_provider_availability", None)))


if __name__ == "__main__":
    unittest.main()
