"""Tests for grimoire.tools.learnings — operational learnings accumulator."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from grimoire.tools.learnings import LearningEntry, Learnings


class TestLearningEntry(unittest.TestCase):
    def test_defaults(self) -> None:
        entry = LearningEntry(key="k", insight="i")
        self.assertEqual(entry.key, "k")
        self.assertEqual(entry.insight, "i")
        self.assertEqual(entry.confidence, 80)
        self.assertEqual(entry.source, "observed")
        self.assertEqual(entry.tags, ())
        self.assertEqual(entry.hit_count, 0)

    def test_roundtrip(self) -> None:
        entry = LearningEntry(
            key="pytest-flag",
            insight="Use -x to stop early",
            confidence=95,
            source="documented",
            skill="tdd",
            tags=("pytest", "ci"),
            timestamp="2025-01-01T00:00:00Z",
            hit_count=3,
        )
        d = entry.to_dict()
        restored = LearningEntry.from_dict(d)
        self.assertEqual(restored.key, entry.key)
        self.assertEqual(restored.insight, entry.insight)
        self.assertEqual(restored.confidence, entry.confidence)
        self.assertEqual(restored.source, entry.source)
        self.assertEqual(restored.skill, entry.skill)
        self.assertEqual(restored.tags, entry.tags)
        self.assertEqual(restored.hit_count, entry.hit_count)

    def test_from_dict_missing_fields(self) -> None:
        entry = LearningEntry.from_dict({"key": "k", "insight": "i"})
        self.assertEqual(entry.confidence, 80)
        self.assertEqual(entry.source, "observed")


class TestLearningsLog(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.learn = Learnings(project_root=self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_log_creates_file(self) -> None:
        entry = self.learn.log("test-key", "some insight")
        self.assertEqual(entry.key, "test-key")
        self.assertEqual(entry.insight, "some insight")
        jsonl = self.root / "_grimoire" / "_memory" / "learnings" / "operational.jsonl"
        self.assertTrue(jsonl.exists())

    def test_log_deduplicates_by_key(self) -> None:
        self.learn.log("dup-key", "first")
        self.learn.log("dup-key", "second")
        self.assertEqual(self.learn.count(), 1)
        results = self.learn.search("dup-key")
        self.assertEqual(results[0].insight, "second")

    def test_log_clamps_confidence(self) -> None:
        entry = self.learn.log("high", "test", confidence=150)
        self.assertEqual(entry.confidence, 100)
        entry = self.learn.log("low", "test", confidence=-10)
        self.assertEqual(entry.confidence, 0)


class TestLearningsSearch(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.learn = Learnings(project_root=self.root)
        self.learn.log("pytest-xdist", "Never use -n auto", confidence=90)
        self.learn.log("ruff-rule", "Enable bandit for security", confidence=70)
        self.learn.log("docker-build", "Use multi-stage builds", confidence=85, tags=("docker", "ci"))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_search_by_keyword(self) -> None:
        results = self.learn.search("pytest")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].key, "pytest-xdist")

    def test_search_no_match(self) -> None:
        results = self.learn.search("kubernetes")
        self.assertEqual(len(results), 0)

    def test_search_by_tag(self) -> None:
        results = self.learn.search("docker")
        self.assertTrue(any(r.key == "docker-build" for r in results))

    def test_search_respects_limit(self) -> None:
        results = self.learn.search("e", limit=1)  # matches everything
        self.assertEqual(len(results), 1)


class TestLearningsTop(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.learn = Learnings(project_root=self.root)
        self.learn.log("low", "low confidence", confidence=30)
        self.learn.log("high", "high confidence", confidence=95)
        self.learn.log("mid", "medium confidence", confidence=60)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_top_returns_highest_first(self) -> None:
        top = self.learn.top(limit=2)
        self.assertEqual(len(top), 2)
        self.assertEqual(top[0].key, "high")

    def test_top_limit(self) -> None:
        top = self.learn.top(limit=1)
        self.assertEqual(len(top), 1)


class TestLearningsPrune(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.learn = Learnings(project_root=self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_prune_no_op_under_limit(self) -> None:
        self.learn.log("a", "insight a")
        removed = self.learn.prune(max_entries=10)
        self.assertEqual(removed, 0)
        self.assertEqual(self.learn.count(), 1)

    def test_prune_removes_excess(self) -> None:
        for i in range(10):
            self.learn.log(f"key-{i}", f"insight {i}", confidence=i * 10)
        removed = self.learn.prune(max_entries=5)
        self.assertEqual(removed, 5)
        self.assertEqual(self.learn.count(), 5)


class TestLearningsInjectContext(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.learn = Learnings(project_root=self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_inject_empty(self) -> None:
        ctx = self.learn.inject_context()
        self.assertEqual(ctx, "")

    def test_inject_with_entries(self) -> None:
        self.learn.log("k1", "insight 1", confidence=90)
        self.learn.log("k2", "insight 2", confidence=80)
        ctx = self.learn.inject_context(limit=2)
        self.assertIn("Operational Learnings", ctx)
        self.assertIn("k1", ctx)
        self.assertIn("insight 1", ctx)


class TestLearningsRunDispatch(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.learn = Learnings(project_root=self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_run_log(self) -> None:
        result = self.learn.run(action="log", key="k", insight="i")
        self.assertIsInstance(result, LearningEntry)

    def test_run_top(self) -> None:
        result = self.learn.run(action="top", limit=5)
        self.assertIsInstance(result, list)

    def test_run_count(self) -> None:
        result = self.learn.run(action="count")
        self.assertEqual(result, 0)

    def test_run_unknown_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.learn.run(action="bogus")


class TestLearningsJSONLIntegrity(unittest.TestCase):
    """Ensure JSONL storage is well-formed."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.learn = Learnings(project_root=self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_jsonl_format(self) -> None:
        self.learn.log("a", "insight a")
        self.learn.log("b", "insight b")
        jsonl = self.root / "_grimoire" / "_memory" / "learnings" / "operational.jsonl"
        lines = jsonl.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 2)
        for line in lines:
            parsed = json.loads(line)
            self.assertIn("key", parsed)
            self.assertIn("insight", parsed)


if __name__ == "__main__":
    unittest.main()
