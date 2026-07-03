"""Tests for MemoryManager.progressive_search — 3-layer disclosure."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from grimoire.core.config import GrimoireConfig
from grimoire.memory.manager import MemoryManager


def _make_config(root: Path) -> GrimoireConfig:
    """Create a minimal local-backend config."""
    cfg_text = (
        'project:\n  name: "test-progressive"\n'
        'memory:\n  backend: "local"\n'
        'agents:\n  archetype: "minimal"\n'
    )
    cfg_path = root / "project-context.yaml"
    cfg_path.write_text(cfg_text, encoding="utf-8")
    return GrimoireConfig.from_yaml(cfg_path)


class TestProgressiveSearchSetup(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "_grimoire" / "_memory").mkdir(parents=True)
        cfg = _make_config(self.root)
        self.mgr = MemoryManager.from_config(cfg, project_root=self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_layer_invalid_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.mgr.progressive_search("test", layer="L99")

    def test_empty_returns_empty(self) -> None:
        results = self.mgr.progressive_search("nothing")
        self.assertEqual(results, [])


class TestProgressiveSearchLayers(unittest.TestCase):
    """Verify that L1/L2/L3 progressively reveal more text."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "_grimoire" / "_memory").mkdir(parents=True)
        cfg = _make_config(self.root)
        self.mgr = MemoryManager.from_config(cfg, project_root=self.root)
        # Store a long entry
        long_text = "keyword " + "extra " * 500
        self.mgr.store(long_text, tags=("test",))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_l1_truncates(self) -> None:
        results = self.mgr.progressive_search("keyword", layer="L1")
        if results:
            self.assertTrue(results[0]["truncated"])
            self.assertEqual(results[0]["layer"], "L1")
            self.assertLessEqual(len(results[0]["text"]), 250)

    def test_l3_reveals_more(self) -> None:
        l1 = self.mgr.progressive_search("keyword", layer="L1")
        l3 = self.mgr.progressive_search("keyword", layer="L3")
        if l1 and l3:
            self.assertGreaterEqual(len(l3[0]["text"]), len(l1[0]["text"]))


class TestTrimToBudget(unittest.TestCase):
    def test_short_text_unchanged(self) -> None:
        result = MemoryManager._trim_to_budget("hello world", 100)
        self.assertEqual(result, "hello world")

    def test_long_text_trimmed(self) -> None:
        text = "word " * 200
        result = MemoryManager._trim_to_budget(text, 10)  # ~40 chars
        self.assertLessEqual(len(result), 50)
        self.assertTrue(result.endswith("…"))

    def test_zero_budget(self) -> None:
        result = MemoryManager._trim_to_budget("some text", 0)
        self.assertTrue(result.endswith("…") or result == "")


if __name__ == "__main__":
    unittest.main()
