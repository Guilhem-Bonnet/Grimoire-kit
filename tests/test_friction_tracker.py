"""Tests for grimoire.core.friction_tracker — friction budget tracking."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from grimoire.core.friction_tracker import FrictionEvent, FrictionSnapshot, FrictionTracker


class TestFrictionEvent(unittest.TestCase):
    def test_to_dict(self) -> None:
        ev = FrictionEvent(
            question="Which testing framework?",
            category="preference",
            timestamp="2026-01-01T00:00:00Z",
            batched=False,
        )
        d = ev.to_dict()
        self.assertEqual(d["question"], "Which testing framework?")
        self.assertEqual(d["category"], "preference")
        self.assertFalse(d["batched"])

    def test_frozen(self) -> None:
        ev = FrictionEvent(question="q", category="c", timestamp="t")
        with self.assertRaises(AttributeError):
            ev.question = "new"  # type: ignore[misc]


class TestFrictionSnapshot(unittest.TestCase):
    def test_to_dict(self) -> None:
        snap = FrictionSnapshot(
            budget=5,
            spent=2,
            remaining=3,
            should_batch=True,
            events=(),
            friction_score=0.4,
        )
        d = snap.to_dict()
        self.assertEqual(d["budget"], 5)
        self.assertEqual(d["spent"], 2)
        self.assertEqual(d["remaining"], 3)
        self.assertTrue(d["should_batch"])
        self.assertAlmostEqual(d["friction_score"], 0.4)


class TestFrictionTracker(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_initial_budget(self) -> None:
        tracker = FrictionTracker(self.root, budget=5)
        self.assertEqual(tracker.budget_remaining, 5)
        self.assertFalse(tracker.should_batch)
        self.assertFalse(tracker.budget_exhausted)

    def test_record_question_decreases_budget(self) -> None:
        tracker = FrictionTracker(self.root, budget=5)
        tracker.record_question("What framework?")
        self.assertEqual(tracker.budget_remaining, 4)

    def test_should_batch_after_threshold(self) -> None:
        tracker = FrictionTracker(self.root, budget=5)
        tracker.record_question("Q1")
        self.assertFalse(tracker.should_batch)
        tracker.record_question("Q2")
        self.assertTrue(tracker.should_batch)

    def test_budget_exhausted(self) -> None:
        tracker = FrictionTracker(self.root, budget=3)
        tracker.record_question("Q1")
        tracker.record_question("Q2")
        self.assertFalse(tracker.budget_exhausted)
        tracker.record_question("Q3")
        self.assertTrue(tracker.budget_exhausted)

    def test_remaining_never_negative(self) -> None:
        tracker = FrictionTracker(self.root, budget=1)
        tracker.record_question("Q1")
        tracker.record_question("Q2")
        self.assertEqual(tracker.budget_remaining, 0)

    def test_record_returns_event(self) -> None:
        tracker = FrictionTracker(self.root)
        ev = tracker.record_question("What?", category="escalation")
        self.assertIsInstance(ev, FrictionEvent)
        self.assertEqual(ev.category, "escalation")
        self.assertTrue(ev.timestamp)

    def test_persists_to_jsonl(self) -> None:
        tracker = FrictionTracker(self.root)
        tracker.record_question("Test question")
        jsonl = self.root / "_grimoire" / "_memory" / "telemetry" / "friction-events.jsonl"
        self.assertTrue(jsonl.exists())
        data = json.loads(jsonl.read_text().strip())
        self.assertEqual(data["question"], "Test question")

    def test_batch_counts_as_one(self) -> None:
        tracker = FrictionTracker(self.root, budget=5)
        events = tracker.record_batch(["Q1", "Q2", "Q3"])
        self.assertEqual(len(events), 3)
        self.assertEqual(tracker.budget_remaining, 4)  # batch = 1 point

    def test_batch_marks_batched(self) -> None:
        tracker = FrictionTracker(self.root)
        events = tracker.record_batch(["Q1", "Q2"])
        self.assertTrue(all(e.batched for e in events))

    def test_snapshot(self) -> None:
        tracker = FrictionTracker(self.root, budget=5)
        tracker.record_question("Q1")
        tracker.record_question("Q2")
        snap = tracker.snapshot()
        self.assertIsInstance(snap, FrictionSnapshot)
        self.assertEqual(snap.budget, 5)
        self.assertEqual(snap.spent, 2)
        self.assertEqual(snap.remaining, 3)
        self.assertTrue(snap.should_batch)
        self.assertAlmostEqual(snap.friction_score, 0.4)

    def test_snapshot_capped_at_one(self) -> None:
        tracker = FrictionTracker(self.root, budget=2)
        for i in range(5):
            tracker.record_question(f"Q{i}")
        snap = tracker.snapshot()
        self.assertLessEqual(snap.friction_score, 1.0)

    def test_reset(self) -> None:
        tracker = FrictionTracker(self.root, budget=5)
        tracker.record_question("Q1")
        tracker.reset()
        self.assertEqual(tracker.budget_remaining, 5)
        self.assertFalse(tracker.should_batch)

    def test_custom_category(self) -> None:
        tracker = FrictionTracker(self.root)
        ev = tracker.record_question("Confirm deploy?", category="confirmation")
        self.assertEqual(ev.category, "confirmation")

    def test_empty_batch_no_effect(self) -> None:
        tracker = FrictionTracker(self.root, budget=5)
        events = tracker.record_batch([])
        self.assertEqual(len(events), 0)
        self.assertEqual(tracker.budget_remaining, 5)


if __name__ == "__main__":
    unittest.main()
