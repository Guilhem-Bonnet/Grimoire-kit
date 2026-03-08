#!/usr/bin/env python3
"""
Tests pour reasoning-stream.py — Flux de raisonnement structuré Grimoire.

Fonctions testées :
  - ReasoningEntry (to_dict / from_dict)
  - log_entry()
  - read_stream()
  - update_entry_status()
  - analyze_stream()
  - compact_stream()
  - render_entries()
  - render_analysis()
  - render_stats()
"""

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

KIT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(KIT_DIR / "framework" / "tools"))


def _import_module():
    spec = importlib.util.spec_from_file_location(
        "reasoning_stream",
        KIT_DIR / "framework" / "tools" / "reasoning-stream.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_entry(mod, **kwargs):
    """Créer une ReasoningEntry avec valeurs par défaut."""
    defaults = {
        "timestamp": "2026-01-15T10:30:00",
        "agent": "dev",
        "entry_type": "REASONING",
        "text": "Test entry",
        "context": "",
        "status": "open",
        "confidence": 0.5,
        "related_to": "",
        "tags": [],
    }
    defaults.update(kwargs)
    return mod.ReasoningEntry(**defaults)


def _populate_stream(mod, root, entries):
    """Écrit plusieurs entrées dans le stream."""
    for e in entries:
        mod.log_entry(e, root)


# ── Test ReasoningEntry ───────────────────────────────────────────────────────

class TestReasoningEntry(unittest.TestCase):
    def setUp(self):
        self.mod = _import_module()

    def test_defaults(self):
        e = _make_entry(self.mod)
        self.assertEqual(e.agent, "dev")
        self.assertEqual(e.entry_type, "REASONING")
        self.assertEqual(e.status, "open")
        self.assertEqual(e.confidence, 0.5)
        self.assertEqual(e.tags, [])

    def test_to_dict(self):
        e = _make_entry(self.mod, tags=["arch", "critical"])
        d = e.to_dict()
        self.assertEqual(d["type"], "REASONING")
        self.assertEqual(d["agent"], "dev")
        self.assertEqual(d["tags"], ["arch", "critical"])
        self.assertIn("timestamp", d)

    def test_from_dict(self):
        data = {
            "timestamp": "2026-01-15T10:00:00",
            "agent": "qa",
            "type": "HYPOTHESIS",
            "text": "Test hyp",
            "status": "validated",
            "confidence": 0.8,
            "tags": ["perf"],
        }
        e = self.mod.ReasoningEntry.from_dict(data)
        self.assertEqual(e.agent, "qa")
        self.assertEqual(e.entry_type, "HYPOTHESIS")
        self.assertEqual(e.status, "validated")
        self.assertEqual(e.confidence, 0.8)

    def test_from_dict_defaults(self):
        e = self.mod.ReasoningEntry.from_dict({})
        self.assertEqual(e.agent, "unknown")
        self.assertEqual(e.entry_type, "REASONING")
        self.assertEqual(e.status, "open")

    def test_roundtrip(self):
        e = _make_entry(self.mod, agent="arch", entry_type="DOUBT",
                        text="Are we sure?", confidence=0.3, tags=["api"])
        d = e.to_dict()
        e2 = self.mod.ReasoningEntry.from_dict(d)
        self.assertEqual(e.agent, e2.agent)
        self.assertEqual(e.entry_type, e2.entry_type)
        self.assertEqual(e.text, e2.text)
        self.assertEqual(e.confidence, e2.confidence)


# ── Test log_entry ────────────────────────────────────────────────────────────

class TestLogEntry(unittest.TestCase):
    def setUp(self):
        self.mod = _import_module()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_creates_file(self):
        e = _make_entry(self.mod)
        path = self.mod.log_entry(e, self.tmpdir)
        self.assertTrue(path.exists())

    def test_appends_jsonl(self):
        e1 = _make_entry(self.mod, text="First")
        e2 = _make_entry(self.mod, text="Second")
        self.mod.log_entry(e1, self.tmpdir)
        self.mod.log_entry(e2, self.tmpdir)

        path = self.tmpdir / "_grimoire-output" / "reasoning-stream.jsonl"
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        self.assertEqual(len(lines), 2)

    def test_creates_output_dir(self):
        e = _make_entry(self.mod)
        self.mod.log_entry(e, self.tmpdir)
        self.assertTrue((self.tmpdir / "_grimoire-output").exists())

    def test_entry_is_valid_json(self):
        e = _make_entry(self.mod)
        self.mod.log_entry(e, self.tmpdir)
        path = self.tmpdir / "_grimoire-output" / "reasoning-stream.jsonl"
        data = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(data["agent"], "dev")


# ── Test read_stream ─────────────────────────────────────────────────────────

class TestReadStream(unittest.TestCase):
    def setUp(self):
        self.mod = _import_module()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_empty(self):
        entries = self.mod.read_stream(self.tmpdir)
        self.assertEqual(len(entries), 0)

    def test_reads_all(self):
        entries_data = [
            _make_entry(self.mod, text=f"Entry {i}") for i in range(5)
        ]
        _populate_stream(self.mod, self.tmpdir, entries_data)
        result = self.mod.read_stream(self.tmpdir)
        self.assertEqual(len(result), 5)

    def test_filter_by_agent(self):
        entries = [
            _make_entry(self.mod, agent="dev", text="Dev entry"),
            _make_entry(self.mod, agent="qa", text="QA entry"),
            _make_entry(self.mod, agent="dev", text="Dev entry 2"),
        ]
        _populate_stream(self.mod, self.tmpdir, entries)
        result = self.mod.read_stream(self.tmpdir, agent="dev")
        self.assertEqual(len(result), 2)

    def test_filter_by_type(self):
        entries = [
            _make_entry(self.mod, entry_type="HYPOTHESIS", text="hyp"),
            _make_entry(self.mod, entry_type="DOUBT", text="doubt"),
            _make_entry(self.mod, entry_type="HYPOTHESIS", text="hyp2"),
        ]
        _populate_stream(self.mod, self.tmpdir, entries)
        result = self.mod.read_stream(self.tmpdir, entry_type="HYPOTHESIS")
        self.assertEqual(len(result), 2)

    def test_filter_by_status(self):
        entries = [
            _make_entry(self.mod, status="open", text="a"),
            _make_entry(self.mod, status="validated", text="b"),
            _make_entry(self.mod, status="open", text="c"),
        ]
        _populate_stream(self.mod, self.tmpdir, entries)
        result = self.mod.read_stream(self.tmpdir, status="validated")
        self.assertEqual(len(result), 1)

    def test_filter_by_since(self):
        entries = [
            _make_entry(self.mod, timestamp="2025-06-01T10:00:00", text="old"),
            _make_entry(self.mod, timestamp="2026-03-01T10:00:00", text="new"),
        ]
        _populate_stream(self.mod, self.tmpdir, entries)
        result = self.mod.read_stream(self.tmpdir, since="2026-01-01")
        self.assertEqual(len(result), 1)

    def test_limit(self):
        entries = [_make_entry(self.mod, text=f"E{i}") for i in range(10)]
        _populate_stream(self.mod, self.tmpdir, entries)
        result = self.mod.read_stream(self.tmpdir, limit=3)
        self.assertEqual(len(result), 3)

    def test_handles_corrupt_lines(self):
        path = self.tmpdir / "_grimoire-output" / "reasoning-stream.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "not json\n"
            '{"timestamp":"2026-01-01","agent":"dev","type":"REASONING","text":"ok"}\n',
            encoding="utf-8",
        )
        result = self.mod.read_stream(self.tmpdir)
        self.assertEqual(len(result), 1)

    def test_combined_filters(self):
        entries = [
            _make_entry(self.mod, agent="dev", entry_type="HYPOTHESIS",
                        status="open", text="match"),
            _make_entry(self.mod, agent="dev", entry_type="DOUBT",
                        status="open", text="no match type"),
            _make_entry(self.mod, agent="qa", entry_type="HYPOTHESIS",
                        status="open", text="no match agent"),
        ]
        _populate_stream(self.mod, self.tmpdir, entries)
        result = self.mod.read_stream(self.tmpdir, agent="dev",
                                       entry_type="HYPOTHESIS", status="open")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].text, "match")


# ── Test update_entry_status ─────────────────────────────────────────────────

class TestUpdateEntryStatus(unittest.TestCase):
    def setUp(self):
        self.mod = _import_module()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_update_existing(self):
        e = _make_entry(self.mod, timestamp="2026-01-15T10:00:00", status="open")
        self.mod.log_entry(e, self.tmpdir)

        ok = self.mod.update_entry_status(self.tmpdir, "2026-01-15T10:00:00",
                                           "validated")
        self.assertTrue(ok)

        entries = self.mod.read_stream(self.tmpdir)
        self.assertEqual(entries[0].status, "validated")

    def test_update_nonexistent(self):
        ok = self.mod.update_entry_status(self.tmpdir, "nope", "validated")
        self.assertFalse(ok)

    def test_preserves_other_entries(self):
        e1 = _make_entry(self.mod, timestamp="2026-01-01T00:00:00", text="first")
        e2 = _make_entry(self.mod, timestamp="2026-01-02T00:00:00", text="second")
        _populate_stream(self.mod, self.tmpdir, [e1, e2])

        self.mod.update_entry_status(self.tmpdir, "2026-01-01T00:00:00",
                                      "invalidated")

        entries = self.mod.read_stream(self.tmpdir)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].status, "invalidated")
        self.assertEqual(entries[1].status, "open")


# ── Test analyze_stream ──────────────────────────────────────────────────────

class TestAnalyzeStream(unittest.TestCase):
    def setUp(self):
        self.mod = _import_module()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_empty_stream(self):
        a = self.mod.analyze_stream(self.tmpdir)
        self.assertEqual(a.total_entries, 0)
        self.assertEqual(a.avg_confidence, 0.0)

    def test_counts_by_type(self):
        entries = [
            _make_entry(self.mod, entry_type="HYPOTHESIS"),
            _make_entry(self.mod, entry_type="HYPOTHESIS"),
            _make_entry(self.mod, entry_type="DOUBT"),
        ]
        _populate_stream(self.mod, self.tmpdir, entries)
        a = self.mod.analyze_stream(self.tmpdir)
        self.assertEqual(a.by_type["HYPOTHESIS"], 2)
        self.assertEqual(a.by_type["DOUBT"], 1)

    def test_counts_by_agent(self):
        entries = [
            _make_entry(self.mod, agent="dev"),
            _make_entry(self.mod, agent="qa"),
            _make_entry(self.mod, agent="dev"),
        ]
        _populate_stream(self.mod, self.tmpdir, entries)
        a = self.mod.analyze_stream(self.tmpdir)
        self.assertEqual(a.by_agent["dev"], 2)
        self.assertEqual(a.by_agent["qa"], 1)

    def test_open_hypotheses(self):
        entries = [
            _make_entry(self.mod, entry_type="HYPOTHESIS", status="open",
                        text="hyp1"),
            _make_entry(self.mod, entry_type="HYPOTHESIS", status="validated",
                        text="hyp2"),
        ]
        _populate_stream(self.mod, self.tmpdir, entries)
        a = self.mod.analyze_stream(self.tmpdir)
        self.assertEqual(len(a.open_hypotheses), 1)
        self.assertEqual(a.open_hypotheses[0].text, "hyp1")

    def test_unresolved_doubts(self):
        entries = [
            _make_entry(self.mod, entry_type="DOUBT", status="open",
                        text="doubt1"),
            _make_entry(self.mod, entry_type="DOUBT", status="abandoned",
                        text="doubt2"),
        ]
        _populate_stream(self.mod, self.tmpdir, entries)
        a = self.mod.analyze_stream(self.tmpdir)
        self.assertEqual(len(a.unresolved_doubts), 1)

    def test_unvalidated_assumptions(self):
        entries = [
            _make_entry(self.mod, entry_type="ASSUMPTION", status="open",
                        text="assume1"),
        ]
        _populate_stream(self.mod, self.tmpdir, entries)
        a = self.mod.analyze_stream(self.tmpdir)
        self.assertEqual(len(a.unvalidated_assumptions), 1)

    def test_abandoned_alternatives(self):
        entries = [
            _make_entry(self.mod, entry_type="ALTERNATIVE",
                        status="abandoned", text="alt1"),
        ]
        _populate_stream(self.mod, self.tmpdir, entries)
        a = self.mod.analyze_stream(self.tmpdir)
        self.assertEqual(len(a.abandoned_alternatives), 1)

    def test_reasoning_chains(self):
        entries = [
            _make_entry(self.mod, timestamp="2026-01-01T00:00:00",
                        entry_type="REASONING", text="head"),
            _make_entry(self.mod, timestamp="2026-01-01T00:01:00",
                        entry_type="REASONING", text="follow-up",
                        related_to="2026-01-01T00:00:00"),
        ]
        _populate_stream(self.mod, self.tmpdir, entries)
        a = self.mod.analyze_stream(self.tmpdir)
        self.assertEqual(len(a.reasoning_chains), 1)
        self.assertEqual(len(a.reasoning_chains[0]), 2)

    def test_avg_confidence(self):
        entries = [
            _make_entry(self.mod, confidence=0.2),
            _make_entry(self.mod, confidence=0.8),
        ]
        _populate_stream(self.mod, self.tmpdir, entries)
        a = self.mod.analyze_stream(self.tmpdir)
        self.assertAlmostEqual(a.avg_confidence, 0.5, places=1)

    def test_needs_compaction_flag(self):
        a = self.mod.analyze_stream(self.tmpdir)
        self.assertFalse(a.needs_compaction)

    def test_recommendations_many_hypotheses(self):
        entries = [
            _make_entry(self.mod, entry_type="HYPOTHESIS", status="open",
                        text=f"hyp{i}")
            for i in range(7)
        ]
        _populate_stream(self.mod, self.tmpdir, entries)
        a = self.mod.analyze_stream(self.tmpdir)
        self.assertTrue(any("hypothèse" in r.lower() for r in a.recommendations))

    def test_recommendations_low_confidence(self):
        entries = [
            _make_entry(self.mod, confidence=0.1, text=f"low{i}")
            for i in range(10)
        ]
        _populate_stream(self.mod, self.tmpdir, entries)
        a = self.mod.analyze_stream(self.tmpdir)
        self.assertTrue(any("confiance" in r.lower() for r in a.recommendations))


# ── Test compact_stream ──────────────────────────────────────────────────────

class TestCompactStream(unittest.TestCase):
    def setUp(self):
        self.mod = _import_module()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_empty_stream(self):
        result = self.mod.compact_stream(self.tmpdir, before="2026-06-01")
        self.assertEqual(result["compacted"], 0)

    def test_dry_run(self):
        entries = [
            _make_entry(self.mod, timestamp="2025-01-01T00:00:00", text="old"),
            _make_entry(self.mod, timestamp="2026-06-01T00:00:00", text="new"),
        ]
        _populate_stream(self.mod, self.tmpdir, entries)
        result = self.mod.compact_stream(self.tmpdir, before="2026-01-01",
                                          dry_run=True)
        self.assertEqual(result["compacted"], 1)
        self.assertEqual(result["kept"], 1)
        self.assertTrue(result["dry_run"])

        # Stream non modifié
        all_entries = self.mod.read_stream(self.tmpdir)
        self.assertEqual(len(all_entries), 2)

    def test_actual_compaction(self):
        entries = [
            _make_entry(self.mod, timestamp="2025-01-01T00:00:00",
                        entry_type="HYPOTHESIS", text="old hyp"),
            _make_entry(self.mod, timestamp="2025-06-01T00:00:00",
                        entry_type="DOUBT", text="old doubt"),
            _make_entry(self.mod, timestamp="2026-06-01T00:00:00",
                        entry_type="REASONING", text="new reasoning"),
        ]
        _populate_stream(self.mod, self.tmpdir, entries)

        result = self.mod.compact_stream(self.tmpdir, before="2026-01-01")
        self.assertEqual(result["compacted"], 2)
        self.assertEqual(result["kept"], 1)
        self.assertFalse(result["dry_run"])

        # Vérifier que le stream n'a plus que l'entrée récente
        remaining = self.mod.read_stream(self.tmpdir)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].text, "new reasoning")

        # Vérifier le fichier compact
        compact_path = self.tmpdir / "_grimoire-output" / "reasoning-stream-compacted.md"
        self.assertTrue(compact_path.exists())
        content = compact_path.read_text(encoding="utf-8")
        self.assertIn("Compaction", content)

    def test_nothing_to_compact(self):
        entries = [
            _make_entry(self.mod, timestamp="2026-06-01T00:00:00", text="newer"),
        ]
        _populate_stream(self.mod, self.tmpdir, entries)
        result = self.mod.compact_stream(self.tmpdir, before="2025-01-01")
        self.assertEqual(result["compacted"], 0)


# ── Test render_entries ──────────────────────────────────────────────────────

class TestRenderEntries(unittest.TestCase):
    def setUp(self):
        self.mod = _import_module()

    def test_empty(self):
        result = self.mod.render_entries([])
        self.assertIn("Aucune", result)

    def test_renders_type_icon(self):
        entries = [_make_entry(self.mod, entry_type="HYPOTHESIS")]
        result = self.mod.render_entries(entries)
        self.assertIn("🔬", result)

    def test_renders_doubt_icon(self):
        entries = [_make_entry(self.mod, entry_type="DOUBT")]
        result = self.mod.render_entries(entries)
        self.assertIn("❓", result)

    def test_renders_reasoning_icon(self):
        entries = [_make_entry(self.mod, entry_type="REASONING")]
        result = self.mod.render_entries(entries)
        self.assertIn("🧠", result)

    def test_renders_assumption_icon(self):
        entries = [_make_entry(self.mod, entry_type="ASSUMPTION")]
        result = self.mod.render_entries(entries)
        self.assertIn("📌", result)

    def test_renders_alternative_icon(self):
        entries = [_make_entry(self.mod, entry_type="ALTERNATIVE")]
        result = self.mod.render_entries(entries)
        self.assertIn("🔀", result)

    def test_renders_status(self):
        entries = [_make_entry(self.mod, status="validated")]
        result = self.mod.render_entries(entries)
        self.assertIn("✅", result)

    def test_renders_context(self):
        entries = [_make_entry(self.mod, context="Story #42")]
        result = self.mod.render_entries(entries)
        self.assertIn("Story #42", result)

    def test_renders_tags(self):
        entries = [_make_entry(self.mod, tags=["perf", "api"])]
        result = self.mod.render_entries(entries)
        self.assertIn("perf", result)
        self.assertIn("api", result)


# ── Test render_analysis ─────────────────────────────────────────────────────

class TestRenderAnalysis(unittest.TestCase):
    def setUp(self):
        self.mod = _import_module()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_empty_analysis(self):
        a = self.mod.analyze_stream(self.tmpdir)
        result = self.mod.render_analysis(a)
        self.assertIn("Analyse", result)

    def test_contains_type_section(self):
        entries = [_make_entry(self.mod, entry_type="HYPOTHESIS")]
        _populate_stream(self.mod, self.tmpdir, entries)
        a = self.mod.analyze_stream(self.tmpdir)
        result = self.mod.render_analysis(a)
        self.assertIn("Par Type", result)
        self.assertIn("HYPOTHESIS", result)

    def test_contains_agent_section(self):
        entries = [_make_entry(self.mod, agent="dev")]
        _populate_stream(self.mod, self.tmpdir, entries)
        a = self.mod.analyze_stream(self.tmpdir)
        result = self.mod.render_analysis(a)
        self.assertIn("Par Agent", result)
        self.assertIn("dev", result)

    def test_contains_recommendations(self):
        entries = [
            _make_entry(self.mod, entry_type="HYPOTHESIS", status="open",
                        text=f"hyp{i}")
            for i in range(7)
        ]
        _populate_stream(self.mod, self.tmpdir, entries)
        a = self.mod.analyze_stream(self.tmpdir)
        result = self.mod.render_analysis(a)
        self.assertIn("Recommandations", result)


# ── Test render_stats ────────────────────────────────────────────────────────

class TestRenderStats(unittest.TestCase):
    def setUp(self):
        self.mod = _import_module()

    def test_empty(self):
        result = self.mod.render_stats([])
        self.assertIn("Aucune", result)

    def test_with_entries(self):
        mod = self.mod
        entries = [
            _make_entry(mod, entry_type="HYPOTHESIS"),
            _make_entry(mod, entry_type="DOUBT"),
        ]
        result = mod.render_stats(entries)
        self.assertIn("Total", result)
        self.assertIn("HYPOTHESIS", result)
        self.assertIn("DOUBT", result)


# ── Test constantes ──────────────────────────────────────────────────────────

class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_module()

    def test_valid_types(self):
        expected = {"HYPOTHESIS", "DOUBT", "REASONING", "ASSUMPTION", "ALTERNATIVE"}
        self.assertEqual(self.mod.VALID_TYPES, expected)

    def test_valid_statuses(self):
        expected = {"open", "validated", "invalidated", "abandoned"}
        self.assertEqual(self.mod.VALID_STATUSES, expected)

    def test_type_icons(self):
        for t in self.mod.VALID_TYPES:
            self.assertIn(t, self.mod.TYPE_ICONS)

    def test_status_icons(self):
        for s in self.mod.VALID_STATUSES:
            self.assertIn(s, self.mod.STATUS_ICONS)


if __name__ == "__main__":
    unittest.main()
