"""Tests for grimoire.core.trust_scorer — sub-agent trust scoring."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from grimoire.core.trust_scorer import TrustScore, TrustScorer, _level_for


class TestLevelFor(unittest.TestCase):
    def test_trusted(self) -> None:
        self.assertEqual(_level_for(0.8), "trusted")

    def test_trusted_boundary(self) -> None:
        self.assertEqual(_level_for(0.75), "trusted")

    def test_cautious(self) -> None:
        self.assertEqual(_level_for(0.6), "cautious")

    def test_cautious_boundary(self) -> None:
        self.assertEqual(_level_for(0.5), "cautious")

    def test_untrusted(self) -> None:
        self.assertEqual(_level_for(0.3), "untrusted")

    def test_zero(self) -> None:
        self.assertEqual(_level_for(0.0), "untrusted")


class TestTrustScore(unittest.TestCase):
    def test_to_dict(self) -> None:
        ts = TrustScore(
            agent="dev",
            score=0.8,
            level="trusted",
            eval_count=5,
            success_rate=0.9,
            avg_grade_score=0.85,
            evidence=("good track record",),
        )
        d = ts.to_dict()
        self.assertEqual(d["agent"], "dev")
        self.assertEqual(d["score"], 0.8)
        self.assertEqual(d["level"], "trusted")
        self.assertEqual(d["eval_count"], 5)
        self.assertIn("good track record", d["evidence"])

    def test_frozen(self) -> None:
        ts = TrustScore("dev", 0.5, "cautious", 0, 0.0, 0.0, ())
        with self.assertRaises(AttributeError):
            ts.agent = "qa"  # type: ignore[misc]


class TestTrustScorerNoData(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_no_data_returns_cautious(self) -> None:
        scorer = TrustScorer(self.root)
        trust = scorer.score("dev")
        self.assertEqual(trust.level, "cautious")
        self.assertEqual(trust.score, 0.5)
        self.assertIn("Insufficient data", trust.evidence[0])

    def test_scoreboard_empty(self) -> None:
        scorer = TrustScorer(self.root)
        board = scorer.scoreboard()
        self.assertEqual(board, {})


class TestTrustScorerWithEvals(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._telem_dir = self.root / "_grimoire" / "_memory" / "telemetry"
        self._telem_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_evals(self, entries: list[dict]) -> None:
        path = self._telem_dir / "evaluations.jsonl"
        with open(path, "w", encoding="utf-8") as fh:
            for e in entries:
                fh.write(json.dumps(e) + "\n")

    def _write_telemetry(self, entries: list[dict]) -> None:
        path = self._telem_dir / "skill-usage.jsonl"
        with open(path, "w", encoding="utf-8") as fh:
            for e in entries:
                fh.write(json.dumps(e) + "\n")

    def test_high_eval_trusted(self) -> None:
        self._write_evals([
            {"agent": "dev", "grade": "A", "score": 0.95},
            {"agent": "dev", "grade": "A", "score": 0.92},
            {"agent": "dev", "grade": "B", "score": 0.85},
        ])
        scorer = TrustScorer(self.root)
        trust = scorer.score("dev")
        self.assertEqual(trust.level, "trusted")
        self.assertGreater(trust.score, 0.75)

    def test_low_eval_untrusted(self) -> None:
        self._write_evals([
            {"agent": "dev", "grade": "F", "score": 0.2},
            {"agent": "dev", "grade": "F", "score": 0.3},
            {"agent": "dev", "grade": "D", "score": 0.5},
        ])
        scorer = TrustScorer(self.root)
        trust = scorer.score("dev")
        self.assertIn(trust.level, ("untrusted", "cautious"))
        self.assertLess(trust.score, 0.6)

    def test_combines_evals_and_telemetry(self) -> None:
        self._write_evals([
            {"agent": "qa", "grade": "B", "score": 0.8},
            {"agent": "qa", "grade": "A", "score": 0.9},
        ])
        self._write_telemetry([
            {"skill": "qa", "outcome": "success"},
            {"skill": "qa", "outcome": "success"},
            {"skill": "qa", "outcome": "failure"},
        ])
        scorer = TrustScorer(self.root)
        trust = scorer.score("qa")
        self.assertGreater(trust.score, 0.5)
        self.assertGreater(trust.eval_count, 0)
        self.assertGreater(trust.success_rate, 0)

    def test_scoreboard_multiple_agents(self) -> None:
        self._write_evals([
            {"agent": "dev", "grade": "A", "score": 0.95},
            {"agent": "dev", "grade": "A", "score": 0.92},
            {"agent": "dev", "grade": "B", "score": 0.85},
            {"agent": "qa", "grade": "C", "score": 0.7},
            {"agent": "qa", "grade": "D", "score": 0.6},
            {"agent": "qa", "grade": "F", "score": 0.3},
        ])
        scorer = TrustScorer(self.root)
        board = scorer.scoreboard()
        self.assertIn("dev", board)
        self.assertIn("qa", board)
        self.assertGreater(board["dev"].score, board["qa"].score)

    def test_min_events_threshold(self) -> None:
        self._write_evals([
            {"agent": "dev", "grade": "A", "score": 0.95},
        ])
        scorer = TrustScorer(self.root, min_events=5)
        trust = scorer.score("dev")
        self.assertEqual(trust.level, "cautious")
        self.assertIn("Insufficient data", trust.evidence[0])

    def test_only_telemetry(self) -> None:
        self._write_telemetry([
            {"skill": "dev", "outcome": "success"},
            {"skill": "dev", "outcome": "success"},
            {"skill": "dev", "outcome": "success"},
        ])
        scorer = TrustScorer(self.root)
        trust = scorer.score("dev")
        self.assertEqual(trust.success_rate, 1.0)

    def test_corrupted_jsonl(self) -> None:
        path = self._telem_dir / "evaluations.jsonl"
        path.write_text("not-valid-json\n", encoding="utf-8")
        scorer = TrustScorer(self.root)
        # Should not raise
        trust = scorer.score("dev")
        self.assertEqual(trust.level, "cautious")


if __name__ == "__main__":
    unittest.main()
