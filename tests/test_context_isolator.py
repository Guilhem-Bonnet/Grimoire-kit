"""Tests for grimoire.core.context_isolator."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from grimoire.core.context_isolator import (
    ContextIsolator,
    ContextItem,
    ContextPackage,
    _estimate_tokens,
)


class TestEstimateTokens(unittest.TestCase):
    def test_empty(self) -> None:
        self.assertEqual(_estimate_tokens(""), 1)

    def test_short(self) -> None:
        self.assertEqual(_estimate_tokens("hello"), 1)

    def test_longer(self) -> None:
        self.assertGreater(_estimate_tokens("a" * 100), 20)


class TestContextItem(unittest.TestCase):
    def test_to_dict(self) -> None:
        item = ContextItem(source="learning", key="k", content="c", relevance=0.75, tokens=10)
        d = item.to_dict()
        self.assertEqual(d["source"], "learning")
        self.assertEqual(d["relevance"], 0.75)


class TestContextPackage(unittest.TestCase):
    def test_item_count(self) -> None:
        pkg = ContextPackage(
            agent="dev",
            task="test",
            items=(
                ContextItem("a", "k1", "c1", 0.5, 10),
                ContextItem("b", "k2", "c2", 0.3, 20),
            ),
            budget_tokens=100,
            used_tokens=30,
            trimmed=False,
        )
        self.assertEqual(pkg.item_count, 2)

    def test_to_markdown(self) -> None:
        pkg = ContextPackage(
            agent="dev",
            task="test",
            items=(ContextItem("learning", "key1", "content1", 0.8, 10),),
            budget_tokens=100,
            used_tokens=10,
            trimmed=False,
        )
        md = pkg.to_markdown()
        self.assertIn("CONTEXT:START", md)
        self.assertIn("CONTEXT:END", md)
        self.assertIn("key1", md)
        self.assertIn("80%", md)


class TestContextIsolator(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        # Create learnings
        learnings_dir = self.root / "_grimoire/_memory/learnings"
        learnings_dir.mkdir(parents=True)
        entries = [
            {"key": "pytest-timeout", "insight": "Always use timeout in pytest tests", "confidence": 90},
            {"key": "ruff-fix", "insight": "Run ruff check --fix before commit", "confidence": 80},
            {"key": "docker-build", "insight": "Use buildkit for faster builds", "confidence": 70},
        ]
        with open(learnings_dir / "operational.jsonl", "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

        # Create shared memory
        memory = self.root / "_grimoire/_memory"
        (memory / "shared-context.md").write_text(
            "# Context\n\n## Testing\n\nUse pytest with -x flag.\n\n## Docker\n\nUse compose v2.\n"
        )

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_isolate_basic(self) -> None:
        iso = ContextIsolator(self.root)
        pkg = iso.isolate(agent="dev", task="fix pytest test timeout issue")
        self.assertIsInstance(pkg, ContextPackage)
        self.assertEqual(pkg.agent, "dev")
        self.assertGreater(pkg.item_count, 0)

    def test_isolate_relevance_ordering(self) -> None:
        iso = ContextIsolator(self.root)
        pkg = iso.isolate(agent="dev", task="pytest fix timeout")
        # pytest-timeout should score higher than docker-build
        keys = [i.key for i in pkg.items]
        if "pytest-timeout" in keys and "docker-build" in keys:
            self.assertLess(keys.index("pytest-timeout"), keys.index("docker-build"))

    def test_isolate_budget_trimming(self) -> None:
        iso = ContextIsolator(self.root)
        pkg = iso.isolate(agent="dev", task="something", budget_tokens=10)
        self.assertLessEqual(pkg.used_tokens, 10)

    def test_isolate_empty_project(self) -> None:
        with TemporaryDirectory() as td:
            iso = ContextIsolator(Path(td))
            pkg = iso.isolate(agent="dev", task="nothing")
            self.assertEqual(pkg.item_count, 0)
            self.assertFalse(pkg.trimmed)

    def test_isolate_no_learnings(self) -> None:
        iso = ContextIsolator(self.root)
        pkg = iso.isolate(agent="dev", task="test", include_learnings=False)
        sources = {i.source for i in pkg.items}
        self.assertNotIn("learning", sources)

    def test_isolate_no_memory(self) -> None:
        iso = ContextIsolator(self.root)
        pkg = iso.isolate(agent="dev", task="test", include_memory=False)
        sources = {i.source for i in pkg.items}
        self.assertNotIn("memory", sources)

    def test_agent_domains_static(self) -> None:
        domains = ContextIsolator.agent_domains()
        self.assertIn("dev", domains)
        self.assertIn("qa", domains)
        self.assertIn("architect", domains)

    def test_agent_domain_bonus(self) -> None:
        iso = ContextIsolator(self.root)
        # Task matching "test" should boost qa agent's relevance
        pkg_qa = iso.isolate(agent="qa", task="validate test coverage")
        pkg_pm = iso.isolate(agent="pm", task="validate test coverage")
        # QA should get higher total relevance for test-related content
        qa_total = sum(i.relevance for i in pkg_qa.items)
        pm_total = sum(i.relevance for i in pkg_pm.items)
        self.assertGreaterEqual(qa_total, pm_total)

    def test_memory_section_splitting(self) -> None:
        iso = ContextIsolator(self.root)
        pkg = iso.isolate(agent="dev", task="docker compose", include_learnings=False)
        keys = [i.key for i in pkg.items if i.source == "memory"]
        # Should have split into "Testing" and "Docker" sections
        self.assertTrue(any("Testing" in k or "Docker" in k for k in keys))

    def test_malformed_learnings_graceful(self) -> None:
        learnings_file = self.root / "_grimoire/_memory/learnings/operational.jsonl"
        learnings_file.write_text("not json\n{bad\n")
        iso = ContextIsolator(self.root)
        pkg = iso.isolate(agent="dev", task="test")
        # Should not crash, just skip learnings
        self.assertIsInstance(pkg, ContextPackage)


if __name__ == "__main__":
    unittest.main()
