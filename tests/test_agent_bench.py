#!/usr/bin/env python3
"""
Tests pour agent-bench.py — benchmark des agents.

Fonctions testées :
  - parse_trace()
  - AgentMetrics properties (ac_pass_rate, activity_score)
  - SessionMetrics structure
  - _auto_recommendations()
  - summary_line() (capture stdout)
"""

import importlib
import shutil
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

KIT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(KIT_DIR / "framework" / "tools"))


def _import_bench():
    return importlib.import_module("agent-bench")


class TestAgentMetricsProperties(unittest.TestCase):
    """Test AgentMetrics computed properties."""

    def setUp(self):
        self.bench = _import_bench()

    def test_ac_pass_rate_perfect(self):
        m = self.bench.AgentMetrics(agent_id="dev")
        m.ac_pass_count = 10
        m.ac_fail_count = 0
        self.assertEqual(m.ac_pass_rate, 100.0)

    def test_ac_pass_rate_50_50(self):
        m = self.bench.AgentMetrics(agent_id="dev")
        m.ac_pass_count = 5
        m.ac_fail_count = 5
        self.assertEqual(m.ac_pass_rate, 50.0)

    def test_ac_pass_rate_zero_tests(self):
        m = self.bench.AgentMetrics(agent_id="dev")
        self.assertEqual(m.ac_pass_rate, 0.0)

    def test_activity_score_empty(self):
        m = self.bench.AgentMetrics(agent_id="idle")
        self.assertEqual(m.activity_score, 0)

    def test_activity_score_active(self):
        m = self.bench.AgentMetrics(agent_id="active")
        m.stories_touched = {"s1", "s2", "s3"}
        m.decisions_count = 5
        m.ac_pass_count = 8
        m.ac_fail_count = 2
        m.learnings_count = 3
        m.commits_attributed = 4
        score = m.activity_score
        self.assertGreater(score, 40)
        self.assertLessEqual(score, 100)

    def test_activity_score_capped_at_100(self):
        m = self.bench.AgentMetrics(agent_id="max")
        m.stories_touched = {"s" + str(i) for i in range(20)}
        m.decisions_count = 30
        m.ac_pass_count = 50
        m.ac_fail_count = 0
        m.learnings_count = 20
        m.commits_attributed = 20
        self.assertLessEqual(m.activity_score, 100)


class TestParseTrace(unittest.TestCase):
    """Test parse_trace() — Grimoire_TRACE parsing."""

    def setUp(self):
        self.bench = _import_bench()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_parse_empty_trace(self):
        f = self.tmpdir / "Grimoire_TRACE.md"
        f.write_text("")
        session = self.bench.parse_trace(f)
        self.assertEqual(session.total_entries, 0)
        self.assertEqual(len(session.agents), 0)

    def test_parse_missing_trace(self):
        session = self.bench.parse_trace(self.tmpdir / "nonexistent.md")
        self.assertIsNone(session.period_start)

    def test_parse_basic_entries(self):
        f = self.tmpdir / "Grimoire_TRACE.md"
        f.write_text(
            "## 2026-01-15 14:30 | forge | STORY-001\n"
            "[GIT-COMMIT] feat: added monitoring stack\n"
            "\n"
            "## 2026-01-15 15:00 | hawk | STORY-002\n"
            "[DECISION] Use Prometheus over Datadog\n"
            "\n"
            "## 2026-01-16 10:00 | forge | STORY-001\n"
            "[FAILURE] docker build context too large\n"
            "\n"
            "## 2026-01-16 11:00 | dev | STORY-003\n"
            "[AC-PASS] All unit tests pass\n"
        )
        session = self.bench.parse_trace(f)
        self.assertEqual(session.total_entries, 4)
        self.assertIn("forge", session.agents)
        self.assertIn("hawk", session.agents)
        self.assertIn("dev", session.agents)
        self.assertEqual(session.total_commits, 1)
        self.assertEqual(session.total_decisions, 1)
        self.assertEqual(session.total_failures, 1)
        self.assertEqual(session.agents["forge"].commits_attributed, 1)
        self.assertEqual(session.agents["forge"].failures_count, 1)
        self.assertEqual(session.agents["hawk"].decisions_count, 1)

    def test_agent_filter(self):
        f = self.tmpdir / "Grimoire_TRACE.md"
        f.write_text(
            "## 2026-01-15 14:30 | forge | STORY-001\n"
            "[GIT-COMMIT] feat: added X\n"
            "\n"
            "## 2026-01-15 15:00 | hawk | STORY-002\n"
            "[DECISION] Chose Y\n"
        )
        session = self.bench.parse_trace(f, agent_filter="forge")
        self.assertEqual(session.total_entries, 1)
        self.assertIn("forge", session.agents)
        self.assertNotIn("hawk", session.agents)

    def test_since_filter(self):
        f = self.tmpdir / "Grimoire_TRACE.md"
        f.write_text(
            "## 2025-01-01 10:00 | dev | old-story\n"
            "[GIT-COMMIT] ancient commit\n"
            "\n"
            "## 2026-06-01 10:00 | dev | new-story\n"
            "[GIT-COMMIT] recent commit\n"
        )
        session = self.bench.parse_trace(f, since="2026-01-01")
        # Only recent entry
        self.assertEqual(session.total_commits, 1)

    def test_story_cycle_times(self):
        f = self.tmpdir / "Grimoire_TRACE.md"
        f.write_text(
            "## 2026-01-01 10:00 | dev | STORY-X\n"
            "[GIT-COMMIT] started\n"
            "\n"
            "## 2026-01-03 10:00 | dev | STORY-X\n"
            "[GIT-COMMIT] finished\n"
        )
        session = self.bench.parse_trace(f)
        self.assertIn("STORY-X", session.story_cycle_times)
        self.assertAlmostEqual(session.story_cycle_times["STORY-X"], 2.0, places=0)

    def test_failure_categorization(self):
        f = self.tmpdir / "Grimoire_TRACE.md"
        f.write_text(
            "## 2026-01-01 | dev | S1\n"
            "[FAILURE] pytest error: test_login failed\n"
        )
        session = self.bench.parse_trace(f)
        self.assertIn("test-failure", session.failure_patterns)

    def test_checkpoint_count(self):
        f = self.tmpdir / "Grimoire_TRACE.md"
        f.write_text(
            "## 2026-01-01 | dev | S1\n"
            "[CHECKPOINT] checkpoint_id: ckpt-001\n"
        )
        session = self.bench.parse_trace(f)
        self.assertEqual(session.total_checkpoints, 1)


class TestAutoRecommendations(unittest.TestCase):
    """Test _auto_recommendations()."""

    def setUp(self):
        self.bench = _import_bench()

    def test_no_anomalies(self):
        session = self.bench.SessionMetrics(period_start=None, period_end=None)
        recs = self.bench._auto_recommendations(session)
        self.assertTrue(any("Aucune anomalie" in r for r in recs))

    def test_recurring_failures(self):
        session = self.bench.SessionMetrics(period_start=None, period_end=None)
        session.failure_patterns = {"test-failure": 8}
        recs = self.bench._auto_recommendations(session)
        self.assertTrue(any("test-failure" in r for r in recs))

    def test_long_cycle_times(self):
        session = self.bench.SessionMetrics(period_start=None, period_end=None)
        session.story_cycle_times = {"S1": 20.0, "S2": 15.0}
        recs = self.bench._auto_recommendations(session)
        self.assertTrue(any("14 jours" in r for r in recs))

    def test_silent_agents(self):
        session = self.bench.SessionMetrics(period_start=None, period_end=None)
        silent = self.bench.AgentMetrics(agent_id="ghost")
        silent.stories_touched = {"s1", "s2"}
        silent.decisions_count = 0
        silent.commits_attributed = 0
        session.agents = {"ghost": silent}
        recs = self.bench._auto_recommendations(session)
        self.assertTrue(any("ghost" in r for r in recs))


class TestSummaryLine(unittest.TestCase):
    """Test summary_line() output."""

    def setUp(self):
        self.bench = _import_bench()

    def test_prints_header_and_agents(self):
        session = self.bench.SessionMetrics(period_start=None, period_end=None)
        ag = self.bench.AgentMetrics(agent_id="dev")
        ag.stories_touched = {"s1"}
        ag.decisions_count = 3
        ag.commits_attributed = 2
        session.agents = {"dev": ag}

        captured = StringIO()
        with patch("sys.stdout", captured):
            self.bench.summary_line(session)
        output = captured.getvalue()
        self.assertIn("Agent", output)
        self.assertIn("dev", output)
        self.assertIn("Score", output)


if __name__ == "__main__":
    unittest.main()
