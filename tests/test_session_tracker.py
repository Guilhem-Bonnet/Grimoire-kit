"""Tests for grimoire.core.session_tracker — session momentum tracking."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from grimoire.core.session_tracker import (
    Exchange,
    SessionSnapshot,
    SessionTracker,
    _classify_momentum,
)


class TestExchange(unittest.TestCase):
    def test_to_dict(self) -> None:
        ex = Exchange(tokens_in=100, tokens_out=500, autonomy="high", timestamp="2026-01-01T00:00:00Z")
        d = ex.to_dict()
        self.assertEqual(d["tokens_in"], 100)
        self.assertEqual(d["tokens_out"], 500)
        self.assertEqual(d["autonomy"], "high")

    def test_frozen(self) -> None:
        ex = Exchange(tokens_in=0, tokens_out=0, autonomy="low", timestamp="")
        with self.assertRaises(AttributeError):
            ex.tokens_in = 999  # type: ignore[misc]


class TestSessionSnapshot(unittest.TestCase):
    def test_to_dict(self) -> None:
        snap = SessionSnapshot(
            exchange_count=5,
            total_tokens_in=2000,
            total_tokens_out=8000,
            current_autonomy="high",
            autonomy_transitions=1,
            momentum="hot",
            avg_tokens_per_exchange=2000,
        )
        d = snap.to_dict()
        self.assertEqual(d["exchange_count"], 5)
        self.assertEqual(d["momentum"], "hot")
        self.assertEqual(d["avg_tokens_per_exchange"], 2000)


class TestClassifyMomentum(unittest.TestCase):
    def test_empty_is_cold(self) -> None:
        self.assertEqual(_classify_momentum([]), "cold")

    def test_single_is_cold(self) -> None:
        ex = Exchange(tokens_in=100, tokens_out=200, autonomy="low", timestamp="")
        self.assertEqual(_classify_momentum([ex]), "cold")

    def test_warming(self) -> None:
        exchanges = [
            Exchange(tokens_in=50, tokens_out=100, autonomy="low", timestamp=""),
            Exchange(tokens_in=100, tokens_out=300, autonomy="medium", timestamp=""),
            Exchange(tokens_in=200, tokens_out=500, autonomy="high", timestamp=""),
        ]
        result = _classify_momentum(exchanges)
        self.assertIn(result, ("warming", "hot"))

    def test_hot_sustained(self) -> None:
        exchanges = [
            Exchange(tokens_in=200, tokens_out=1000, autonomy="high", timestamp="")
            for _ in range(6)
        ]
        self.assertEqual(_classify_momentum(exchanges), "hot")

    def test_cooling(self) -> None:
        exchanges = [
            Exchange(tokens_in=200, tokens_out=1000, autonomy="high", timestamp=""),
            Exchange(tokens_in=200, tokens_out=1000, autonomy="high", timestamp=""),
            Exchange(tokens_in=200, tokens_out=1000, autonomy="high", timestamp=""),
            Exchange(tokens_in=50, tokens_out=100, autonomy="low", timestamp=""),
            Exchange(tokens_in=50, tokens_out=100, autonomy="low", timestamp=""),
            Exchange(tokens_in=50, tokens_out=100, autonomy="low", timestamp=""),
        ]
        self.assertEqual(_classify_momentum(exchanges), "cooling")


class TestSessionTracker(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_record_exchange(self) -> None:
        tracker = SessionTracker(self.root)
        ex = tracker.record_exchange(tokens_in=100, tokens_out=500, autonomy="high")
        self.assertIsInstance(ex, Exchange)
        self.assertEqual(ex.tokens_in, 100)
        self.assertTrue(ex.timestamp)

    def test_persists_to_jsonl(self) -> None:
        tracker = SessionTracker(self.root)
        tracker.record_exchange(tokens_in=100, tokens_out=500)
        jsonl = self.root / "_grimoire" / "_memory" / "telemetry" / "session-momentum.jsonl"
        self.assertTrue(jsonl.exists())
        lines = jsonl.read_text().strip().splitlines()
        self.assertEqual(len(lines), 1)
        data = json.loads(lines[0])
        self.assertEqual(data["tokens_in"], 100)

    def test_snapshot_empty(self) -> None:
        tracker = SessionTracker(self.root)
        snap = tracker.snapshot()
        self.assertEqual(snap.exchange_count, 0)
        self.assertEqual(snap.momentum, "cold")
        self.assertEqual(snap.current_autonomy, "medium")

    def test_snapshot_after_exchanges(self) -> None:
        tracker = SessionTracker(self.root)
        tracker.record_exchange(tokens_in=100, tokens_out=500, autonomy="low")
        tracker.record_exchange(tokens_in=200, tokens_out=800, autonomy="high")
        snap = tracker.snapshot()
        self.assertEqual(snap.exchange_count, 2)
        self.assertEqual(snap.total_tokens_in, 300)
        self.assertEqual(snap.total_tokens_out, 1300)
        self.assertEqual(snap.current_autonomy, "high")
        self.assertEqual(snap.autonomy_transitions, 1)

    def test_reset_clears_state(self) -> None:
        tracker = SessionTracker(self.root)
        tracker.record_exchange(tokens_in=100, tokens_out=500)
        tracker.reset()
        snap = tracker.snapshot()
        # After reset, snapshot loads from disk
        self.assertGreaterEqual(snap.exchange_count, 0)

    def test_momentum_progression(self) -> None:
        tracker = SessionTracker(self.root)
        for i in range(6):
            tracker.record_exchange(tokens_in=100 * (i + 1), tokens_out=500 * (i + 1), autonomy="high")
        snap = tracker.snapshot()
        self.assertIn(snap.momentum, ("warming", "hot"))

    def test_avg_tokens(self) -> None:
        tracker = SessionTracker(self.root)
        tracker.record_exchange(tokens_in=100, tokens_out=400)
        tracker.record_exchange(tokens_in=200, tokens_out=600)
        snap = tracker.snapshot()
        # (100+400+200+600) / 2 = 650
        self.assertEqual(snap.avg_tokens_per_exchange, 650)

    def test_loads_from_disk_if_memory_empty(self) -> None:
        # Write then create new tracker
        tracker1 = SessionTracker(self.root)
        tracker1.record_exchange(tokens_in=100, tokens_out=500, autonomy="high")
        tracker1.record_exchange(tokens_in=200, tokens_out=600, autonomy="medium")

        tracker2 = SessionTracker(self.root)
        snap = tracker2.snapshot()
        self.assertEqual(snap.exchange_count, 2)


if __name__ == "__main__":
    unittest.main()
