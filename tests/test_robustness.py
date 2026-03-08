#!/usr/bin/env python3
"""
Tests de robustesse — corruption JSON, champs manquants, edge cases.

Vérifie que les outils du Nervous System résistent gracieusement à :
  - Fichiers JSON corrompus (syntaxe invalide)
  - Champs manquants ou types inattendus
  - Valeurs hors limites (confiance > 1.0, intensité négative)
  - Fichiers vides ou inaccessibles
  - Entrées avec caractères Unicode exotiques
  - Timestamps invalides ou dans le futur
"""

import importlib
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

KIT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(KIT_DIR / "framework" / "tools"))


def _import(name):
    return importlib.import_module(name)


# ── Dream — Robustesse JSON ──────────────────────────────────────────────────

class TestDreamCorruptedMemory(unittest.TestCase):
    """Dream doit gérer un dream-memory.json corrompu."""

    def setUp(self):
        self.mod = _import("dream")
        self.tmpdir = Path(tempfile.mkdtemp())
        (self.tmpdir / "_grimoire-output").mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_corrupted_json_returns_empty(self):
        mem_path = self.tmpdir / "_grimoire-output" / "dream-memory.json"
        mem_path.write_text("{invalid json;;;", encoding="utf-8")
        memory = self.mod.load_dream_memory(self.tmpdir)
        self.assertEqual(memory, {})

    def test_empty_file_returns_empty(self):
        mem_path = self.tmpdir / "_grimoire-output" / "dream-memory.json"
        mem_path.write_text("", encoding="utf-8")
        memory = self.mod.load_dream_memory(self.tmpdir)
        self.assertEqual(memory, {})

    def test_nonexistent_returns_empty(self):
        memory = self.mod.load_dream_memory(self.tmpdir)
        self.assertEqual(memory, {})

    def test_save_then_load_roundtrip(self):
        memory = {"insights": {}, "total_dreams": 1, "last_dream": "2026-02-28"}
        self.mod.save_dream_memory(self.tmpdir, memory)
        loaded = self.mod.load_dream_memory(self.tmpdir)
        self.assertEqual(loaded["total_dreams"], 1)


class TestDreamEdgeCaseInsights(unittest.TestCase):
    """Dream handles edge case insights gracefully."""

    def setUp(self):
        self.mod = _import("dream")

    def test_confidence_above_one_capped(self):
        """update_dream_memory should cap confidence ≤ 1.0."""
        i = self.mod.DreamInsight(
            title="T", description="D", sources=["a.md"],
            category="pattern", confidence=0.95,
        )
        memory: dict = {}
        # First pass: new
        self.mod.update_dream_memory([i], memory)
        # Second pass: persistent → boost 0.95 + 0.15 should be capped at 1.0
        i2 = self.mod.DreamInsight(
            title="T", description="D", sources=["a.md"],
            category="pattern", confidence=0.95,
        )
        result = self.mod.update_dream_memory([i2], memory)
        for ins in result["persistent"]:
            self.assertLessEqual(ins.confidence, 1.0)

    def test_empty_title_insight(self):
        """Insight with empty title should still get a signature."""
        i = self.mod.DreamInsight(
            title="", description="D", sources=["a.md"],
            category="pattern", confidence=0.5,
        )
        sig = self.mod._insight_signature(i)
        self.assertTrue(sig.startswith("pattern:"))

    def test_unicode_in_entries(self):
        """Keywords extraction handles Unicode characters."""
        src = self.mod.DreamSource(
            name="a.md", kind="learnings",
            entries=["améliorer l'architecture réseau 日本語テスト"],
        )
        keywords = self.mod._extract_keywords(src.entries[0])
        self.assertIn("améliorer", keywords)
        self.assertIn("architecture", keywords)
        self.assertIn("réseau", keywords)

    def test_very_long_entry(self):
        """Extremely long entries don't crash."""
        long_entry = "caching " * 10000
        keywords = self.mod._extract_keywords(long_entry)
        self.assertIn("caching", keywords)


class TestDreamTemporalEdgeCases(unittest.TestCase):
    """Temporal weight edge cases."""

    def setUp(self):
        self.mod = _import("dream")

    def test_future_date_returns_one(self):
        """A date in the future should return 1.0."""
        future = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        w = self.mod._temporal_weight(future)
        self.assertEqual(w, 1.0)

    def test_garbage_date(self):
        """Garbage date returns 1.0 (no penalty)."""
        w = self.mod._temporal_weight("not-a-date")
        self.assertEqual(w, 1.0)

    def test_partial_date(self):
        """Partial date string returns 1.0."""
        w = self.mod._temporal_weight("2026-02")
        self.assertEqual(w, 1.0)

    def test_negative_days(self):
        """Technically impossible but shouldn't crash."""
        from datetime import datetime as dt
        w = self.mod._temporal_weight("2099-12-31", now=dt(2026, 1, 1))
        self.assertEqual(w, 1.0)


# ── Stigmergy — Robustesse JSON ──────────────────────────────────────────────

class TestStigmeryCorruptedBoard(unittest.TestCase):
    """Stigmergy handles corrupt pheromone-board.json."""

    def setUp(self):
        self.mod = _import("stigmergy")
        self.tmpdir = Path(tempfile.mkdtemp())
        (self.tmpdir / "_grimoire-output").mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_corrupted_json_returns_default_board(self):
        path = self.tmpdir / "_grimoire-output" / "pheromone-board.json"
        path.write_text("not valid json {{{{", encoding="utf-8")
        board = self.mod.load_board(self.tmpdir)
        self.assertEqual(len(board.pheromones), 0)
        self.assertEqual(board.total_emitted, 0)

    def test_empty_file_returns_default(self):
        path = self.tmpdir / "_grimoire-output" / "pheromone-board.json"
        path.write_text("", encoding="utf-8")
        board = self.mod.load_board(self.tmpdir)
        self.assertIsNotNone(board)

    def test_missing_fields_handled(self):
        path = self.tmpdir / "_grimoire-output" / "pheromone-board.json"
        path.write_text('{"pheromones": [{}]}', encoding="utf-8")
        board = self.mod.load_board(self.tmpdir)
        self.assertEqual(len(board.pheromones), 1)
        # Defaults should be applied
        self.assertEqual(board.pheromones[0].pheromone_type, "NEED")
        self.assertEqual(board.pheromones[0].resolved, False)

    def test_nonexistent_file(self):
        board = self.mod.load_board(self.tmpdir)
        self.assertIsNotNone(board)
        self.assertEqual(len(board.pheromones), 0)


class TestStigmeryEdgeCases(unittest.TestCase):
    """Edge cases for stigmergy operations."""

    def setUp(self):
        self.mod = _import("stigmergy")
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_emit_clamps_intensity(self):
        board = self.mod.PheromoneBoard()
        p = self.mod.emit_pheromone(board, "NEED", "test", "text", "dev",
                                     intensity=5.0)
        self.assertLessEqual(p.intensity, 1.0)

    def test_emit_negative_intensity(self):
        board = self.mod.PheromoneBoard()
        p = self.mod.emit_pheromone(board, "NEED", "test", "text", "dev",
                                     intensity=-1.0)
        self.assertGreaterEqual(p.intensity, 0.0)

    def test_amplify_nonexistent_returns_none(self):
        board = self.mod.PheromoneBoard()
        result = self.mod.amplify_pheromone(board, "PH-nonexist", "dev")
        self.assertIsNone(result)

    def test_evaporate_empty_board(self):
        board = self.mod.PheromoneBoard()
        _board, evaporated = self.mod.evaporate(board)
        self.assertEqual(evaporated, 0)

    def test_pheromone_bad_timestamp(self):
        """compute_current_intensity with garbage timestamp."""
        p = self.mod.Pheromone(
            pheromone_id="PH-test",
            pheromone_type="NEED",
            location="test",
            text="test",
            emitter="dev",
            timestamp="not-a-timestamp",
            intensity=0.7,
        )
        intensity = self.mod.compute_current_intensity(
            p, self.mod.DEFAULT_HALF_LIFE_HOURS)
        # Should return original intensity on bad timestamp
        self.assertEqual(intensity, 0.7)


# ── Memory Lint — Robustesse ─────────────────────────────────────────────────

class TestMemoryLintRobustness(unittest.TestCase):
    """Memory lint handles edge cases."""

    def setUp(self):
        self.mod = _import("memory-lint")
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_unreadable_file_skipped(self):
        """If a memory file is unreadable, lint doesn't crash."""
        mem = self.tmpdir / "_grimoire" / "_memory" / "agent-learnings"
        mem.mkdir(parents=True)
        # Create a directory with the name of a file (can't be read as text)
        (mem / "dev.md").mkdir()
        files = self.mod.collect_memory_files(self.tmpdir)
        # Should skip the directory, not crash
        self.assertIsInstance(files, list)

    def test_empty_entries_no_crash(self):
        """Files with no parseable entries are skipped."""
        mem = self.tmpdir / "_grimoire" / "_memory"
        mem.mkdir(parents=True)
        (mem / "decisions-log.md").write_text("# Decisions\n\nNo entries yet.\n",
                                              encoding="utf-8")
        report = self.mod.lint_memory(self.tmpdir)
        self.assertEqual(report.error_count, 0)

    def test_unicode_entries(self):
        """Unicode content doesn't crash the linter."""
        files = [
            self.mod.MemoryFile(
                path="a.md", kind="learnings",
                entries=[("2026-02-01", "améliorer l'API après échec 日本語")],
            ),
        ]
        # Should not crash
        issues = self.mod.check_contradictions(files)
        self.assertIsInstance(issues, list)

    def test_very_many_entries_performance(self):
        """Many entries don't cause issues (within reason)."""
        entries = [(f"2026-02-{(i % 28) + 1:02d}", f"entry number {i}")
                   for i in range(100)]
        files = [
            self.mod.MemoryFile(path="a.md", kind="learnings", entries=entries),
            self.mod.MemoryFile(path="b.md", kind="decisions", entries=entries),
        ]
        # Should complete without hanging
        issues = self.mod.check_duplicates(files)
        self.assertIsInstance(issues, list)


# ── Dream — Similarity edge cases ────────────────────────────────────────────

class TestSimilarityEdgeCases(unittest.TestCase):
    """Similarity function edge cases."""

    def setUp(self):
        self.mod = _import("dream")

    def test_both_empty(self):
        self.assertEqual(self.mod._similarity("", ""), 0.0)

    def test_one_empty(self):
        self.assertEqual(self.mod._similarity("caching layer", ""), 0.0)

    def test_only_stopwords(self):
        self.assertEqual(self.mod._similarity("the is and", "of to in"), 0.0)

    def test_special_characters_only(self):
        self.assertEqual(self.mod._similarity("!@#$%", "^&*()"), 0.0)

    def test_numbers_ignored(self):
        """Numbers < 3 chars are excluded from keywords."""
        kw = self.mod._extract_keywords("v2 3x faster 42 items")
        self.assertNotIn("v2", kw)
        self.assertNotIn("42", kw)
        self.assertIn("faster", kw)
        self.assertIn("items", kw)


# ── Dream — DreamSource edge cases ───────────────────────────────────────────

class TestDreamSourceEdgeCases(unittest.TestCase):
    """DreamSource handling of edge data."""

    def setUp(self):
        self.mod = _import("dream")

    def test_source_with_no_entries(self):
        src = self.mod.DreamSource(name="empty.md", kind="learnings")
        self.assertEqual(src.entries, [])
        self.assertEqual(src.dates, [])

    def test_find_patterns_with_all_unique(self):
        """No patterns when every entry is unique."""
        srcs = [
            self.mod.DreamSource(
                name="a.md", kind="learnings",
                entries=["alpha unique entry xyz"],
            ),
            self.mod.DreamSource(
                name="b.md", kind="decisions",
                entries=["beta different content abc"],
            ),
        ]
        insights = self.mod.find_recurring_patterns(srcs)
        self.assertEqual(insights, [])

    def test_find_tensions_empty_sources(self):
        insights = self.mod.find_tensions([])
        self.assertEqual(insights, [])

    def test_find_opportunities_empty_sources(self):
        insights = self.mod.find_opportunities([])
        self.assertEqual(insights, [])

    def test_find_cross_connections_single_source(self):
        """Cross-connections require 2+ sources of different types."""
        src = self.mod.DreamSource(
            name="a.md", kind="learnings",
            entries=["entry one", "entry two"],
        )
        insights = self.mod.find_cross_connections([src])
        self.assertEqual(insights, [])


# ── NSO — Robustesse ─────────────────────────────────────────────────────────

class TestNSORobustness(unittest.TestCase):
    """NSO handles missing tools and corrupted state."""

    def setUp(self):
        self.mod = _import("nso")
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_full_run_on_empty_project(self):
        """NSO should not crash on a completely empty project."""
        report = self.mod.run_nso(self.tmpdir)
        self.assertIsInstance(report, self.mod.NSOReport)
        # All phases should have run (or skipped gracefully)
        self.assertEqual(len(report.phases), 5)

    def test_corrupted_dream_memory(self):
        """NSO handles corrupted dream-memory.json."""
        out = self.tmpdir / "_grimoire-output"
        out.mkdir(parents=True)
        (out / "dream-memory.json").write_text("CORRUPT", encoding="utf-8")
        report = self.mod.run_nso(self.tmpdir)
        # Dream phase should not crash
        dream_phase = next(p for p in report.phases if p.name == "dream")
        self.assertIn(dream_phase.status, ("ok", "error"))

    def test_corrupted_pheromone_board(self):
        """NSO handles corrupted pheromone-board.json."""
        out = self.tmpdir / "_grimoire-output"
        out.mkdir(parents=True)
        (out / "pheromone-board.json").write_text("<<<BROKEN>>>",
                                                   encoding="utf-8")
        report = self.mod.run_nso(self.tmpdir)
        stigm_phase = next(p for p in report.phases if p.name == "stigmergy")
        self.assertIn(stigm_phase.status, ("ok", "error", "skip"))


if __name__ == "__main__":
    unittest.main()
