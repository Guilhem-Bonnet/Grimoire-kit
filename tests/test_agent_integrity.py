"""Tests for agent-integrity.py — Agent Integrity System."""
from __future__ import annotations

import dataclasses
import importlib.util
import sys
import unittest
from pathlib import Path

TOOL_PATH = Path(__file__).resolve().parent.parent / "framework" / "tools" / "agent-integrity.py"


def _import_mod():
    spec = importlib.util.spec_from_file_location("agent_integrity", TOOL_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["agent_integrity"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_version_constant(self):
        self.assertTrue(hasattr(self.mod, "VERSION"))
        self.assertIsInstance(self.mod.VERSION, str)

    def test_integrity_dir(self):
        self.assertTrue(hasattr(self.mod, "INTEGRITY_DIR"))

    def test_snapshot_file(self):
        self.assertTrue(hasattr(self.mod, "SNAPSHOT_FILE"))

    def test_agent_patterns(self):
        self.assertTrue(hasattr(self.mod, "AGENT_PATTERNS"))
        self.assertIsInstance(self.mod.AGENT_PATTERNS, (list, tuple))


class TestIntegrityReport(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_dataclass_exists(self):
        self.assertTrue(hasattr(self.mod, "IntegrityReport"))
        self.assertTrue(dataclasses.is_dataclass(self.mod.IntegrityReport))

    def test_fields(self):
        fields = {f.name for f in dataclasses.fields(self.mod.IntegrityReport)}
        for name in ("status", "total_files", "modified", "added", "removed", "timestamp"):
            self.assertIn(name, fields)


class TestCallables(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_sha256(self):
        self.assertTrue(callable(getattr(self.mod, "_sha256", None)))

    def test_collect_agent_files(self):
        self.assertTrue(callable(getattr(self.mod, "_collect_agent_files", None)))

    def test_snapshot(self):
        self.assertTrue(callable(getattr(self.mod, "snapshot", None)))

    def test_verify(self):
        self.assertTrue(callable(getattr(self.mod, "verify", None)))

    def test_main(self):
        self.assertTrue(callable(getattr(self.mod, "main", None)))


class TestSha256(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_hash_consistency(self):
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("hello grimoire")
            f.flush()
            h1 = self.mod._sha256(Path(f.name))
            h2 = self.mod._sha256(Path(f.name))
        self.assertEqual(h1, h2)
        self.assertIsInstance(h1, str)
        self.assertEqual(len(h1), 64)  # SHA-256 hex digest


if __name__ == "__main__":
    unittest.main()
