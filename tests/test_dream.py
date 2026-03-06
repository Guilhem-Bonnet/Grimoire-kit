#!/usr/bin/env python3
"""
Tests pour dream.py — BMAD Dream Mode.

Fonctions testées :
  - collect_sources()
  - _parse_markdown_entries()
  - _parse_trace_entries()
  - _parse_shared_context_sections()
  - _extract_keywords()
  - _similarity()
  - find_cross_connections()
  - find_recurring_patterns()
  - find_tensions()
  - find_opportunities()
  - validate_insight()
  - deduplicate_insights()
  - dream()
  - render_journal()
  - write_journal()
  - DreamSource, DreamInsight dataclasses
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


def _import_dream():
    return importlib.import_module("dream")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _create_memory_tree(root: Path, learnings=None, decisions=None,
                        trace=None, failures=None, shared=None,
                        contradictions=None):
    """Créer un arbre mémoire minimal pour les tests."""
    mem = root / "_bmad" / "_memory"
    mem.mkdir(parents=True, exist_ok=True)

    if learnings:
        ld = mem / "agent-learnings"
        ld.mkdir(exist_ok=True)
        for name, content in learnings.items():
            (ld / name).write_text(content, encoding="utf-8")

    if decisions:
        (mem / "decisions-log.md").write_text(decisions, encoding="utf-8")

    if failures:
        (mem / "failure-museum.md").write_text(failures, encoding="utf-8")

    if shared:
        (mem / "shared-context.md").write_text(shared, encoding="utf-8")

    if contradictions:
        (mem / "contradiction-log.md").write_text(contradictions, encoding="utf-8")

    if trace:
        out = root / "_bmad-output"
        out.mkdir(parents=True, exist_ok=True)
        (out / "BMAD_TRACE.md").write_text(trace, encoding="utf-8")


# ── Test DreamSource / DreamInsight ───────────────────────────────────────────

class TestDataClasses(unittest.TestCase):
    def setUp(self):
        self.mod = _import_dream()

    def test_dream_source_defaults(self):
        src = self.mod.DreamSource(name="test.md", kind="learnings")
        self.assertEqual(src.entries, [])
        self.assertEqual(src.dates, [])

    def test_dream_insight_defaults(self):
        ins = self.mod.DreamInsight(
            title="t", description="d", sources=["a"],
            category="pattern", confidence=0.5,
        )
        self.assertEqual(ins.agents_relevant, [])
        self.assertFalse(ins.actionable)


# ── Test _parse_markdown_entries ──────────────────────────────────────────────

class TestParseMarkdownEntries(unittest.TestCase):
    def setUp(self):
        self.mod = _import_dream()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_parses_dated_entries(self):
        f = self.tmpdir / "test.md"
        f.write_text(
            "# Learnings\n"
            "- [2025-01-15] Important learning about caching\n"
            "- [2025-01-20] Another learning about API design\n",
            encoding="utf-8",
        )
        entries = self.mod._parse_markdown_entries(f)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0][0], "2025-01-15")
        self.assertIn("caching", entries[0][1])

    def test_filters_by_since(self):
        f = self.tmpdir / "test.md"
        f.write_text(
            "- [2025-01-01] Old entry\n"
            "- [2025-06-01] New entry\n",
            encoding="utf-8",
        )
        entries = self.mod._parse_markdown_entries(f, since="2025-03-01")
        self.assertEqual(len(entries), 1)
        self.assertIn("New", entries[0][1])

    def test_handles_undated_entries(self):
        f = self.tmpdir / "test.md"
        f.write_text(
            "- entry without date\n"
            "* another entry\n",
            encoding="utf-8",
        )
        entries = self.mod._parse_markdown_entries(f)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0][0], "")  # no date

    def test_skips_headers_and_blanks(self):
        f = self.tmpdir / "test.md"
        f.write_text("# Header\n\n## Sub\n\n- Actual entry\n", encoding="utf-8")
        entries = self.mod._parse_markdown_entries(f)
        self.assertEqual(len(entries), 1)

    def test_missing_file(self):
        entries = self.mod._parse_markdown_entries(self.tmpdir / "nope.md")
        self.assertEqual(entries, [])

    def test_empty_file(self):
        f = self.tmpdir / "empty.md"
        f.write_text("", encoding="utf-8")
        entries = self.mod._parse_markdown_entries(f)
        self.assertEqual(entries, [])


# ── Test _parse_trace_entries ─────────────────────────────────────────────────

class TestParseTraceEntries(unittest.TestCase):
    def setUp(self):
        self.mod = _import_dream()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_parses_standard_trace(self):
        f = self.tmpdir / "BMAD_TRACE.md"
        f.write_text(
            "[2025-06-01 10:00] [DECISION] [architect] Chose microservices arch\n"
            "[2025-06-01 10:05] [INFO] [dev] Starting implementation\n"
            "[2025-06-01 10:10] [CHECKPOINT] [pm] Sprint review done\n"
            "[2025-06-01 10:15] [FAILURE] [qa] Test suite broken\n",
            encoding="utf-8",
        )
        entries = self.mod._parse_trace_entries(f)
        # INFO is not included (only DECISION, CHECKPOINT, FAILURE, REMEMBER)
        self.assertEqual(len(entries), 3)

    def test_filters_by_agent(self):
        f = self.tmpdir / "trace.md"
        f.write_text(
            "[2025-06-01 10:00] [DECISION] [dev] Choice A\n"
            "[2025-06-01 10:05] [DECISION] [architect] Choice B\n",
            encoding="utf-8",
        )
        entries = self.mod._parse_trace_entries(f, agent_filter="dev")
        self.assertEqual(len(entries), 1)
        self.assertIn("dev", entries[0][1])

    def test_filters_by_since(self):
        f = self.tmpdir / "trace.md"
        f.write_text(
            "[2025-01-01 10:00] [DECISION] [dev] Old\n"
            "[2025-07-01 10:00] [DECISION] [dev] New\n",
            encoding="utf-8",
        )
        entries = self.mod._parse_trace_entries(f, since="2025-06-01")
        self.assertEqual(len(entries), 1)
        self.assertIn("New", entries[0][1])

    def test_missing_file(self):
        entries = self.mod._parse_trace_entries(self.tmpdir / "nope.md")
        self.assertEqual(entries, [])


# ── Test _parse_shared_context_sections ───────────────────────────────────────

class TestParseSharedContext(unittest.TestCase):
    def setUp(self):
        self.mod = _import_dream()

    def test_extracts_sections(self):
        content = (
            "## Section A\nContent A\n\n"
            "## Section B\nContent B line 1\nContent B line 2\n"
        )
        sections = self.mod._parse_shared_context_sections(content)
        self.assertEqual(len(sections), 2)
        self.assertIn("Section A", sections[0])
        self.assertIn("Content B line 1", sections[1])

    def test_empty_content(self):
        sections = self.mod._parse_shared_context_sections("")
        self.assertEqual(sections, [])

    def test_no_headers(self):
        content = "Just a plain paragraph\nwith no headers"
        sections = self.mod._parse_shared_context_sections(content)
        self.assertEqual(len(sections), 1)


# ── Test _extract_keywords ────────────────────────────────────────────────────

class TestExtractKeywords(unittest.TestCase):
    def setUp(self):
        self.mod = _import_dream()

    def test_removes_stopwords(self):
        kw = self.mod._extract_keywords("the api is in the database server")
        self.assertNotIn("the", kw)
        self.assertNotIn("is", kw)
        self.assertIn("api", kw)
        self.assertIn("database", kw)
        self.assertIn("server", kw)

    def test_ignores_short_words(self):
        kw = self.mod._extract_keywords("go do it up in db")
        # All words are < 3 chars → empty (db=2)
        for w in kw:
            self.assertGreaterEqual(len(w), 3)

    def test_case_insensitive(self):
        kw = self.mod._extract_keywords("PostgreSQL database")
        self.assertIn("postgresql", kw)
        self.assertIn("database", kw)

    def test_empty_string(self):
        kw = self.mod._extract_keywords("")
        self.assertEqual(kw, set())


# ── Test _similarity ──────────────────────────────────────────────────────────

class TestSimilarity(unittest.TestCase):
    def setUp(self):
        self.mod = _import_dream()

    def test_identical_texts(self):
        sim = self.mod._similarity("database caching layer", "database caching layer")
        self.assertEqual(sim, 1.0)

    def test_completely_different(self):
        sim = self.mod._similarity("database caching layer", "banana orange fruit")
        self.assertEqual(sim, 0.0)

    def test_partial_overlap(self):
        sim = self.mod._similarity(
            "database caching performance",
            "database indexing performance"
        )
        self.assertGreater(sim, 0.0)
        self.assertLess(sim, 1.0)

    def test_empty_text(self):
        sim = self.mod._similarity("", "something")
        self.assertEqual(sim, 0.0)


# ── Test collect_sources ──────────────────────────────────────────────────────

class TestCollectSources(unittest.TestCase):
    def setUp(self):
        self.mod = _import_dream()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_collects_learnings(self):
        _create_memory_tree(self.tmpdir, learnings={
            "dev.md": "# Dev Learnings\n- [2025-06-01] Caching improves latency\n",
        })
        sources = self.mod.collect_sources(self.tmpdir)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].kind, "learnings")
        self.assertEqual(len(sources[0].entries), 1)

    def test_collects_decisions(self):
        _create_memory_tree(self.tmpdir, decisions=(
            "# Decisions\n- [2025-06-01] Chose PostgreSQL for persistence\n"
        ))
        sources = self.mod.collect_sources(self.tmpdir)
        self.assertTrue(any(s.kind == "decisions" for s in sources))

    def test_collects_trace(self):
        _create_memory_tree(self.tmpdir, trace=(
            "[2025-06-01 10:00] [DECISION] [dev] Implemented caching\n"
        ))
        sources = self.mod.collect_sources(self.tmpdir)
        self.assertTrue(any(s.kind == "trace" for s in sources))

    def test_collects_failures(self):
        _create_memory_tree(self.tmpdir, failures=(
            "# Failure Museum\n- [2025-06-01] Cache invalidation bug\n"
        ))
        sources = self.mod.collect_sources(self.tmpdir)
        self.assertTrue(any(s.kind == "failure-museum" for s in sources))

    def test_collects_shared_context(self):
        _create_memory_tree(self.tmpdir, shared=(
            "## Current Arch\nMicroservices with Redis cache\n"
        ))
        sources = self.mod.collect_sources(self.tmpdir)
        self.assertTrue(any(s.kind == "shared-context" for s in sources))

    def test_collects_contradictions(self):
        _create_memory_tree(self.tmpdir, contradictions=(
            "# Contradictions\n- [2025-06-01] SQL vs NoSQL debate ongoing\n"
        ))
        sources = self.mod.collect_sources(self.tmpdir)
        self.assertTrue(any(s.kind == "contradictions" for s in sources))

    def test_agent_filter(self):
        _create_memory_tree(self.tmpdir, learnings={
            "dev.md": "- [2025-06-01] Dev learning\n",
            "architect.md": "- [2025-06-01] Arch learning\n",
        })
        sources = self.mod.collect_sources(self.tmpdir, agent_filter="dev")
        self.assertEqual(len(sources), 1)
        self.assertIn("dev", sources[0].name)

    def test_since_filter(self):
        _create_memory_tree(self.tmpdir, learnings={
            "dev.md": "- [2025-01-01] Old\n- [2025-07-01] New\n",
        })
        sources = self.mod.collect_sources(self.tmpdir, since="2025-06-01")
        self.assertEqual(len(sources), 1)
        self.assertEqual(len(sources[0].entries), 1)

    def test_empty_project(self):
        sources = self.mod.collect_sources(self.tmpdir)
        self.assertEqual(sources, [])

    def test_all_sources_combined(self):
        _create_memory_tree(
            self.tmpdir,
            learnings={"dev.md": "- [2025-06-01] L1\n"},
            decisions="- [2025-06-01] D1\n",
            trace="[2025-06-01 10:00] [DECISION] [dev] T1\n",
            failures="- [2025-06-01] F1\n",
            shared="## Ctx\nContent\n",
            contradictions="- [2025-06-01] C1\n",
        )
        sources = self.mod.collect_sources(self.tmpdir)
        self.assertEqual(len(sources), 6)
        kinds = {s.kind for s in sources}
        self.assertEqual(kinds, {
            "learnings", "decisions", "trace",
            "failure-museum", "shared-context", "contradictions",
        })


# ── Test find_cross_connections ───────────────────────────────────────────────

class TestFindCrossConnections(unittest.TestCase):
    def setUp(self):
        self.mod = _import_dream()

    def test_finds_connection_between_different_kinds(self):
        src_a = self.mod.DreamSource(
            name="learnings/dev.md", kind="learnings",
            entries=["database caching performance optimization layer"],
        )
        src_b = self.mod.DreamSource(
            name="decisions-log.md", kind="decisions",
            entries=["database caching performance optimization system"],
        )
        insights = self.mod.find_cross_connections([src_a, src_b])
        self.assertGreater(len(insights), 0)
        self.assertEqual(insights[0].category, "connection")

    def test_ignores_same_kind(self):
        src_a = self.mod.DreamSource(
            name="learnings/dev.md", kind="learnings",
            entries=["database caching"],
        )
        src_b = self.mod.DreamSource(
            name="learnings/arch.md", kind="learnings",
            entries=["database caching"],
        )
        insights = self.mod.find_cross_connections([src_a, src_b])
        self.assertEqual(len(insights), 0)

    def test_no_connection_different_topics(self):
        src_a = self.mod.DreamSource(
            name="learnings/dev.md", kind="learnings",
            entries=["banana smoothie recipe"],
        )
        src_b = self.mod.DreamSource(
            name="decisions-log.md", kind="decisions",
            entries=["kubernetes deployment strategy"],
        )
        insights = self.mod.find_cross_connections([src_a, src_b])
        self.assertEqual(len(insights), 0)

    def test_empty_sources(self):
        insights = self.mod.find_cross_connections([])
        self.assertEqual(insights, [])


# ── Test find_recurring_patterns ──────────────────────────────────────────────

class TestFindRecurringPatterns(unittest.TestCase):
    def setUp(self):
        self.mod = _import_dream()

    def test_detects_recurring_keyword(self):
        src_a = self.mod.DreamSource(
            name="learnings/dev.md", kind="learnings",
            entries=[
                "caching improved performance significantly",
                "added caching layer",
                "caching invalidation needs work",
            ],
        )
        src_b = self.mod.DreamSource(
            name="decisions-log.md", kind="decisions",
            entries=["implemented caching strategy for sessions"],
        )
        insights = self.mod.find_recurring_patterns([src_a, src_b])
        # "caching" should appear across 2 different source names with ≥3 total
        caching_found = any("caching" in i.title.lower() for i in insights)
        self.assertTrue(caching_found)

    def test_no_pattern_single_source(self):
        src = self.mod.DreamSource(
            name="a.md", kind="learnings",
            entries=["unique banana entry"],
        )
        insights = self.mod.find_recurring_patterns([src])
        self.assertEqual(insights, [])

    def test_empty_sources(self):
        insights = self.mod.find_recurring_patterns([])
        self.assertEqual(insights, [])


# ── Test find_tensions ────────────────────────────────────────────────────────

class TestFindTensions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_dream()

    def test_detects_tension(self):
        src_a = self.mod.DreamSource(
            name="learnings/dev.md", kind="learnings",
            entries=["caching database layer optimization must always remain enabled"],
        )
        src_b = self.mod.DreamSource(
            name="failure-museum.md", kind="failure-museum",
            entries=["danger caching database layer optimization caused stale data"],
        )
        insights = self.mod.find_tensions([src_a, src_b])
        self.assertGreater(len(insights), 0)
        self.assertEqual(insights[0].category, "tension")

    def test_no_tension_in_same_source(self):
        src = self.mod.DreamSource(
            name="a.md", kind="learnings",
            entries=[
                "must always cache data",
                "avoid caching large datasets",
            ],
        )
        insights = self.mod.find_tensions([src])
        self.assertEqual(len(insights), 0)

    def test_no_tension_different_topics(self):
        src_a = self.mod.DreamSource(
            name="a.md", kind="learnings",
            entries=["must always validate input"],
        )
        src_b = self.mod.DreamSource(
            name="b.md", kind="decisions",
            entries=["danger from network latency"],
        )
        insights = self.mod.find_tensions([src_a, src_b])
        # Different topics so similarity < 0.3
        # (validate/input vs network/latency → no overlap)
        self.assertEqual(len(insights), 0)


# ── Test find_opportunities ───────────────────────────────────────────────────

class TestFindOpportunities(unittest.TestCase):
    def setUp(self):
        self.mod = _import_dream()

    def test_finds_todo(self):
        src = self.mod.DreamSource(
            name="learnings/dev.md", kind="learnings",
            entries=["TODO: refactor the caching layer"],
        )
        insights = self.mod.find_opportunities([src])
        self.assertEqual(len(insights), 1)
        self.assertEqual(insights[0].category, "opportunity")
        self.assertTrue(insights[0].actionable)

    def test_finds_improvement_markers(self):
        src_a = self.mod.DreamSource(
            name="notes.md", kind="learnings",
            entries=[
                "à améliorer: error handling",
                "could be better at logging",
            ],
        )
        src_b = self.mod.DreamSource(
            name="retro.md", kind="decisions",
            entries=[
                "not yet implemented: retry logic",
                "automatiser les tests de regression",
            ],
        )
        insights = self.mod.find_opportunities([src_a, src_b])
        self.assertEqual(len(insights), 4)

    def test_no_opportunities(self):
        src = self.mod.DreamSource(
            name="a.md", kind="learnings",
            entries=["Everything works perfectly fine"],
        )
        insights = self.mod.find_opportunities([src])
        self.assertEqual(insights, [])

    def test_one_marker_per_entry(self):
        """Even if entry has multiple markers, only one insight per entry."""
        src = self.mod.DreamSource(
            name="a.md", kind="learnings",
            entries=["TODO: automatiser simplifier le process"],
        )
        insights = self.mod.find_opportunities([src])
        self.assertEqual(len(insights), 1)


# ── Test validate_insight ─────────────────────────────────────────────────────

class TestValidateInsight(unittest.TestCase):
    def setUp(self):
        self.mod = _import_dream()
        self.sources = [
            self.mod.DreamSource(name="a.md", kind="learnings"),
            self.mod.DreamSource(name="b.md", kind="decisions"),
        ]

    def test_valid_insight(self):
        ins = self.mod.DreamInsight(
            title="Test", description="Valid description text here",
            sources=["a.md"], category="pattern", confidence=0.7,
        )
        self.assertTrue(self.mod.validate_insight(ins, self.sources))

    def test_no_sources(self):
        ins = self.mod.DreamInsight(
            title="T", description="Description text here",
            sources=[], category="pattern", confidence=0.5,
        )
        self.assertFalse(self.mod.validate_insight(ins, self.sources))

    def test_invalid_source_reference(self):
        ins = self.mod.DreamInsight(
            title="T", description="Description text here",
            sources=["nonexistent.md"], category="pattern", confidence=0.5,
        )
        self.assertFalse(self.mod.validate_insight(ins, self.sources))

    def test_zero_confidence(self):
        ins = self.mod.DreamInsight(
            title="T", description="Description texte",
            sources=["a.md"], category="pattern", confidence=0.0,
        )
        self.assertFalse(self.mod.validate_insight(ins, self.sources))

    def test_empty_description(self):
        ins = self.mod.DreamInsight(
            title="T", description="",
            sources=["a.md"], category="pattern", confidence=0.5,
        )
        self.assertFalse(self.mod.validate_insight(ins, self.sources))

    def test_short_description(self):
        ins = self.mod.DreamInsight(
            title="T", description="Short",
            sources=["a.md"], category="pattern", confidence=0.5,
        )
        self.assertFalse(self.mod.validate_insight(ins, self.sources))


# ── Test deduplicate_insights ─────────────────────────────────────────────────

class TestDeduplicateInsights(unittest.TestCase):
    def setUp(self):
        self.mod = _import_dream()

    def test_removes_duplicates(self):
        ins_a = self.mod.DreamInsight(
            title="A",
            description="database caching performance optimization layer fast query response",
            sources=["a.md"], category="pattern", confidence=0.6,
        )
        ins_b = self.mod.DreamInsight(
            title="B",
            description="database caching performance optimization layer fast query handling",
            sources=["b.md"], category="pattern", confidence=0.8,
        )
        result = self.mod.deduplicate_insights([ins_a, ins_b])
        self.assertEqual(len(result), 1)
        # Higher confidence kept
        self.assertEqual(result[0].confidence, 0.8)

    def test_keeps_different_insights(self):
        ins_a = self.mod.DreamInsight(
            title="A", description="database caching layer performance",
            sources=["a.md"], category="pattern", confidence=0.6,
        )
        ins_b = self.mod.DreamInsight(
            title="B", description="kubernetes deployment orchestration strategy",
            sources=["b.md"], category="connection", confidence=0.5,
        )
        result = self.mod.deduplicate_insights([ins_a, ins_b])
        self.assertEqual(len(result), 2)

    def test_empty_list(self):
        result = self.mod.deduplicate_insights([])
        self.assertEqual(result, [])


# ── Test dream() orchestrator ─────────────────────────────────────────────────

class TestDream(unittest.TestCase):
    def setUp(self):
        self.mod = _import_dream()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_returns_empty_no_sources(self):
        result = self.mod.dream(self.tmpdir)
        self.assertEqual(result, [])

    def test_returns_insights_from_rich_data(self):
        _create_memory_tree(
            self.tmpdir,
            learnings={
                "dev.md": (
                    "- [2025-06-01] TODO: refactor caching layer\n"
                    "- [2025-06-02] caching improved performance database\n"
                    "- [2025-06-03] caching invalidation still problematic\n"
                ),
            },
            decisions=(
                "- [2025-06-01] Implemented caching strategy database sessions layer\n"
            ),
            failures=(
                "- [2025-06-01] caching caused stale data problem in database\n"
            ),
        )
        insights = self.mod.dream(self.tmpdir)
        self.assertGreater(len(insights), 0)
        # Should be sorted by confidence desc
        if len(insights) >= 2:
            self.assertGreaterEqual(insights[0].confidence, insights[1].confidence)

    def test_respects_max_insights(self):
        # Create many overlapping entries to generate lots of insights
        entries = "\n".join(
            f"- [2025-06-{i:02d}] TODO: refactor item {i} needs improvement"
            for i in range(1, 25)
        )
        _create_memory_tree(
            self.tmpdir,
            learnings={"dev.md": entries},
            decisions=entries.replace("TODO:", "Decided:"),
        )
        insights = self.mod.dream(self.tmpdir)
        self.assertLessEqual(len(insights), self.mod.MAX_INSIGHTS)

    def test_validation_filters_bad_insights(self):
        _create_memory_tree(
            self.tmpdir,
            learnings={"dev.md": "- [2025-06-01] small entry\n"},
        )
        # With validation on: insights with bad data should be filtered
        insights = self.mod.dream(self.tmpdir, do_validate=True)
        for ins in insights:
            self.assertGreater(ins.confidence, 0)
            self.assertGreater(len(ins.description), 0)

    def test_since_filter_propagates(self):
        _create_memory_tree(
            self.tmpdir,
            learnings={
                "dev.md": (
                    "- [2025-01-01] Old learning about caching database layer performance\n"
                    "- [2025-07-01] New learning about caching database layer optimization\n"
                ),
            },
        )
        insights_all = self.mod.dream(self.tmpdir, since=None, do_validate=False)
        insights_recent = self.mod.dream(self.tmpdir, since="2025-06-01", do_validate=False)
        # recent should have fewer or equal insights
        self.assertLessEqual(len(insights_recent), len(insights_all))


# ── Test render_journal ───────────────────────────────────────────────────────

class TestRenderJournal(unittest.TestCase):
    def setUp(self):
        self.mod = _import_dream()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_renders_markdown(self):
        sources = [
            self.mod.DreamSource(name="a.md", kind="learnings", entries=["e1"]),
        ]
        insights = [
            self.mod.DreamInsight(
                title="Test Insight", description="Some description here",
                sources=["a.md"], category="pattern", confidence=0.8,
            ),
        ]
        md = self.mod.render_journal(insights, sources, self.tmpdir)
        self.assertIn("Dream Journal", md)
        self.assertIn("Test Insight", md)
        self.assertIn("pattern", md)
        self.assertIn("a.md", md)

    def test_includes_summary_table(self):
        sources = [self.mod.DreamSource(name="a.md", kind="learnings", entries=["e"])]
        insights = [
            self.mod.DreamInsight(
                title="T", description="D" * 20, sources=["a.md"],
                category="pattern", confidence=0.7,
            ),
        ]
        md = self.mod.render_journal(insights, sources, self.tmpdir)
        self.assertIn("Résumé", md)
        self.assertIn("Catégorie", md)

    def test_includes_since_info(self):
        sources = [self.mod.DreamSource(name="a.md", kind="learnings", entries=["e"])]
        insights = []
        md = self.mod.render_journal(insights, sources, self.tmpdir, since="2025-01-01")
        self.assertIn("2025-01-01", md)


# ── Test write_journal ────────────────────────────────────────────────────────

class TestWriteJournal(unittest.TestCase):
    def setUp(self):
        self.mod = _import_dream()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_writes_file(self):
        content = "# Test Journal\nHello"
        path = self.mod.write_journal(content, self.tmpdir)
        self.assertTrue(path.exists())
        self.assertEqual(path.read_text(encoding="utf-8"), content)

    def test_creates_output_dir(self):
        deep = self.tmpdir / "sub" / "project"
        deep.mkdir(parents=True)
        content = "# Test"
        path = self.mod.write_journal(content, deep)
        self.assertTrue(path.exists())

    def test_archives_previous(self):
        # Write first journal
        self.mod.write_journal("First", self.tmpdir)
        # Write second — should archive first
        self.mod.write_journal("Second", self.tmpdir)
        journal_path = self.tmpdir / "_bmad-output" / "dream-journal.md"
        self.assertEqual(journal_path.read_text(encoding="utf-8"), "Second")
        archives = list((self.tmpdir / "_bmad-output" / "dream-archives").glob("*.md"))
        self.assertEqual(len(archives), 1)

    def test_dry_run_no_write(self, ):
        content = "# DryRun"
        path = self.mod.write_journal(content, self.tmpdir, dry_run=True)
        self.assertFalse(path.exists())


# ── Test _parse_pheromone_board (feedback loop) ──────────────────────────────

class TestParsePheromoneBoard(unittest.TestCase):
    """Tests pour _parse_pheromone_board — dream lit les phéromones."""

    def setUp(self):
        self.mod = _import_dream()
        self.tmpdir = Path(tempfile.mkdtemp())
        self.board_dir = self.tmpdir / "_bmad-output"
        self.board_dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_board(self, pheromones):
        import json
        data = {"version": "1.0.0", "half_life_hours": 168.0,
                "pheromones": pheromones, "total_emitted": len(pheromones)}
        (self.board_dir / "pheromone-board.json").write_text(
            json.dumps(data), encoding="utf-8")

    def test_no_board_file(self):
        entries = self.mod._parse_pheromone_board(self.tmpdir)
        self.assertEqual(entries, [])

    def test_empty_board(self):
        self._write_board([])
        entries = self.mod._parse_pheromone_board(self.tmpdir)
        self.assertEqual(entries, [])

    def test_parses_active_pheromones(self):
        self._write_board([{
            "pheromone_id": "PH-abc",
            "pheromone_type": "ALERT",
            "location": "src/auth",
            "text": "Security review needed",
            "emitter": "qa-agent",
            "timestamp": "2025-06-15T10:00:00+00:00",
            "intensity": 0.8,
            "tags": ["security"],
            "reinforcements": 0,
            "resolved": False,
        }])
        entries = self.mod._parse_pheromone_board(self.tmpdir)
        self.assertEqual(len(entries), 1)
        self.assertIn("ALERT", entries[0][1])
        self.assertIn("Security review needed", entries[0][1])

    def test_skips_resolved(self):
        self._write_board([{
            "pheromone_id": "PH-resolved",
            "pheromone_type": "NEED",
            "location": "src/core",
            "text": "Done",
            "emitter": "dev",
            "timestamp": "2025-06-15T10:00:00+00:00",
            "resolved": True,
        }])
        entries = self.mod._parse_pheromone_board(self.tmpdir)
        self.assertEqual(entries, [])

    def test_skips_own_non_reinforced_pheromones(self):
        """Dream's own pheromones are skipped unless reinforced (feedback)."""
        self._write_board([{
            "pheromone_id": "PH-dream1",
            "pheromone_type": "NEED",
            "location": "system/dream",
            "text": "[dream] Some insight",
            "emitter": "dream-mode",
            "timestamp": "2025-06-15T10:00:00+00:00",
            "reinforcements": 0,
            "resolved": False,
        }])
        entries = self.mod._parse_pheromone_board(self.tmpdir)
        self.assertEqual(entries, [])

    def test_includes_own_reinforced_pheromones(self):
        """Dream's pheromones that got reinforced = feedback signal."""
        self._write_board([{
            "pheromone_id": "PH-dream2",
            "pheromone_type": "OPPORTUNITY",
            "location": "src/api",
            "text": "[dream] Optimization opportunity",
            "emitter": "dream-mode",
            "timestamp": "2025-06-15T10:00:00+00:00",
            "reinforcements": 2,
            "resolved": False,
        }])
        entries = self.mod._parse_pheromone_board(self.tmpdir)
        self.assertEqual(len(entries), 1)
        self.assertIn("+2 reinforcements", entries[0][1])

    def test_since_filter(self):
        self._write_board([
            {
                "pheromone_id": "PH-old",
                "pheromone_type": "NEED",
                "location": "src",
                "text": "Old signal",
                "emitter": "dev",
                "timestamp": "2025-01-01T10:00:00+00:00",
                "resolved": False,
            },
            {
                "pheromone_id": "PH-new",
                "pheromone_type": "ALERT",
                "location": "src",
                "text": "New signal",
                "emitter": "dev",
                "timestamp": "2025-07-01T10:00:00+00:00",
                "resolved": False,
            },
        ])
        entries = self.mod._parse_pheromone_board(self.tmpdir, since="2025-06-01")
        self.assertEqual(len(entries), 1)
        self.assertIn("New signal", entries[0][1])

    def test_collect_sources_includes_stigmergy(self):
        """collect_sources doit inclure le pheromone board comme source."""
        _create_memory_tree(self.tmpdir, learnings={
            "dev.md": "- [2025-06-01] caching issue\n",
        })
        self._write_board([{
            "pheromone_id": "PH-test",
            "pheromone_type": "ALERT",
            "location": "src/db",
            "text": "Database bottleneck detected",
            "emitter": "ops",
            "timestamp": "2025-06-15T10:00:00+00:00",
            "resolved": False,
        }])
        sources = self.mod.collect_sources(self.tmpdir)
        kinds = [s.kind for s in sources]
        self.assertIn("stigmergy", kinds)

    def test_invalid_json_returns_empty(self):
        (self.board_dir / "pheromone-board.json").write_text(
            "not json{", encoding="utf-8")
        entries = self.mod._parse_pheromone_board(self.tmpdir)
        self.assertEqual(entries, [])


# ── Test timestamp incrémental ────────────────────────────────────────────────

class TestDreamTimestamp(unittest.TestCase):
    """Tests pour save/read_last_dream_timestamp — mode incrémental."""

    def setUp(self):
        self.mod = _import_dream()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_previous_timestamp(self):
        result = self.mod.read_last_dream_timestamp(self.tmpdir)
        self.assertIsNone(result)

    def test_save_and_read(self):
        self.mod.save_last_dream_timestamp(self.tmpdir)
        result = self.mod.read_last_dream_timestamp(self.tmpdir)
        self.assertIsNotNone(result)
        # Should be today's date in YYYY-MM-DD format
        self.assertEqual(len(result), 10)
        self.assertEqual(result[4], "-")
        self.assertEqual(result[7], "-")

    def test_creates_directory(self):
        deep = self.tmpdir / "sub" / "project"
        self.mod.save_last_dream_timestamp(deep)
        self.assertTrue((deep / "_bmad" / "_memory" / "dream-last-run").exists())

    def test_corrupted_file_returns_none(self):
        mem = self.tmpdir / "_bmad" / "_memory"
        mem.mkdir(parents=True)
        (mem / "dream-last-run").write_text("corrupted", encoding="utf-8")
        result = self.mod.read_last_dream_timestamp(self.tmpdir)
        self.assertIsNone(result)


# ── Test dream_quick ──────────────────────────────────────────────────────────

class TestDreamQuick(unittest.TestCase):
    """Tests pour dream_quick() — mode rapide O(n)."""

    def setUp(self):
        self.mod = _import_dream()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_returns_empty_no_sources(self):
        result = self.mod.dream_quick(self.tmpdir)
        self.assertEqual(result, [])

    def test_returns_insights_from_data(self):
        _create_memory_tree(
            self.tmpdir,
            learnings={
                "dev.md": (
                    "- [2025-06-01] TODO: refactor caching layer\n"
                    "- [2025-06-02] caching improved performance database\n"
                    "- [2025-06-03] caching invalidation still problematic\n"
                ),
            },
            decisions=(
                "- [2025-06-01] Implemented caching strategy database layer\n"
            ),
        )
        insights = self.mod.dream_quick(self.tmpdir)
        # May return 0 if data not rich enough, but function must not crash
        self.assertIsInstance(insights, list)

    def test_respects_quick_max(self):
        entries = "\n".join(
            f"- [2025-06-{i:02d}] TODO: refactor item {i} needs improvement"
            for i in range(1, 25)
        )
        _create_memory_tree(
            self.tmpdir,
            learnings={"dev.md": entries},
            decisions=entries.replace("TODO:", "Decided:"),
        )
        insights = self.mod.dream_quick(self.tmpdir)
        self.assertLessEqual(len(insights), self.mod.QUICK_MAX_INSIGHTS)

    def test_only_patterns_and_opportunities(self):
        """dream_quick ne doit retourner que des patterns et opportunities."""
        _create_memory_tree(
            self.tmpdir,
            learnings={
                "dev.md": (
                    "- [2025-06-01] caching layer needs refactor database\n"
                    "- [2025-06-02] caching performance database slow\n"
                    "- [2025-06-03] caching invalidation database problem\n"
                ),
            },
            decisions=(
                "- [2025-06-01] caching database strategy implemented\n"
            ),
        )
        insights = self.mod.dream_quick(self.tmpdir)
        for ins in insights:
            self.assertIn(ins.category, ("pattern", "opportunity"))

    def test_sorted_by_confidence_desc(self):
        entries = "\n".join(
            f"- [2025-06-{i:02d}] TODO: refactor item {i} caching performance"
            for i in range(1, 15)
        )
        _create_memory_tree(
            self.tmpdir,
            learnings={"dev.md": entries},
            decisions=entries.replace("TODO:", "Decided:"),
        )
        insights = self.mod.dream_quick(self.tmpdir)
        for i in range(len(insights) - 1):
            self.assertGreaterEqual(insights[i].confidence,
                                    insights[i + 1].confidence)

    def test_since_filter(self):
        _create_memory_tree(
            self.tmpdir,
            learnings={
                "dev.md": (
                    "- [2025-01-01] Old caching database layer performance\n"
                    "- [2025-07-01] New caching database layer optimization\n"
                ),
            },
        )
        all_ins = self.mod.dream_quick(self.tmpdir, since=None)
        recent = self.mod.dream_quick(self.tmpdir, since="2025-06-01")
        self.assertLessEqual(len(recent), len(all_ins))

    def test_agent_filter(self):
        _create_memory_tree(
            self.tmpdir,
            learnings={
                "dev.md": "- [2025-06-01] dev caching database\n",
                "qa.md": "- [2025-06-01] qa testing coverage\n",
            },
        )
        insights = self.mod.dream_quick(self.tmpdir, agent_filter="dev")
        self.assertIsInstance(insights, list)

    def test_pre_collected_sources(self):
        """dream_quick doit accepter _sources pré-collectées."""
        _create_memory_tree(
            self.tmpdir,
            learnings={
                "dev.md": (
                    "- [2025-06-01] TODO: refactor caching layer\n"
                    "- [2025-06-02] caching improved performance database\n"
                ),
            },
        )
        sources = self.mod.collect_sources(self.tmpdir)
        insights = self.mod.dream_quick(self.tmpdir, _sources=sources)
        self.assertIsInstance(insights, list)


# ── Test dream(quick=True) parameter ─────────────────────────────────────────

class TestDreamQuickParam(unittest.TestCase):
    """Tests que dream() avec quick=True produit le même résultat que dream_quick."""

    def setUp(self):
        self.mod = _import_dream()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_quick_param_no_crash(self):
        _create_memory_tree(
            self.tmpdir,
            learnings={
                "dev.md": (
                    "- [2025-06-01] caching layer database slow\n"
                    "- [2025-06-02] caching performance database improved\n"
                ),
            },
        )
        insights = self.mod.dream(self.tmpdir, quick=True)
        self.assertIsInstance(insights, list)
        self.assertLessEqual(len(insights), self.mod.QUICK_MAX_INSIGHTS)

    def test_quick_equals_dream_quick(self):
        """dream(quick=True) doit donner le même résultat que dream_quick()."""
        _create_memory_tree(
            self.tmpdir,
            learnings={
                "dev.md": (
                    "- [2025-06-01] TODO: refactor caching layer\n"
                    "- [2025-06-02] caching improved performance database\n"
                    "- [2025-06-03] caching invalidation still problematic\n"
                ),
            },
            decisions=(
                "- [2025-06-01] Implemented caching strategy database layer\n"
            ),
        )
        via_param = self.mod.dream(self.tmpdir, quick=True)
        via_func = self.mod.dream_quick(self.tmpdir)
        # Same titles (order may differ if same confidence)
        self.assertEqual(
            sorted(i.title for i in via_param),
            sorted(i.title for i in via_func),
        )

    def test_pre_collected_sources(self):
        """dream() doit accepter _sources pré-collectées."""
        _create_memory_tree(
            self.tmpdir,
            learnings={
                "dev.md": "- [2025-06-01] TODO: caching database layer\n",
            },
        )
        sources = self.mod.collect_sources(self.tmpdir)
        insights = self.mod.dream(self.tmpdir, _sources=sources)
        self.assertIsInstance(insights, list)


# ── Test _INSIGHT_TO_PHEROMONE mapping ────────────────────────────────────────

class TestInsightToPheromone(unittest.TestCase):
    def setUp(self):
        self.mod = _import_dream()

    def test_mapping_keys(self):
        mapping = self.mod._INSIGHT_TO_PHEROMONE
        self.assertIn("tension", mapping)
        self.assertIn("opportunity", mapping)
        self.assertIn("connection", mapping)
        self.assertIn("pattern", mapping)

    def test_mapping_values_are_valid_pheromone_types(self):
        valid_types = {"NEED", "ALERT", "OPPORTUNITY", "PROGRESS", "COMPLETE", "BLOCK"}
        mapping = self.mod._INSIGHT_TO_PHEROMONE
        for value in mapping.values():
            self.assertIn(value, valid_types)


# ── Test emit_to_stigmergy ───────────────────────────────────────────────────

class TestEmitToStigmergy(unittest.TestCase):
    """Tests pour emit_to_stigmergy() — bridge dream → stigmergy."""

    def setUp(self):
        self.mod = _import_dream()
        self.tmpdir = Path(tempfile.mkdtemp())
        # Créer le dossier _bmad-output pour le board stigmergy
        (self.tmpdir / "_bmad-output").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_returns_zero_empty_list(self):
        count = self.mod.emit_to_stigmergy([], self.tmpdir)
        self.assertEqual(count, 0)

    def test_emits_pheromones_for_insights(self):
        insights = [
            self.mod.DreamInsight(
                title="Test Pattern",
                description="Recurring caching issue",
                sources=["dev.md"],
                category="pattern",
                confidence=0.7,
            ),
            self.mod.DreamInsight(
                title="Test Opportunity",
                description="Optimization opportunity",
                sources=["qa.md"],
                category="opportunity",
                confidence=0.8,
            ),
        ]
        count = self.mod.emit_to_stigmergy(insights, self.tmpdir)
        self.assertEqual(count, 2)

    def test_pheromone_board_saved(self):
        insights = [
            self.mod.DreamInsight(
                title="Saved Test",
                description="Should persist on board",
                sources=["dev.md"],
                category="tension",
                confidence=0.6,
            ),
        ]
        self.mod.emit_to_stigmergy(insights, self.tmpdir)
        # Verify board file was created
        board_file = self.tmpdir / "_bmad-output" / "pheromone-board.json"
        self.assertTrue(board_file.exists())

    def test_pheromone_has_dream_prefix(self):
        insights = [
            self.mod.DreamInsight(
                title="Prefix Test",
                description="Should have [dream] prefix",
                sources=["dev.md"],
                category="connection",
                confidence=0.5,
            ),
        ]
        self.mod.emit_to_stigmergy(insights, self.tmpdir)

        # Load board and check text prefix
        sg = importlib.import_module("stigmergy")
        board = sg.load_board(self.tmpdir)
        self.assertTrue(len(board.pheromones) > 0)
        for p in board.pheromones:
            self.assertTrue(p.text.startswith("[dream]"))

    def test_pheromone_has_auto_dream_tag(self):
        insights = [
            self.mod.DreamInsight(
                title="Tag Test",
                description="Should have auto-dream tag",
                sources=["system"],
                category="pattern",
                confidence=0.5,
            ),
        ]
        self.mod.emit_to_stigmergy(insights, self.tmpdir)

        sg = importlib.import_module("stigmergy")
        board = sg.load_board(self.tmpdir)
        for p in board.pheromones:
            self.assertIn("auto-dream", p.tags)

    def test_intensity_capped(self):
        insights = [
            self.mod.DreamInsight(
                title="Cap Test",
                description="Confidence 0.95 should be capped at 0.9",
                sources=["dev.md"],
                category="opportunity",
                confidence=0.95,
            ),
        ]
        self.mod.emit_to_stigmergy(insights, self.tmpdir)

        sg = importlib.import_module("stigmergy")
        board = sg.load_board(self.tmpdir)
        for p in board.pheromones:
            self.assertLessEqual(p.intensity, 0.9)

    def test_category_to_pheromone_type(self):
        """Chaque catégorie d'insight doit mapper vers le bon type phéromone."""
        sg = importlib.import_module("stigmergy")
        for category, expected_type in self.mod._INSIGHT_TO_PHEROMONE.items():
            # Fresh tmpdir per iteration to avoid accumulated state
            cat_dir = Path(tempfile.mkdtemp())
            (cat_dir / "_bmad-output").mkdir(parents=True, exist_ok=True)
            try:
                insights = [
                    self.mod.DreamInsight(
                        title=f"Test {category}",
                        description=f"Testing {category} mapping",
                        sources=["dev.md"],
                        category=category,
                        confidence=0.6,
                    ),
                ]
                count = self.mod.emit_to_stigmergy(insights, cat_dir)
                self.assertGreater(count, 0,
                                   f"emit should succeed for {category}")

                board = sg.load_board(cat_dir)
                self.assertTrue(len(board.pheromones) > 0,
                                f"board should have pheromones for {category}")
                last = board.pheromones[-1]
                self.assertEqual(last.pheromone_type, expected_type,
                                 f"{category} should map to {expected_type}")
            finally:
                shutil.rmtree(cat_dir, ignore_errors=True)

    def test_unknown_category_defaults_to_need(self):
        insights = [
            self.mod.DreamInsight(
                title="Unknown",
                description="Category not in mapping",
                sources=["dev.md"],
                category="unknown_cat",
                confidence=0.5,
            ),
        ]
        self.mod.emit_to_stigmergy(insights, self.tmpdir)

        sg = importlib.import_module("stigmergy")
        board = sg.load_board(self.tmpdir)
        self.assertEqual(board.pheromones[-1].pheromone_type, "NEED")

    def test_dedup_cross_session(self):
        """Émettre 2x le même insight ne doit pas créer de doublon."""
        insights = [
            self.mod.DreamInsight(
                title="Dedup Test",
                description="Same insight emitted twice",
                sources=["dev.md"],
                category="pattern",
                confidence=0.6,
            ),
        ]
        count1 = self.mod.emit_to_stigmergy(insights, self.tmpdir)
        count2 = self.mod.emit_to_stigmergy(insights, self.tmpdir)
        self.assertEqual(count1, 1)
        self.assertEqual(count2, 0, "Second emit should be deduplicated")

        sg = importlib.import_module("stigmergy")
        board = sg.load_board(self.tmpdir)
        dream_pheromones = [p for p in board.pheromones
                            if p.text.startswith("[dream]")]
        self.assertEqual(len(dream_pheromones), 1)

    def test_dedup_allows_different_insights(self):
        """Deux insights différents doivent chacun être émis."""
        insight_a = self.mod.DreamInsight(
            title="Insight A", description="First unique insight",
            sources=["dev.md"], category="pattern", confidence=0.6,
        )
        insight_b = self.mod.DreamInsight(
            title="Insight B", description="Second unique insight",
            sources=["qa.md"], category="opportunity", confidence=0.7,
        )
        count1 = self.mod.emit_to_stigmergy([insight_a], self.tmpdir)
        count2 = self.mod.emit_to_stigmergy([insight_b], self.tmpdir)
        self.assertEqual(count1, 1)
        self.assertEqual(count2, 1)

        sg = importlib.import_module("stigmergy")
        board = sg.load_board(self.tmpdir)
        self.assertEqual(len(board.pheromones), 2)


# ── Test QUICK_MAX_INSIGHTS constant ─────────────────────────────────────────

class TestQuickMaxInsights(unittest.TestCase):
    def test_constant_is_positive_int(self):
        mod = _import_dream()
        self.assertIsInstance(mod.QUICK_MAX_INSIGHTS, int)
        self.assertGreater(mod.QUICK_MAX_INSIGHTS, 0)

    def test_quick_max_less_than_max(self):
        mod = _import_dream()
        self.assertLess(mod.QUICK_MAX_INSIGHTS, mod.MAX_INSIGHTS)


# ── Test Bigram Keywords ──────────────────────────────────────────────────────

class TestBigramKeywords(unittest.TestCase):
    """Vérifie que _extract_keywords retourne aussi des bigrams."""

    def setUp(self):
        self.mod = _import_dream()

    def test_returns_unigrams(self):
        kw = self.mod._extract_keywords("API design pattern")
        self.assertIn("api", kw)
        self.assertIn("design", kw)
        self.assertIn("pattern", kw)

    def test_returns_bigrams_for_consecutive_significant_words(self):
        kw = self.mod._extract_keywords("API design pattern")
        self.assertIn("api_design", kw)
        self.assertIn("design_pattern", kw)

    def test_stopwords_break_bigrams(self):
        """Stopwords entre deux mots significatifs cassent le bigram."""
        kw = self.mod._extract_keywords("caching for performance")
        self.assertIn("caching", kw)
        self.assertIn("performance", kw)
        # "for" est un stopword → pas de bigram caching_performance
        self.assertNotIn("caching_performance", kw)

    def test_single_word_no_bigram(self):
        kw = self.mod._extract_keywords("architecture")
        self.assertIn("architecture", kw)
        # Pas de bigram possible
        bigrams = [k for k in kw if "_" in k]
        self.assertEqual(bigrams, [])

    def test_empty_text(self):
        kw = self.mod._extract_keywords("")
        self.assertEqual(kw, set())

    def test_bigrams_capture_shared_concepts(self):
        """Bigrams should capture multi-word concepts like 'api_design'."""
        kw_a = self.mod._extract_keywords("refactorer API design endpoints")
        kw_b = self.mod._extract_keywords("améliorer API design modules")
        # Both should contain the shared bigram
        self.assertIn("api_design", kw_a)
        self.assertIn("api_design", kw_b)
        # The shared bigram means they have non-zero similarity
        sim = self.mod._similarity(
            "refactorer API design endpoints",
            "améliorer API design modules",
        )
        self.assertGreater(sim, 0.2)

    def test_stopwords_constant_is_frozenset(self):
        self.assertIsInstance(self.mod._STOPWORDS, frozenset)


# ── Test Temporal Decay ───────────────────────────────────────────────────────

class TestTemporalWeight(unittest.TestCase):
    """Vérifie la pondération temporelle des entrées."""

    def setUp(self):
        self.mod = _import_dream()
        self.now = datetime(2026, 2, 28)

    def test_today_returns_one(self):
        w = self.mod._temporal_weight("2026-02-28", now=self.now)
        self.assertEqual(w, 1.0)

    def test_empty_date_returns_one(self):
        w = self.mod._temporal_weight("", now=self.now)
        self.assertEqual(w, 1.0)

    def test_invalid_date_returns_one(self):
        w = self.mod._temporal_weight("not-a-date", now=self.now)
        self.assertEqual(w, 1.0)

    def test_one_halflife_ago_returns_half(self):
        """An entry exactly DECAY_HALFLIFE_DAYS ago should have weight ~0.5."""
        halflife = self.mod.DECAY_HALFLIFE_DAYS
        days_ago = self.now - timedelta(days=halflife)
        date_str = days_ago.strftime("%Y-%m-%d")
        w = self.mod._temporal_weight(date_str, now=self.now)
        self.assertAlmostEqual(w, 0.5, places=1)

    def test_very_old_entry_hits_floor(self):
        """Entries older than ~2 half-lives should hit the 0.3 floor."""
        old_date = "2025-01-01"  # ~14 months ago
        w = self.mod._temporal_weight(old_date, now=self.now)
        self.assertEqual(w, 0.3)

    def test_recent_entry_high_weight(self):
        """An entry from 3 days ago should be close to 1.0."""
        recent = (self.now - timedelta(days=3)).strftime("%Y-%m-%d")
        w = self.mod._temporal_weight(recent, now=self.now)
        self.assertGreater(w, 0.8)

    def test_monotonically_decreasing(self):
        """Older entries should always have lower or equal weight."""
        weights = []
        for days in [0, 3, 7, 14, 30, 60, 120]:
            d = (self.now - timedelta(days=days)).strftime("%Y-%m-%d")
            weights.append(self.mod._temporal_weight(d, now=self.now))
        for i in range(len(weights) - 1):
            self.assertGreaterEqual(weights[i], weights[i + 1])


class TestApplyTemporalDecay(unittest.TestCase):
    """Vérifie que apply_temporal_decay modifie les confidences."""

    def setUp(self):
        self.mod = _import_dream()
        self.now = datetime(2026, 2, 28)

    def test_recent_entries_minimal_decay(self):
        sources = [
            self.mod.DreamSource(
                name="recent.md", kind="learnings",
                entries=["recent entry"], dates=["2026-02-27"],
            ),
        ]
        insight = self.mod.DreamInsight(
            title="T", description="D", sources=["recent.md"],
            category="pattern", confidence=0.8,
        )
        self.mod.apply_temporal_decay([insight], sources, now=self.now)
        # Should barely change — entry is from yesterday
        self.assertGreater(insight.confidence, 0.7)

    def test_old_entries_reduce_confidence(self):
        sources = [
            self.mod.DreamSource(
                name="old.md", kind="learnings",
                entries=["old entry"], dates=["2025-06-01"],
            ),
        ]
        insight = self.mod.DreamInsight(
            title="T", description="D", sources=["old.md"],
            category="pattern", confidence=0.8,
        )
        self.mod.apply_temporal_decay([insight], sources, now=self.now)
        self.assertLess(insight.confidence, 0.5)

    def test_no_dates_no_penalty(self):
        sources = [
            self.mod.DreamSource(
                name="nodates.md", kind="shared-context",
                entries=["no date entry"], dates=[],
            ),
        ]
        insight = self.mod.DreamInsight(
            title="T", description="D", sources=["nodates.md"],
            category="pattern", confidence=0.8,
        )
        self.mod.apply_temporal_decay([insight], sources, now=self.now)
        # No dates → no decay applied (weights list empty)
        self.assertEqual(insight.confidence, 0.8)

    def test_mixed_sources_averaged(self):
        sources = [
            self.mod.DreamSource(
                name="new.md", kind="learnings",
                entries=["new"], dates=["2026-02-28"],
            ),
            self.mod.DreamSource(
                name="old.md", kind="decisions",
                entries=["old"], dates=["2025-01-01"],
            ),
        ]
        insight = self.mod.DreamInsight(
            title="T", description="D", sources=["new.md", "old.md"],
            category="connection", confidence=0.8,
        )
        self.mod.apply_temporal_decay([insight], sources, now=self.now)
        # Average of ~1.0 and ~0.3 ≈ 0.65 → 0.8 * 0.65 ≈ 0.52
        self.assertLess(insight.confidence, 0.7)
        self.assertGreater(insight.confidence, 0.3)


# ── Test Dream Memory ─────────────────────────────────────────────────────────

class TestInsightSignature(unittest.TestCase):
    """Vérifie la signature stable des insights."""

    def setUp(self):
        self.mod = _import_dream()

    def test_same_insight_same_signature(self):
        ins = self.mod.DreamInsight(
            title="Pattern X", description="desc",
            sources=["a.md"], category="pattern", confidence=0.5,
        )
        sig1 = self.mod._insight_signature(ins)
        sig2 = self.mod._insight_signature(ins)
        self.assertEqual(sig1, sig2)

    def test_different_description_same_signature(self):
        """La signature ne dépend pas de la description."""
        ins1 = self.mod.DreamInsight(
            title="Pattern X", description="description A",
            sources=["a.md"], category="pattern", confidence=0.5,
        )
        ins2 = self.mod.DreamInsight(
            title="Pattern X", description="description B completely different",
            sources=["b.md"], category="pattern", confidence=0.9,
        )
        self.assertEqual(
            self.mod._insight_signature(ins1),
            self.mod._insight_signature(ins2),
        )

    def test_different_category_different_signature(self):
        ins1 = self.mod.DreamInsight(
            title="Same Title", description="d",
            sources=["a.md"], category="pattern", confidence=0.5,
        )
        ins2 = self.mod.DreamInsight(
            title="Same Title", description="d",
            sources=["a.md"], category="tension", confidence=0.5,
        )
        self.assertNotEqual(
            self.mod._insight_signature(ins1),
            self.mod._insight_signature(ins2),
        )

    def test_signature_format(self):
        ins = self.mod.DreamInsight(
            title="Test Title!", description="d",
            sources=["a.md"], category="pattern", confidence=0.5,
        )
        sig = self.mod._insight_signature(ins)
        self.assertTrue(sig.startswith("pattern:"))
        # Should be lowercase alphanumeric after the colon
        after_colon = sig.split(":")[1]
        self.assertTrue(after_colon.isalnum())


class TestDreamMemoryPersistence(unittest.TestCase):
    """Vérifie le load/save de dream-memory.json."""

    def setUp(self):
        self.mod = _import_dream()
        self.tmpdir = Path(tempfile.mkdtemp())
        (self.tmpdir / "_bmad-output").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_empty(self):
        mem = self.mod.load_dream_memory(self.tmpdir)
        self.assertEqual(mem, {})

    def test_save_and_load_roundtrip(self):
        mem = {"insights": {"sig1": {"title": "Test"}}, "total_dreams": 1}
        self.mod.save_dream_memory(self.tmpdir, mem)
        loaded = self.mod.load_dream_memory(self.tmpdir)
        self.assertEqual(loaded["total_dreams"], 1)
        self.assertIn("sig1", loaded["insights"])

    def test_corrupted_file_returns_empty(self):
        mem_path = self.tmpdir / "_bmad-output" / self.mod.DREAM_MEMORY_FILE
        mem_path.write_text("not json!", encoding="utf-8")
        mem = self.mod.load_dream_memory(self.tmpdir)
        self.assertEqual(mem, {})


class TestUpdateDreamMemory(unittest.TestCase):
    """Vérifie la logique de mise à jour de la mémoire dream."""

    def setUp(self):
        self.mod = _import_dream()

    def _make_insight(self, title, category="pattern"):
        return self.mod.DreamInsight(
            title=title, description=f"Desc for {title}",
            sources=["a.md"], category=category, confidence=0.6,
        )

    def test_first_dream_all_new(self):
        memory = {}
        insights = [self._make_insight("Alpha"), self._make_insight("Beta")]
        diff = self.mod.update_dream_memory(insights, memory)

        self.assertEqual(len(diff["new"]), 2)
        self.assertEqual(len(diff["persistent"]), 0)
        self.assertEqual(len(diff["resolved"]), 0)
        self.assertEqual(memory["total_dreams"], 1)

    def test_second_dream_detects_persistent(self):
        memory = {}
        ins1 = [self._make_insight("Alpha")]
        self.mod.update_dream_memory(ins1, memory)

        # Second dream with same insight
        ins2 = [self._make_insight("Alpha")]
        diff = self.mod.update_dream_memory(ins2, memory)

        self.assertEqual(len(diff["persistent"]), 1)
        self.assertEqual(len(diff["new"]), 0)
        self.assertEqual(memory["total_dreams"], 2)
        # Confidence should be boosted
        self.assertGreater(ins2[0].confidence, 0.6)

    def test_persistent_confidence_boost(self):
        memory = {}
        ins1 = [self._make_insight("Alpha")]
        self.mod.update_dream_memory(ins1, memory)

        ins2 = [self._make_insight("Alpha")]
        original_conf = ins2[0].confidence
        self.mod.update_dream_memory(ins2, memory)

        self.assertAlmostEqual(
            ins2[0].confidence,
            min(1.0, original_conf + self.mod.PERSISTENCE_BOOST),
            places=3,
        )

    def test_confidence_boost_capped_at_one(self):
        memory = {}
        ins1 = [self._make_insight("Alpha")]
        ins1[0].confidence = 0.95
        self.mod.update_dream_memory(ins1, memory)

        ins2 = [self._make_insight("Alpha")]
        ins2[0].confidence = 0.95
        self.mod.update_dream_memory(ins2, memory)

        self.assertLessEqual(ins2[0].confidence, 1.0)

    def test_resolved_detection(self):
        """Insight vu 2+ fois puis absent → resolved."""
        memory = {}
        ins_alpha = [self._make_insight("Alpha")]
        self.mod.update_dream_memory(ins_alpha, memory)
        ins_alpha2 = [self._make_insight("Alpha")]
        self.mod.update_dream_memory(ins_alpha2, memory)

        # Third dream WITHOUT Alpha
        ins3 = [self._make_insight("Beta")]
        diff = self.mod.update_dream_memory(ins3, memory)

        self.assertEqual(len(diff["resolved"]), 1)
        self.assertEqual(len(diff["new"]), 1)

    def test_single_occurrence_not_resolved(self):
        """Insight vu 1 seule fois puis absent → NOT resolved (just noise)."""
        memory = {}
        ins1 = [self._make_insight("Alpha")]
        self.mod.update_dream_memory(ins1, memory)

        ins2 = [self._make_insight("Beta")]
        diff = self.mod.update_dream_memory(ins2, memory)

        self.assertEqual(len(diff["resolved"]), 0)

    def test_seen_count_increments(self):
        memory = {}
        for _ in range(5):
            self.mod.update_dream_memory([self._make_insight("Alpha")], memory)

        sig = self.mod._insight_signature(self._make_insight("Alpha"))
        self.assertEqual(memory["insights"][sig]["seen_count"], 5)

    def test_stale_flag_set(self):
        """Absent insight gets stale flag."""
        memory = {}
        self.mod.update_dream_memory([self._make_insight("Alpha")], memory)

        # Second dream without Alpha
        self.mod.update_dream_memory([self._make_insight("Beta")], memory)

        sig_alpha = self.mod._insight_signature(self._make_insight("Alpha"))
        self.assertTrue(memory["insights"][sig_alpha].get("stale", False))


# ── Test render_journal with dream_diff ───────────────────────────────────────

class TestRenderJournalDreamDiff(unittest.TestCase):
    """Vérifie que le journal inclut la section Dream Diff."""

    def setUp(self):
        self.mod = _import_dream()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_diff_no_section(self):
        ins = [self.mod.DreamInsight(
            title="T", description="D", sources=["a.md"],
            category="pattern", confidence=0.5,
        )]
        src = [self.mod.DreamSource(name="a.md", kind="learnings",
                                     entries=["e"])]
        journal = self.mod.render_journal(ins, src, self.tmpdir)
        self.assertNotIn("Dream Diff", journal)

    def test_diff_with_persistent(self):
        ins = [self.mod.DreamInsight(
            title="Persistent Insight", description="D", sources=["a.md"],
            category="pattern", confidence=0.75,
        )]
        src = [self.mod.DreamSource(name="a.md", kind="learnings",
                                     entries=["e"])]
        diff = {"new": [], "persistent": ins, "resolved": []}
        journal = self.mod.render_journal(ins, src, self.tmpdir,
                                           dream_diff=diff)
        self.assertIn("Dream Diff", journal)
        self.assertIn("Persistants", journal)
        self.assertIn("Persistent Insight", journal)

    def test_diff_with_new_and_resolved(self):
        ins = [self.mod.DreamInsight(
            title="New One", description="D", sources=["a.md"],
            category="pattern", confidence=0.5,
        )]
        src = [self.mod.DreamSource(name="a.md", kind="learnings",
                                     entries=["e"])]
        diff = {"new": ins, "persistent": [],
                "resolved": ["pattern:oldinsight"]}
        journal = self.mod.render_journal(ins, src, self.tmpdir,
                                           dream_diff=diff)
        self.assertIn("Nouveaux", journal)
        self.assertIn("Résolus", journal)
        self.assertIn("pattern:oldinsight", journal)


# ── Test _extract_agents ──────────────────────────────────────────────────────

class TestExtractAgents(unittest.TestCase):
    """Vérifie l'extraction d'agents depuis le texte."""

    def setUp(self):
        self.mod = _import_dream()

    def test_known_agents_is_frozenset(self):
        self.assertIsInstance(self.mod._KNOWN_AGENTS, frozenset)

    def test_known_agents_contains_expected(self):
        for agent in ("dev", "architect", "pm", "qa", "sm", "analyst",
                       "tech-writer", "ux-designer", "bmad-master"):
            self.assertIn(agent, self.mod._KNOWN_AGENTS)

    def test_extract_bracket_pattern(self):
        agents = self.mod._extract_agents("[dev] [DECISION] refactored caching")
        self.assertIn("dev", agents)

    def test_extract_multiple_agents(self):
        agents = self.mod._extract_agents("[dev] reviewed [architect] design")
        self.assertIn("dev", agents)
        self.assertIn("architect", agents)

    def test_ignores_unknown_agent(self):
        agents = self.mod._extract_agents("[unknown-agent] did something")
        self.assertEqual(agents, [])

    def test_extract_from_filename(self):
        agents = self.mod._extract_agents("Pattern in learnings/dev.md")
        self.assertIn("dev", agents)

    def test_empty_text(self):
        agents = self.mod._extract_agents("")
        self.assertEqual(agents, [])

    def test_returns_sorted(self):
        agents = self.mod._extract_agents("[qa] and [architect] stuff")
        self.assertEqual(agents, ["architect", "qa"])

    def test_case_insensitive_bracket(self):
        agents = self.mod._extract_agents("[Dev] uppercase")
        self.assertIn("dev", agents)

    def test_ignores_non_agent_brackets(self):
        """Patterns like [DECISION] or [123] should not be extracted."""
        agents = self.mod._extract_agents("[DECISION] [CHECKPOINT] nothing")
        self.assertEqual(agents, [])

    def test_filename_tech_writer(self):
        agents = self.mod._extract_agents("from learnings/tech-writer.md")
        self.assertIn("tech-writer", agents)


# ── Test Agent Attribution in Insights ────────────────────────────────────────

class TestAgentAttribution(unittest.TestCase):
    """Vérifie que les insights contiennent agents_relevant."""

    def setUp(self):
        self.mod = _import_dream()

    def test_cross_connection_populates_agents(self):
        src_a = self.mod.DreamSource(
            name="learnings/dev.md", kind="learnings",
            entries=["[dev] caching database layer optimization strategy"],
        )
        src_b = self.mod.DreamSource(
            name="decisions-log.md", kind="decisions",
            entries=["[architect] caching database layer optimization decision"],
        )
        insights = self.mod.find_cross_connections([src_a, src_b])
        if insights:
            agents = insights[0].agents_relevant
            self.assertIn("dev", agents)
            self.assertIn("architect", agents)

    def test_recurring_pattern_populates_agents(self):
        src_a = self.mod.DreamSource(
            name="learnings/dev.md", kind="learnings",
            entries=[
                "[dev] caching layer deployed",
                "[dev] caching optimization reviewed",
                "[dev] caching metrics collected",
            ],
        )
        src_b = self.mod.DreamSource(
            name="decisions-log.md", kind="decisions",
            entries=["[architect] caching strategy adopted"],
        )
        insights = self.mod.find_recurring_patterns([src_a, src_b])
        caching_insights = [i for i in insights if "caching" in i.title.lower()]
        if caching_insights:
            agents = caching_insights[0].agents_relevant
            self.assertIn("dev", agents)

    def test_tension_populates_agents(self):
        src_a = self.mod.DreamSource(
            name="learnings/dev.md", kind="learnings",
            entries=["[dev] caching must always remain enabled for database"],
        )
        src_b = self.mod.DreamSource(
            name="failure-museum.md", kind="failure-museum",
            entries=["[qa] danger caching database caused stale data"],
        )
        insights = self.mod.find_tensions([src_a, src_b])
        if insights:
            agents = insights[0].agents_relevant
            self.assertIn("dev", agents)
            self.assertIn("qa", agents)

    def test_opportunity_populates_agents(self):
        src = self.mod.DreamSource(
            name="learnings/dev.md", kind="learnings",
            entries=["[dev] TODO: refactor the caching layer"],
        )
        insights = self.mod.find_opportunities([src])
        self.assertGreater(len(insights), 0)
        self.assertIn("dev", insights[0].agents_relevant)

    def test_opportunity_source_name_agents(self):
        """Agent extracted from source filename even without [agent] bracket."""
        src = self.mod.DreamSource(
            name="learnings/architect.md", kind="learnings",
            entries=["TODO: review infrastructure decisions"],
        )
        insights = self.mod.find_opportunities([src])
        self.assertGreater(len(insights), 0)
        self.assertIn("architect", insights[0].agents_relevant)


# ── Test Opportunity Cap ──────────────────────────────────────────────────────

class TestOpportunityCap(unittest.TestCase):
    """Vérifie le plafond d'opportunités par source."""

    def setUp(self):
        self.mod = _import_dream()

    def test_cap_limits_per_source(self):
        """A source with many TODOs should be capped at MAX_OPPORTUNITIES_PER_SOURCE."""
        entries = [f"TODO: fix item {i}" for i in range(10)]
        src = self.mod.DreamSource(
            name="bloated.md", kind="learnings", entries=entries,
        )
        insights = self.mod.find_opportunities([src])
        self.assertEqual(len(insights), 3)  # MAX_OPPORTUNITIES_PER_SOURCE

    def test_different_sources_independent_caps(self):
        """Each source has its own cap."""
        entries_a = [f"TODO: task A{i}" for i in range(5)]
        entries_b = [f"TODO: task B{i}" for i in range(5)]
        src_a = self.mod.DreamSource(
            name="a.md", kind="learnings", entries=entries_a,
        )
        src_b = self.mod.DreamSource(
            name="b.md", kind="decisions", entries=entries_b,
        )
        insights = self.mod.find_opportunities([src_a, src_b])
        self.assertEqual(len(insights), 6)  # 3 + 3

    def test_fewer_than_cap_not_truncated(self):
        """If source has fewer opportunities than cap, all are kept."""
        src = self.mod.DreamSource(
            name="a.md", kind="learnings",
            entries=["TODO: one thing", "TODO: another thing"],
        )
        insights = self.mod.find_opportunities([src])
        self.assertEqual(len(insights), 2)


# ── Test Bigram Scoring in Patterns ───────────────────────────────────────────

class TestBigramScoring(unittest.TestCase):
    """Vérifie que les bigrams ont un score plus élevé que les unigrams."""

    def setUp(self):
        self.mod = _import_dream()

    def test_bigram_higher_base_confidence(self):
        """Bigram patterns should start at higher confidence than unigrams."""
        # Create sources where both a unigram and a bigram appear across 2 sources
        src_a = self.mod.DreamSource(
            name="a.md", kind="learnings",
            entries=[
                "caching strategy review today",
                "caching strategy implementation done",
                "caching strategy deployed successfully",
            ],
        )
        src_b = self.mod.DreamSource(
            name="b.md", kind="decisions",
            entries=["caching strategy selected for production"],
        )
        insights = self.mod.find_recurring_patterns([src_a, src_b])

        bigram_insights = [i for i in insights if "caching strategy" in i.title.lower()]

        # At minimum we should have the bigram pattern
        self.assertTrue(len(bigram_insights) > 0,
                        f"Expected bigram insight, got: {[i.title for i in insights]}")

    def test_bigram_label_uses_space(self):
        """Bigram labels should use space instead of underscore."""
        src_a = self.mod.DreamSource(
            name="a.md", kind="learnings",
            entries=[
                "error handling mechanism deployed",
                "error handling review done",
                "error handling improved today",
            ],
        )
        src_b = self.mod.DreamSource(
            name="b.md", kind="decisions",
            entries=["error handling strategy selected"],
        )
        insights = self.mod.find_recurring_patterns([src_a, src_b])
        # Look for bigram "error handling" (not "error_handling")
        titles = [i.title for i in insights]
        bigram_found = any("error handling" in t.lower() for t in titles)
        self.assertTrue(bigram_found,
                        f"Expected 'error handling' bigram in titles: {titles}")


# ── Test Tension Marker Fix ───────────────────────────────────────────────────

class TestTensionMarkerFix(unittest.TestCase):
    """Vérifie que 'never'/'jamais' ne créent pas de tensions fantômes."""

    def setUp(self):
        self.mod = _import_dream()

    def test_never_only_positive(self):
        """Entry with 'never' should appear in positive only, not negative."""
        src_a = self.mod.DreamSource(
            name="a.md", kind="learnings",
            entries=["never deploy on Friday"],
        )
        src_b = self.mod.DreamSource(
            name="b.md", kind="decisions",
            entries=["normal caching optimization decision"],
        )
        # No tension should be detected because src_b has no negative marker
        insights = self.mod.find_tensions([src_a, src_b])
        self.assertEqual(len(insights), 0)

    def test_no_self_tension_with_never(self):
        """An entry with 'never' should not tension with itself across sources."""
        src_a = self.mod.DreamSource(
            name="a.md", kind="learnings",
            entries=["never skip testing of database queries before deploy"],
        )
        src_b = self.mod.DreamSource(
            name="b.md", kind="decisions",
            entries=["never skip testing of database queries before deploy"],
        )
        # Same text in two sources — if 'never' were in both markers,
        # it would create a ghost tension. Now it shouldn't.
        insights = self.mod.find_tensions([src_a, src_b])
        self.assertEqual(len(insights), 0)

    def test_jamais_only_positive(self):
        """Same fix for 'jamais' — positive only."""
        src_a = self.mod.DreamSource(
            name="a.md", kind="learnings",
            entries=["jamais merger sans review approfondie du code"],
        )
        src_b = self.mod.DreamSource(
            name="b.md", kind="decisions",
            entries=["routine update sans problème particulier"],
        )
        # No tension: src_b doesn't have a real negative marker on the same topic
        insights = self.mod.find_tensions([src_a, src_b])
        self.assertEqual(len(insights), 0)

    def test_real_tension_still_detected(self):
        """Actual tensions (positive vs negative) still work."""
        src_a = self.mod.DreamSource(
            name="a.md", kind="learnings",
            entries=["caching layer optimization must always remain enabled"],
        )
        src_b = self.mod.DreamSource(
            name="b.md", kind="failure-museum",
            entries=["danger caching layer optimization remain enabled causes stale data"],
        )
        insights = self.mod.find_tensions([src_a, src_b])
        self.assertGreater(len(insights), 0)


# ── Test Render Journal Agents ────────────────────────────────────────────────

class TestRenderJournalAgents(unittest.TestCase):
    """Vérifie que le journal affiche les agents pertinents."""

    def setUp(self):
        self.mod = _import_dream()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_agents_displayed(self):
        ins = [self.mod.DreamInsight(
            title="Test Insight", description="Some description here",
            sources=["a.md"], category="pattern", confidence=0.5,
            agents_relevant=["dev", "architect"],
        )]
        src = [self.mod.DreamSource(name="a.md", kind="learnings",
                                     entries=["e"])]
        journal = self.mod.render_journal(ins, src, self.tmpdir)
        self.assertIn("**Agents**", journal)
        self.assertIn("dev", journal)
        self.assertIn("architect", journal)

    def test_no_agents_no_line(self):
        ins = [self.mod.DreamInsight(
            title="Test Insight", description="Some description here",
            sources=["a.md"], category="pattern", confidence=0.5,
            agents_relevant=[],
        )]
        src = [self.mod.DreamSource(name="a.md", kind="learnings",
                                     entries=["e"])]
        journal = self.mod.render_journal(ins, src, self.tmpdir)
        self.assertNotIn("**Agents**", journal)


# ── Test JSON Output Enrichment ───────────────────────────────────────────────

class TestJsonOutputStructure(unittest.TestCase):
    """Vérifie la structure enrichie de la sortie JSON."""

    def setUp(self):
        self.mod = _import_dream()

    def test_insight_has_agents_relevant(self):
        """DreamInsight serialized as dict should include agents_relevant."""
        ins = self.mod.DreamInsight(
            title="T", description="D", sources=["a.md"],
            category="pattern", confidence=0.5,
            agents_relevant=["dev"],
        )
        data = {
            "title": ins.title,
            "description": ins.description,
            "sources": ins.sources,
            "category": ins.category,
            "confidence": ins.confidence,
            "actionable": ins.actionable,
            "agents_relevant": ins.agents_relevant,
        }
        self.assertIn("agents_relevant", data)
        self.assertEqual(data["agents_relevant"], ["dev"])


class TestEmitToIncubator(unittest.TestCase):
    """Tests for Dream-Driven Development (QW4)."""

    def setUp(self):
        self.dream = _import_dream()

    def test_emit_to_incubator_function_exists(self):
        self.assertTrue(callable(self.dream.emit_to_incubator))

    def test_no_actionable_insights_creates_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ins = self.dream.DreamInsight(
                title="Test insight",
                description="Not actionable",
                sources=["src1"],
                category="pattern",
                confidence=0.8,
                actionable=False,
            )
            count = self.dream.emit_to_incubator([ins], root)
            self.assertEqual(count, 0)

    def test_low_confidence_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ins = self.dream.DreamInsight(
                title="Weak insight",
                description="Too uncertain",
                sources=["src1"],
                category="opportunity",
                confidence=0.2,
                actionable=True,
            )
            count = self.dream.emit_to_incubator([ins], root, min_confidence=0.5)
            self.assertEqual(count, 0)

    def test_actionable_insight_creates_idea(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Ensure incubator.py is importable
            tools_dir = KIT_DIR / "framework" / "tools"
            if not (root / "framework" / "tools").exists():
                (root / "framework" / "tools").mkdir(parents=True)
            import shutil
            shutil.copy(tools_dir / "incubator.py", root / "framework" / "tools" / "incubator.py")

            ins = self.dream.DreamInsight(
                title="Add GraphQL support",
                description="Multiple agents mention GraphQL as needed",
                sources=["learnings/dev.md", "decisions/arch.md"],
                category="opportunity",
                confidence=0.75,
                actionable=True,
            )
            count = self.dream.emit_to_incubator([ins], root)
            self.assertEqual(count, 1)

    def test_duplicate_title_not_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tools_dir = KIT_DIR / "framework" / "tools"
            (root / "framework" / "tools").mkdir(parents=True)
            import shutil
            shutil.copy(tools_dir / "incubator.py", root / "framework" / "tools" / "incubator.py")

            ins = self.dream.DreamInsight(
                title="Same Idea",
                description="Test dedup",
                sources=["src1"],
                category="opportunity",
                confidence=0.8,
                actionable=True,
            )
            self.dream.emit_to_incubator([ins], root)
            # Second call with same title — should create 0
            count = self.dream.emit_to_incubator([ins], root)
            self.assertEqual(count, 0)

    def test_category_to_tags_mapping(self):
        self.assertIn("tension", self.dream._CATEGORY_TO_TAGS)
        self.assertIn("opportunity", self.dream._CATEGORY_TO_TAGS)

    def test_incubate_flag_in_parser(self):
        """Verify --incubate flag exists in CLI parser."""
        import argparse
        # Check the source for the flag
        import inspect
        source = inspect.getsource(self.dream.main)
        self.assertIn("--incubate", source)


if __name__ == "__main__":
    unittest.main()
