"""Tests for grimoire.core.telemetry — JSONL-based skill and tool usage analytics."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from grimoire.core.telemetry import Telemetry, TelemetryEntry


class TestTelemetryEntry(unittest.TestCase):
    def test_defaults(self) -> None:
        e = TelemetryEntry(event_type="skill")
        self.assertEqual(e.event_type, "skill")
        self.assertEqual(e.skill, "")
        self.assertEqual(e.tool, "")
        self.assertEqual(e.outcome, "")
        self.assertAlmostEqual(e.duration_s, 0.0)
        self.assertEqual(e.message, "")
        self.assertIsNone(e.metadata)
        self.assertEqual(e.timestamp, "")

    def test_to_dict_minimal(self) -> None:
        e = TelemetryEntry(event_type="session", timestamp="2025-01-01T00:00:00Z", outcome="ok")
        d = e.to_dict()
        self.assertEqual(d["event_type"], "session")
        self.assertEqual(d["timestamp"], "2025-01-01T00:00:00Z")
        self.assertEqual(d["outcome"], "ok")
        self.assertNotIn("skill", d)
        self.assertNotIn("tool", d)
        self.assertNotIn("duration_s", d)

    def test_to_dict_full(self) -> None:
        e = TelemetryEntry(
            event_type="skill",
            skill="grimoire-tdd",
            outcome="success",
            duration_s=3.14,
            message="all passed",
            metadata={"tests": 7},
            timestamp="2025-01-01T00:00:00Z",
        )
        d = e.to_dict()
        self.assertEqual(d["skill"], "grimoire-tdd")
        self.assertAlmostEqual(d["duration_s"], 3.14)
        self.assertEqual(d["message"], "all passed")
        self.assertEqual(d["metadata"]["tests"], 7)

    def test_frozen(self) -> None:
        e = TelemetryEntry(event_type="tool")
        with self.assertRaises(AttributeError):
            e.event_type = "changed"  # type: ignore[misc]


class TestTelemetryRecordSkill(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.telem = Telemetry(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_record_creates_file(self) -> None:
        self.telem.record_skill("grimoire-tdd", outcome="success")
        jsonl = self.root / "_grimoire" / "_memory" / "telemetry" / "skill-usage.jsonl"
        self.assertTrue(jsonl.exists())

    def test_record_returns_entry(self) -> None:
        entry = self.telem.record_skill("grimoire-tdd", outcome="success", duration_s=1.5)
        self.assertIsInstance(entry, TelemetryEntry)
        self.assertEqual(entry.event_type, "skill")
        self.assertEqual(entry.skill, "grimoire-tdd")
        self.assertTrue(entry.timestamp)

    def test_multiple_records(self) -> None:
        self.telem.record_skill("a")
        self.telem.record_skill("b")
        self.telem.record_skill("c")
        self.assertEqual(self.telem.count(), 3)


class TestTelemetryRecordTool(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.telem = Telemetry(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_record_tool(self) -> None:
        entry = self.telem.record_tool("ruff", outcome="failure", message="3 errors")
        self.assertEqual(entry.event_type, "tool")
        self.assertEqual(entry.tool, "ruff")
        self.assertEqual(entry.outcome, "failure")

    def test_tool_with_skill(self) -> None:
        entry = self.telem.record_tool("pytest", skill="grimoire-tdd")
        self.assertEqual(entry.skill, "grimoire-tdd")


class TestTelemetryRecordSession(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.telem = Telemetry(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_record_session(self) -> None:
        entry = self.telem.record_session(outcome="completed", duration_s=120.0)
        self.assertEqual(entry.event_type, "session")
        self.assertEqual(entry.outcome, "completed")
        self.assertAlmostEqual(entry.duration_s, 120.0)


class TestTelemetryRecent(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.telem = Telemetry(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_recent_empty(self) -> None:
        result = self.telem.recent()
        self.assertEqual(result, [])

    def test_recent_returns_last_n(self) -> None:
        for i in range(5):
            self.telem.record_skill(f"skill-{i}")
        result = self.telem.recent(limit=3)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[-1]["skill"], "skill-4")

    def test_recent_filtered_by_skill(self) -> None:
        self.telem.record_skill("a")
        self.telem.record_skill("b")
        self.telem.record_skill("a")
        result = self.telem.recent(skill="a")
        self.assertEqual(len(result), 2)
        self.assertTrue(all(e["skill"] == "a" for e in result))

    def test_recent_filtered_by_event_type(self) -> None:
        self.telem.record_skill("x")
        self.telem.record_tool("ruff")
        self.telem.record_session()
        result = self.telem.recent(event_type="tool")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["event_type"], "tool")


class TestTelemetrySkillStats(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.telem = Telemetry(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_empty_stats(self) -> None:
        self.assertEqual(self.telem.skill_stats(), {})

    def test_single_skill(self) -> None:
        self.telem.record_skill("tdd", outcome="success", duration_s=1.0)
        self.telem.record_skill("tdd", outcome="success", duration_s=3.0)
        self.telem.record_skill("tdd", outcome="failure", duration_s=2.0)
        stats = self.telem.skill_stats()
        self.assertIn("tdd", stats)
        self.assertEqual(stats["tdd"]["count"], 3)
        self.assertAlmostEqual(stats["tdd"]["success_rate"], 66.7)
        self.assertAlmostEqual(stats["tdd"]["avg_duration_s"], 2.0)

    def test_multiple_skills(self) -> None:
        self.telem.record_skill("a", outcome="success")
        self.telem.record_skill("b", outcome="failure")
        stats = self.telem.skill_stats()
        self.assertEqual(len(stats), 2)
        self.assertAlmostEqual(stats["a"]["success_rate"], 100.0)
        self.assertAlmostEqual(stats["b"]["success_rate"], 0.0)

    def test_tool_records_excluded(self) -> None:
        self.telem.record_skill("x", outcome="success")
        self.telem.record_tool("ruff", outcome="success")
        stats = self.telem.skill_stats()
        self.assertNotIn("ruff", stats)
        self.assertEqual(len(stats), 1)


class TestTelemetryPrune(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.telem = Telemetry(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_prune_no_op(self) -> None:
        self.telem.record_skill("a")
        removed = self.telem.prune(max_entries=10)
        self.assertEqual(removed, 0)
        self.assertEqual(self.telem.count(), 1)

    def test_prune_removes_oldest(self) -> None:
        for i in range(10):
            self.telem.record_skill(f"skill-{i}")
        removed = self.telem.prune(max_entries=5)
        self.assertEqual(removed, 5)
        self.assertEqual(self.telem.count(), 5)
        # Verify oldest removed
        recent = self.telem.recent(limit=5)
        skills = [e["skill"] for e in recent]
        self.assertNotIn("skill-0", skills)
        self.assertIn("skill-9", skills)


class TestTelemetryCount(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.telem = Telemetry(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_count_empty(self) -> None:
        self.assertEqual(self.telem.count(), 0)

    def test_count_mixed(self) -> None:
        self.telem.record_skill("a")
        self.telem.record_tool("b")
        self.telem.record_session()
        self.assertEqual(self.telem.count(), 3)


class TestTelemetryPersistence(unittest.TestCase):
    """Verify data survives a new Telemetry instance."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_data_survives_reload(self) -> None:
        t1 = Telemetry(self.root)
        t1.record_skill("persistent-skill", outcome="success")
        t2 = Telemetry(self.root)
        self.assertEqual(t2.count(), 1)
        recent = t2.recent()
        self.assertEqual(recent[0]["skill"], "persistent-skill")


if __name__ == "__main__":
    unittest.main()
