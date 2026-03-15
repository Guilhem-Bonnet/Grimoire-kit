"""Tests for agent-build.py — Agent Build System."""
from __future__ import annotations

import dataclasses
import importlib.util
import sys
import unittest
from pathlib import Path

TOOL_PATH = Path(__file__).resolve().parent.parent / "framework" / "tools" / "agent-build.py"


def _import_mod():
    spec = importlib.util.spec_from_file_location("agent_build", TOOL_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["agent_build"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_version_constant(self):
        self.assertTrue(hasattr(self.mod, "AGENT_BUILD_VERSION"))
        self.assertIsInstance(self.mod.AGENT_BUILD_VERSION, str)

    def test_build_dir_constant(self):
        self.assertTrue(hasattr(self.mod, "BUILD_DIR"))


class TestDependencyCheck(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_dataclass_exists(self):
        self.assertTrue(hasattr(self.mod, "DependencyCheck"))
        self.assertTrue(dataclasses.is_dataclass(self.mod.DependencyCheck))

    def test_fields(self):
        fields = {f.name for f in dataclasses.fields(self.mod.DependencyCheck)}
        for name in ("dep_type", "name", "required", "resolved", "location", "message"):
            self.assertIn(name, fields)


class TestValidationResult(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_dataclass_exists(self):
        self.assertTrue(hasattr(self.mod, "ValidationResult"))
        self.assertTrue(dataclasses.is_dataclass(self.mod.ValidationResult))

    def test_fields(self):
        fields = {f.name for f in dataclasses.fields(self.mod.ValidationResult)}
        for name in ("agent_file", "agent_name", "valid", "errors", "warnings", "checks"):
            self.assertIn(name, fields)


class TestBuildResult(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_dataclass_exists(self):
        self.assertTrue(hasattr(self.mod, "BuildResult"))
        self.assertTrue(dataclasses.is_dataclass(self.mod.BuildResult))

    def test_fields(self):
        fields = {f.name for f in dataclasses.fields(self.mod.BuildResult)}
        for name in ("agent_file", "agent_name", "build_id", "timestamp", "validation",
                      "dependencies", "all_deps_resolved", "build_status"):
            self.assertIn(name, fields)


class TestCallables(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_parse_agent_file(self):
        self.assertTrue(callable(getattr(self.mod, "parse_agent_file", None)))

    def test_validate_agent(self):
        self.assertTrue(callable(getattr(self.mod, "validate_agent", None)))

    def test_resolve_dependencies(self):
        self.assertTrue(callable(getattr(self.mod, "resolve_dependencies", None)))

    def test_build_agent(self):
        self.assertTrue(callable(getattr(self.mod, "build_agent", None)))

    def test_main(self):
        self.assertTrue(callable(getattr(self.mod, "main", None)))


if __name__ == "__main__":
    unittest.main()
