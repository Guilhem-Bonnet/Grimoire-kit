"""Tests for grimoire.core.evaluator."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from grimoire.core.evaluator import (
    DimensionScore,
    EvalCriteria,
    EvalResult,
    Evaluator,
    _grade,
)


class TestGrade(unittest.TestCase):
    def test_a(self) -> None:
        self.assertEqual(_grade(0.95), "A")

    def test_b(self) -> None:
        self.assertEqual(_grade(0.85), "B")

    def test_c(self) -> None:
        self.assertEqual(_grade(0.75), "C")

    def test_d(self) -> None:
        self.assertEqual(_grade(0.65), "D")

    def test_f(self) -> None:
        self.assertEqual(_grade(0.3), "F")


class TestDimensionScore(unittest.TestCase):
    def test_to_dict(self) -> None:
        ds = DimensionScore("safety", 0.9, "OK")
        d = ds.to_dict()
        self.assertEqual(d["dimension"], "safety")
        self.assertEqual(d["score"], 0.9)


class TestEvalResult(unittest.TestCase):
    def test_to_dict(self) -> None:
        result = EvalResult(
            agent="dev",
            task="test",
            dimensions=(DimensionScore("safety", 1.0, "OK"),),
            score=0.9,
            grade="A",
            passed=True,
            timestamp="2026-01-01T00:00:00Z",
        )
        d = result.to_dict()
        self.assertEqual(d["agent"], "dev")
        self.assertEqual(d["grade"], "A")
        self.assertTrue(d["passed"])


class TestEvaluator(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_evaluate_good_output(self) -> None:
        ev = Evaluator(self.root)
        result = ev.evaluate(
            agent="dev",
            output="def login(user: str, password: str) -> bool:\n    return authenticate(user, password)\n",
            task="implement login function",
        )
        self.assertIsInstance(result, EvalResult)
        self.assertTrue(result.passed)

    def test_evaluate_short_output(self) -> None:
        ev = Evaluator(self.root)
        result = ev.evaluate(agent="dev", output="ok", task="big task")
        # Completeness should be low
        completeness = next((d for d in result.dimensions if d.dimension == "completeness"), None)
        self.assertIsNotNone(completeness)
        self.assertLess(completeness.score, 0.5)

    def test_evaluate_unsafe_output(self) -> None:
        ev = Evaluator(self.root)
        result = ev.evaluate(
            agent="dev",
            output="import os\nos.system('rm -rf /')\n",
            task="cleanup",
        )
        safety = next((d for d in result.dimensions if d.dimension == "safety"), None)
        self.assertIsNotNone(safety)
        self.assertLess(safety.score, 0.5)

    def test_evaluate_mixed_indentation(self) -> None:
        ev = Evaluator(self.root)
        output = "def foo():\n\tif True:\n        pass\n"
        result = ev.evaluate(agent="dev", output=output, task="test")
        style = next((d for d in result.dimensions if d.dimension == "style"), None)
        self.assertIsNotNone(style)
        self.assertLess(style.score, 1.0)

    def test_evaluate_relevance(self) -> None:
        ev = Evaluator(self.root)
        result = ev.evaluate(
            agent="dev",
            output="def sort_array(arr): return sorted(arr)",
            task="implement sort array function",
        )
        relevance = next((d for d in result.dimensions if d.dimension == "relevance"), None)
        self.assertIsNotNone(relevance)
        self.assertGreater(relevance.score, 0.5)

    def test_evaluate_no_task_relevance(self) -> None:
        ev = Evaluator(self.root)
        criteria = EvalCriteria(check_relevance=True)
        result = ev.evaluate(agent="dev", output="some code", task="", criteria=criteria)
        # No relevance dimension when task is empty
        dims = [d.dimension for d in result.dimensions]
        self.assertNotIn("relevance", dims)

    def test_evaluate_with_tests(self) -> None:
        ev = Evaluator(self.root)
        criteria = EvalCriteria(check_tests=True)
        output = "def test_login():\n    assert login('user', 'pass') is True\n\nclass TestAuth(unittest.TestCase):\n    pass\n"
        result = ev.evaluate(agent="dev", output=output, task="test", criteria=criteria)
        tests = next((d for d in result.dimensions if d.dimension == "tests"), None)
        self.assertIsNotNone(tests)
        self.assertGreater(tests.score, 0.5)

    def test_evaluate_records_to_jsonl(self) -> None:
        ev = Evaluator(self.root)
        ev.evaluate(agent="dev", output="valid code here" * 5, task="test")
        eval_file = self.root / "_grimoire/_memory/telemetry/evaluations.jsonl"
        self.assertTrue(eval_file.exists())
        data = json.loads(eval_file.read_text().strip().splitlines()[0])
        self.assertEqual(data["agent"], "dev")

    def test_recent_empty(self) -> None:
        ev = Evaluator(self.root)
        self.assertEqual(ev.recent(), [])

    def test_recent_after_evaluate(self) -> None:
        ev = Evaluator(self.root)
        ev.evaluate(agent="dev", output="good output here with content", task="test")
        ev.evaluate(agent="qa", output="test results are fine and correct", task="validate")
        recent = ev.recent()
        self.assertEqual(len(recent), 2)

    def test_recent_filter_by_agent(self) -> None:
        ev = Evaluator(self.root)
        ev.evaluate(agent="dev", output="code implementation result", task="t1")
        ev.evaluate(agent="qa", output="test validation outcome", task="t2")
        recent = ev.recent(agent="qa")
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["agent"], "qa")

    def test_agent_scores(self) -> None:
        ev = Evaluator(self.root)
        ev.evaluate(agent="dev", output="good code implementation result", task="t1")
        ev.evaluate(agent="dev", output="more code implementation output", task="t2")
        scores = ev.agent_scores()
        self.assertIn("dev", scores)
        self.assertEqual(scores["dev"]["count"], 2)

    def test_todo_penalty(self) -> None:
        ev = Evaluator(self.root)
        result = ev.evaluate(
            agent="dev",
            output="def login():\n    pass  # TODO implement this\n",
            task="login",
        )
        completeness = next((d for d in result.dimensions if d.dimension == "completeness"), None)
        self.assertLess(completeness.score, 0.8)

    def test_hardcoded_secret_detection(self) -> None:
        ev = Evaluator(self.root)
        result = ev.evaluate(
            agent="dev",
            output='api_key = "sk-1234567890"\n',
            task="config",
        )
        safety = next((d for d in result.dimensions if d.dimension == "safety"), None)
        self.assertLess(safety.score, 0.5)


if __name__ == "__main__":
    unittest.main()
