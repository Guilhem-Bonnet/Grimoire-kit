"""Tests for grimoire.core.workflow_analyzer."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from grimoire.core.workflow_analyzer import (
    AnalysisReport,
    Recommendation,
    SkillMetrics,
    WorkflowAnalyzer,
)


class TestSkillMetrics(unittest.TestCase):
    def test_success_rate(self) -> None:
        m = SkillMetrics("tdd", 10, 8, 2, 5.0, "2026-01-01")
        self.assertAlmostEqual(m.success_rate, 0.8)

    def test_success_rate_zero(self) -> None:
        m = SkillMetrics("tdd", 0, 0, 0, 0.0, "")
        self.assertEqual(m.success_rate, 0.0)

    def test_to_dict(self) -> None:
        m = SkillMetrics("tdd", 10, 8, 2, 5.0, "2026-01-01")
        d = m.to_dict()
        self.assertEqual(d["skill"], "tdd")
        self.assertEqual(d["success_rate"], 0.8)


class TestRecommendation(unittest.TestCase):
    def test_to_dict(self) -> None:
        r = Recommendation("bottleneck", "high", "Slow skill", "tdd", "avg 45s")
        d = r.to_dict()
        self.assertEqual(d["category"], "bottleneck")
        self.assertEqual(d["severity"], "high")


class TestAnalysisReport(unittest.TestCase):
    def test_to_markdown_empty(self) -> None:
        report = AnalysisReport(0, 0, 0, (), (), (), "2026")
        md = report.to_markdown()
        self.assertIn("Workflow Analysis", md)

    def test_to_markdown_with_data(self) -> None:
        metrics = (SkillMetrics("tdd", 10, 8, 2, 5.0, "2026"),)
        recs = (Recommendation("bottleneck", "high", "Slow", "tdd"),)
        failures = (("ruff", 3),)
        report = AnalysisReport(50, 5, 3, metrics, recs, failures, "2026")
        md = report.to_markdown()
        self.assertIn("tdd", md)
        self.assertIn("Slow", md)
        self.assertIn("ruff", md)


def _make_telemetry(entries: list[dict]) -> str:
    return "\n".join(json.dumps(e) for e in entries)


class TestWorkflowAnalyzer(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.telem_dir = self.root / "_grimoire/_memory/telemetry"
        self.telem_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_analyze_empty(self) -> None:
        wa = WorkflowAnalyzer(self.root)
        report = wa.analyze()
        self.assertEqual(report.total_events, 0)
        self.assertEqual(report.unique_skills, 0)

    def test_analyze_skill_metrics(self) -> None:
        entries = [
            {"event_type": "skill", "skill": "tdd", "outcome": "success", "duration_s": 10.0, "timestamp": "2026-01"},
            {"event_type": "skill", "skill": "tdd", "outcome": "success", "duration_s": 20.0, "timestamp": "2026-02"},
            {"event_type": "skill", "skill": "tdd", "outcome": "failure", "duration_s": 5.0, "timestamp": "2026-03"},
        ]
        (self.telem_dir / "skill-usage.jsonl").write_text(_make_telemetry(entries))

        wa = WorkflowAnalyzer(self.root)
        report = wa.analyze()
        self.assertEqual(report.total_events, 3)
        self.assertEqual(report.unique_skills, 1)
        tdd = report.skill_metrics[0]
        self.assertEqual(tdd.skill, "tdd")
        self.assertEqual(tdd.invocations, 3)
        self.assertEqual(tdd.successes, 2)
        self.assertEqual(tdd.failures, 1)

    def test_analyze_top_failures(self) -> None:
        entries = [
            {"event_type": "tool", "tool": "ruff", "outcome": "failure", "timestamp": "t1"},
            {"event_type": "tool", "tool": "ruff", "outcome": "failure", "timestamp": "t2"},
            {"event_type": "tool", "tool": "ruff", "outcome": "failure", "timestamp": "t3"},
            {"event_type": "tool", "tool": "pytest", "outcome": "failure", "timestamp": "t4"},
        ]
        (self.telem_dir / "skill-usage.jsonl").write_text(_make_telemetry(entries))

        wa = WorkflowAnalyzer(self.root)
        report = wa.analyze()
        self.assertEqual(report.top_failures[0], ("ruff", 3))

    def test_bottleneck_recommendation(self) -> None:
        entries = [
            {"event_type": "skill", "skill": "slow", "outcome": "success", "duration_s": 45.0, "timestamp": f"t{i}"}
            for i in range(5)
        ]
        (self.telem_dir / "skill-usage.jsonl").write_text(_make_telemetry(entries))

        wa = WorkflowAnalyzer(self.root)
        report = wa.analyze()
        bottlenecks = [r for r in report.recommendations if r.category == "bottleneck"]
        self.assertGreater(len(bottlenecks), 0)
        self.assertEqual(bottlenecks[0].skill, "slow")

    def test_failure_pattern_recommendation(self) -> None:
        entries = [
            {"event_type": "skill", "skill": "flaky", "outcome": "failure", "duration_s": 1.0, "timestamp": f"t{i}"}
            for i in range(4)
        ]
        (self.telem_dir / "skill-usage.jsonl").write_text(_make_telemetry(entries))

        wa = WorkflowAnalyzer(self.root)
        report = wa.analyze()
        failures = [r for r in report.recommendations if r.category == "failure_pattern"]
        self.assertGreater(len(failures), 0)

    def test_underuse_recommendation(self) -> None:
        entries = [
            {"event_type": "skill", "skill": f"one-off-{i}", "outcome": "success", "duration_s": 1.0, "timestamp": "t"}
            for i in range(5)
        ]
        (self.telem_dir / "skill-usage.jsonl").write_text(_make_telemetry(entries))

        wa = WorkflowAnalyzer(self.root)
        report = wa.analyze()
        underuse = [r for r in report.recommendations if r.category == "underuse"]
        self.assertGreater(len(underuse), 0)

    def test_analyze_mixed_events(self) -> None:
        entries = [
            {"event_type": "skill", "skill": "tdd", "outcome": "success", "timestamp": "t1"},
            {"event_type": "tool", "tool": "ruff", "outcome": "success", "timestamp": "t2"},
            {"event_type": "session", "outcome": "success", "timestamp": "t3"},
        ]
        (self.telem_dir / "skill-usage.jsonl").write_text(_make_telemetry(entries))

        wa = WorkflowAnalyzer(self.root)
        report = wa.analyze()
        self.assertEqual(report.total_events, 3)
        self.assertEqual(report.unique_skills, 1)
        self.assertEqual(report.unique_tools, 1)

    def test_malformed_jsonl(self) -> None:
        (self.telem_dir / "skill-usage.jsonl").write_text("not json\n{bad\n")
        wa = WorkflowAnalyzer(self.root)
        report = wa.analyze()
        # Should not crash
        self.assertIsInstance(report, AnalysisReport)


if __name__ == "__main__":
    unittest.main()
