"""Tests for procedural-memory.py — D10 Procedural Memory Layer 4."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "framework" / "tools" / "procedural-memory.py"


def _load():
    mod_name = "procedural_memory_mod"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


pm = _load()


class TestVersion(unittest.TestCase):
    def test_version(self):
        self.assertTrue(pm.PROCEDURAL_MEMORY_VERSION)


class TestStorage(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def test_load_empty(self):
        patterns = pm._load_patterns(Path(self.tmpdir))
        self.assertEqual(patterns, [])

    def test_save_and_load(self):
        root = Path(self.tmpdir)
        data = [{"id": 1, "task_type": "test", "pattern": "p"}]
        pm._save_patterns(root, data)
        loaded = pm._load_patterns(root)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["task_type"], "test")


class TestRecordPattern(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.root = Path(self.tmpdir)

    def test_create_new(self):
        result = pm.record_pattern(self.root, "bug-fix", "reproduce then fix")
        self.assertEqual(result["status"], "created")
        self.assertEqual(result["id"], 1)

    def test_increment_existing(self):
        pm.record_pattern(self.root, "bug-fix", "reproduce then fix")
        result = pm.record_pattern(self.root, "bug-fix", "reproduce then fix")
        self.assertEqual(result["status"], "incremented")
        self.assertEqual(result["success_count"], 2)

    def test_tags_merged(self):
        pm.record_pattern(self.root, "refactor", "split", tags=["python"])
        pm.record_pattern(self.root, "refactor", "split", tags=["testing"])
        patterns = pm._load_patterns(self.root)
        self.assertIn("python", patterns[0]["tags"])
        self.assertIn("testing", patterns[0]["tags"])


class TestLookupPatterns(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.root = Path(self.tmpdir)
        pm.record_pattern(self.root, "bug-fix", "reproduce then fix", tags=["python"])
        pm.record_pattern(self.root, "refactor", "extract module", tags=["python"])
        pm.record_pattern(self.root, "feature", "TDD approach", tags=["testing"])

    def test_lookup_by_type(self):
        results = pm.lookup_patterns(self.root, "bug-fix")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["task_type"], "bug-fix")

    def test_lookup_substring(self):
        results = pm.lookup_patterns(self.root, "fix")
        self.assertEqual(len(results), 1)

    def test_lookup_with_tags(self):
        results = pm.lookup_patterns(self.root, "refactor", tags=["python"])
        self.assertEqual(len(results), 1)

    def test_lookup_no_match(self):
        results = pm.lookup_patterns(self.root, "deploy")
        self.assertEqual(len(results), 0)

    def test_lookup_limit(self):
        for i in range(20):
            pm.record_pattern(self.root, "many", f"pattern-{i}")
        results = pm.lookup_patterns(self.root, "many", limit=5)
        self.assertEqual(len(results), 5)


class TestStats(unittest.TestCase):
    def test_empty_stats(self):
        tmpdir = tempfile.mkdtemp()
        stats = pm.get_stats(Path(tmpdir))
        self.assertEqual(stats["total"], 0)

    def test_populated_stats(self):
        tmpdir = tempfile.mkdtemp()
        root = Path(tmpdir)
        pm.record_pattern(root, "bug-fix", "p1", tags=["python"])
        pm.record_pattern(root, "bug-fix", "p2", tags=["python", "testing"])
        pm.record_pattern(root, "feature", "p3")
        stats = pm.get_stats(root)
        self.assertEqual(stats["total"], 3)
        self.assertIn("bug-fix", stats["task_types"])
        self.assertIn("python", stats["top_tags"])


class TestMcpInterface(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def test_mcp_record(self):
        result = pm.mcp_procedural_record(
            "test-task", "test pattern", project_root=self.tmpdir)
        self.assertEqual(result["status"], "created")

    def test_mcp_lookup(self):
        pm.record_pattern(Path(self.tmpdir), "lookup-test", "pattern A")
        result = pm.mcp_procedural_lookup("lookup-test", project_root=self.tmpdir)
        self.assertEqual(result["count"], 1)


if __name__ == "__main__":
    unittest.main()
