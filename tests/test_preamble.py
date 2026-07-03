"""Tests for grimoire.core.preamble — dynamic context assembly."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from grimoire.core.preamble import PreambleBuilder, PreambleConfig


class TestPreambleConfig(unittest.TestCase):
    def test_defaults(self) -> None:
        cfg = PreambleConfig()
        self.assertEqual(cfg.max_learnings, 5)
        self.assertEqual(cfg.max_session_entries, 3)
        self.assertEqual(cfg.max_telemetry_entries, 5)
        self.assertTrue(cfg.include_learnings)
        self.assertTrue(cfg.include_session_chain)
        self.assertTrue(cfg.include_telemetry)
        self.assertTrue(cfg.include_vitals)

    def test_custom_values(self) -> None:
        cfg = PreambleConfig(max_learnings=10, include_telemetry=False)
        self.assertEqual(cfg.max_learnings, 10)
        self.assertFalse(cfg.include_telemetry)

    def test_frozen(self) -> None:
        cfg = PreambleConfig()
        with self.assertRaises(AttributeError):
            cfg.max_learnings = 99  # type: ignore[misc]


class TestPreambleBuilderEmpty(unittest.TestCase):
    """Preamble with no data files returns empty string."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_build_returns_empty_when_no_data(self) -> None:
        builder = PreambleBuilder(self.root)
        result = builder.build()
        self.assertEqual(result, "")

    def test_build_empty_with_all_disabled(self) -> None:
        cfg = PreambleConfig(
            include_vitals=False,
            include_session_chain=False,
            include_learnings=False,
            include_telemetry=False,
        )
        builder = PreambleBuilder(self.root, config=cfg)
        result = builder.build()
        self.assertEqual(result, "")


class TestPreambleBuilderVitals(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        ctx_path = self.root / "project-context.yaml"
        ctx_path.write_text(
            "project:\n  name: test-grimoire\n  version: 1.0.0\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_vitals_includes_project_name(self) -> None:
        cfg = PreambleConfig(
            include_session_chain=False,
            include_learnings=False,
            include_telemetry=False,
        )
        builder = PreambleBuilder(self.root, config=cfg)
        result = builder.build()
        self.assertIn("PREAMBLE:START", result)
        self.assertIn("test-grimoire", result)
        self.assertIn("PREAMBLE:END", result)

    def test_vitals_includes_timestamp(self) -> None:
        cfg = PreambleConfig(
            include_session_chain=False,
            include_learnings=False,
            include_telemetry=False,
        )
        builder = PreambleBuilder(self.root, config=cfg)
        result = builder.build()
        self.assertIn("Timestamp", result)


class TestPreambleBuilderSessionChain(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        chain_dir = self.root / "_grimoire" / "_memory"
        chain_dir.mkdir(parents=True)
        chain_file = chain_dir / "session-chain.jsonl"
        entries = [
            {"timestamp": "2025-01-01T10:00:00Z", "phase": "dev", "status": "ok", "summaries": ["did stuff"]},
            {"timestamp": "2025-01-02T10:00:00Z", "phase": "qa", "status": "ok", "summaries": ["tested"]},
            {"timestamp": "2025-01-03T10:00:00Z", "phase": "release", "status": "fail", "summaries": ["broke"]},
        ]
        chain_file.write_text(
            "\n".join(json.dumps(e) for e in entries) + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_session_chain_present(self) -> None:
        cfg = PreambleConfig(
            include_vitals=False,
            include_learnings=False,
            include_telemetry=False,
            max_session_entries=2,
        )
        builder = PreambleBuilder(self.root, config=cfg)
        result = builder.build()
        self.assertIn("Session History", result)
        # Should show last 2 entries
        self.assertIn("qa", result)
        self.assertIn("release", result)

    def test_session_chain_respects_limit(self) -> None:
        cfg = PreambleConfig(
            include_vitals=False,
            include_learnings=False,
            include_telemetry=False,
            max_session_entries=1,
        )
        builder = PreambleBuilder(self.root, config=cfg)
        result = builder.build()
        # Only the last entry
        self.assertIn("release", result)
        self.assertNotIn("dev", result)


class TestPreambleBuilderLearnings(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        learn_dir = self.root / "_grimoire" / "_memory" / "learnings"
        learn_dir.mkdir(parents=True)
        learn_file = learn_dir / "operational.jsonl"
        entries = [
            {"key": "ruff-config", "insight": "always use line-length 120", "confidence": 90, "skill": "grimoire-tdd"},
            {"key": "test-isolation", "insight": "use tmp dirs", "confidence": 80, "skill": "grimoire-tdd"},
            {"key": "docker-layer", "insight": "cache pip layers", "confidence": 70, "skill": ""},
            {"key": "low-prio", "insight": "something minor", "confidence": 20, "skill": "other"},
        ]
        learn_file.write_text(
            "\n".join(json.dumps(e) for e in entries) + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_learnings_sorted_by_confidence(self) -> None:
        cfg = PreambleConfig(
            include_vitals=False,
            include_session_chain=False,
            include_telemetry=False,
            max_learnings=3,
        )
        builder = PreambleBuilder(self.root, config=cfg)
        result = builder.build()
        self.assertIn("Operational Learnings", result)
        self.assertIn("ruff-config", result)
        self.assertIn("test-isolation", result)
        self.assertIn("docker-layer", result)
        self.assertNotIn("low-prio", result)

    def test_learnings_filtered_by_skill(self) -> None:
        cfg = PreambleConfig(
            include_vitals=False,
            include_session_chain=False,
            include_telemetry=False,
            max_learnings=2,
        )
        builder = PreambleBuilder(self.root, config=cfg)
        result = builder.build(skill="grimoire-tdd")
        # Skill-specific entries should appear first
        self.assertIn("ruff-config", result)
        self.assertIn("test-isolation", result)


class TestPreambleBuilderTelemetry(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        telem_dir = self.root / "_grimoire" / "_memory" / "telemetry"
        telem_dir.mkdir(parents=True)
        telem_file = telem_dir / "skill-usage.jsonl"
        entries = [
            {"skill": "grimoire-tdd", "timestamp": "2025-01-01T10:00:00Z", "outcome": "success"},
            {"skill": "grimoire-tdd", "timestamp": "2025-01-02T10:00:00Z", "outcome": "failure"},
            {"skill": "grimoire-debug", "timestamp": "2025-01-03T10:00:00Z", "outcome": "success"},
        ]
        telem_file.write_text(
            "\n".join(json.dumps(e) for e in entries) + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_telemetry_present(self) -> None:
        cfg = PreambleConfig(
            include_vitals=False,
            include_session_chain=False,
            include_learnings=False,
        )
        builder = PreambleBuilder(self.root, config=cfg)
        result = builder.build()
        self.assertIn("Recent Skill Usage", result)

    def test_telemetry_filtered_by_skill(self) -> None:
        cfg = PreambleConfig(
            include_vitals=False,
            include_session_chain=False,
            include_learnings=False,
        )
        builder = PreambleBuilder(self.root, config=cfg)
        result = builder.build(skill="grimoire-tdd")
        self.assertIn("grimoire-tdd", result)


class TestPreambleBuilderFull(unittest.TestCase):
    """Full integration — all sections present."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        # project-context.yaml
        (self.root / "project-context.yaml").write_text(
            "project:\n  name: full-test\n",
            encoding="utf-8",
        )
        # Session chain
        chain_dir = self.root / "_grimoire" / "_memory"
        chain_dir.mkdir(parents=True)
        (chain_dir / "session-chain.jsonl").write_text(
            json.dumps({"timestamp": "2025-01-01T10:00:00Z", "phase": "dev", "status": "ok", "summaries": ["init"]})
            + "\n",
            encoding="utf-8",
        )
        # Learnings
        learn_dir = chain_dir / "learnings"
        learn_dir.mkdir(parents=True)
        (learn_dir / "operational.jsonl").write_text(
            json.dumps({"key": "test-key", "insight": "test insight", "confidence": 95}) + "\n",
            encoding="utf-8",
        )
        # Telemetry
        telem_dir = chain_dir / "telemetry"
        telem_dir.mkdir(parents=True)
        (telem_dir / "skill-usage.jsonl").write_text(
            json.dumps({"skill": "grimoire-tdd", "timestamp": "2025-01-01T10:00:00Z", "outcome": "success"}) + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_all_sections_present(self) -> None:
        builder = PreambleBuilder(self.root)
        result = builder.build()
        self.assertIn("PREAMBLE:START", result)
        self.assertIn("Project Vitals", result)
        self.assertIn("Session History", result)
        self.assertIn("Operational Learnings", result)
        self.assertIn("Recent Skill Usage", result)
        self.assertIn("PREAMBLE:END", result)

    def test_markers_wrap_content(self) -> None:
        builder = PreambleBuilder(self.root)
        result = builder.build()
        self.assertTrue(result.startswith("<!-- PREAMBLE:START -->"))
        self.assertTrue(result.endswith("<!-- PREAMBLE:END -->"))


class TestLoadJsonl(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_load_jsonl_returns_last_n(self) -> None:
        path = self.tmp_dir / "test.jsonl"
        entries = [{"i": i} for i in range(10)]
        path.write_text(
            "\n".join(json.dumps(e) for e in entries) + "\n",
            encoding="utf-8",
        )
        result = PreambleBuilder._load_jsonl(path, limit=3)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["i"], 7)

    def test_load_jsonl_zero_returns_all(self) -> None:
        path = self.tmp_dir / "test.jsonl"
        entries = [{"i": i} for i in range(5)]
        path.write_text(
            "\n".join(json.dumps(e) for e in entries) + "\n",
            encoding="utf-8",
        )
        result = PreambleBuilder._load_jsonl(path, limit=0)
        self.assertEqual(len(result), 5)

    def test_load_jsonl_missing_file(self) -> None:
        result = PreambleBuilder._load_jsonl(self.tmp_dir / "nope.jsonl")
        self.assertEqual(result, [])

    def test_load_jsonl_corrupted(self) -> None:
        path = self.tmp_dir / "bad.jsonl"
        path.write_text("not json\n", encoding="utf-8")
        result = PreambleBuilder._load_jsonl(path)
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
