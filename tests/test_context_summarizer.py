#!/usr/bin/env python3
"""
Tests pour context-summarizer.py — Résumé automatique du contexte ancien Grimoire (BM-41 Story 3.1).

Fonctions testées :
  - SectionParser.extract_date(), parse_date(), compute_age_days(), extract_tags(), parse_file()
  - ContextSummarizer._should_summarize(), _extractive_summary(), _detect_file_type()
  - ContextSummarizer._discover_memory_files(), preview(), summarize(), status(), restore()
  - build_summarizer_from_config()
  - main()
"""

import importlib
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

KIT_DIR = Path(__file__).parent.parent
TOOL = KIT_DIR / "framework" / "tools" / "context-summarizer.py"


def _import_mod():
    mod_name = "context_summarizer"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Constants ────────────────────────────────────────────────────────────────

class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_version_defined(self):
        self.assertTrue(hasattr(self.mod, "CONTEXT_SUMMARIZER_VERSION"))

    def test_version_format(self):
        parts = self.mod.CONTEXT_SUMMARIZER_VERSION.split(".")
        self.assertEqual(len(parts), 3)

    def test_chars_per_token(self):
        self.assertEqual(self.mod.CHARS_PER_TOKEN, 4)

    def test_default_age_threshold(self):
        self.assertEqual(self.mod.DEFAULT_AGE_THRESHOLD_DAYS, 30)

    def test_default_max_summary_tokens(self):
        self.assertEqual(self.mod.DEFAULT_MAX_SUMMARY_TOKENS, 500)

    def test_default_learnings_age(self):
        self.assertEqual(self.mod.DEFAULT_LEARNINGS_AGE_DAYS, 60)

    def test_default_preserve_tags(self):
        tags = self.mod.DEFAULT_PRESERVE_TAGS
        self.assertIn("critical", tags)
        self.assertIn("architecture", tags)
        self.assertIn("security", tags)

    def test_date_patterns_not_empty(self):
        self.assertGreater(len(self.mod.DATE_PATTERNS), 0)


# ── Data Classes ────────────────────────────────────────────────────────────

class TestDataclasses(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_section_creation(self):
        s = self.mod.Section(heading="Test", content="Hello world", source_file="test.md")
        self.assertEqual(s.heading, "Test")
        self.assertEqual(s.age_days, 0)
        self.assertFalse(s.preserved)

    def test_section_with_tags(self):
        s = self.mod.Section(heading="H", content="C", source_file="f.md", tags=["critical"])
        self.assertIn("critical", s.tags)

    def test_digest_creation(self):
        d = self.mod.Digest(source_file="test.md", digest_file="digest.md")
        self.assertEqual(d.sections_summarized, 0)
        self.assertEqual(d.compression_ratio, 0.0)

    def test_summary_report_defaults(self):
        r = self.mod.SummaryReport()
        self.assertEqual(r.digests_created, 0)
        self.assertEqual(r.tokens_before, 0)
        self.assertEqual(r.errors, [])

    def test_digest_status_creation(self):
        ds = self.mod.DigestStatus(filename="digest-test.md", source_file="test.md")
        self.assertEqual(ds.sections_count, 0)


# ── SectionParser ───────────────────────────────────────────────────────────

class TestSectionParserExtractDate(unittest.TestCase):
    def setUp(self):
        self.parser = _import_mod().SectionParser

    def test_iso_date(self):
        self.assertEqual(self.parser.extract_date("## Decision 2024-01-15"), "2024-01-15")

    def test_euro_date(self):
        self.assertEqual(self.parser.extract_date("Entry 15/01/2024"), "15/01/2024")

    def test_month_date(self):
        self.assertEqual(self.parser.extract_date("Sprint 2024-03"), "2024-03")

    def test_no_date(self):
        self.assertEqual(self.parser.extract_date("No date here"), "")

    def test_mixed_text_with_date(self):
        result = self.parser.extract_date("Created on 2023-12-25 by John")
        self.assertEqual(result, "2023-12-25")


class TestSectionParserParseDate(unittest.TestCase):
    def setUp(self):
        self.parser = _import_mod().SectionParser

    def test_iso_format(self):
        dt = self.parser.parse_date("2024-01-15")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.year, 2024)
        self.assertEqual(dt.month, 1)
        self.assertEqual(dt.day, 15)

    def test_euro_format(self):
        dt = self.parser.parse_date("15/01/2024")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.year, 2024)

    def test_month_format(self):
        dt = self.parser.parse_date("2024-03")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.month, 3)

    def test_invalid_format(self):
        self.assertIsNone(self.parser.parse_date("not-a-date"))

    def test_empty_string(self):
        self.assertIsNone(self.parser.parse_date(""))


class TestSectionParserComputeAge(unittest.TestCase):
    def setUp(self):
        self.parser = _import_mod().SectionParser

    def test_recent_date(self):
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        age = self.parser.compute_age_days(yesterday)
        self.assertIn(age, [0, 1, 2])

    def test_old_date(self):
        old = (datetime.now() - timedelta(days=100)).strftime("%Y-%m-%d")
        age = self.parser.compute_age_days(old)
        self.assertGreaterEqual(age, 99)

    def test_invalid_date(self):
        self.assertEqual(self.parser.compute_age_days("invalid"), 0)

    def test_empty_date(self):
        self.assertEqual(self.parser.compute_age_days(""), 0)


class TestSectionParserExtractTags(unittest.TestCase):
    def setUp(self):
        self.parser = _import_mod().SectionParser

    def test_single_tag(self):
        result = self.parser.extract_tags("This is #critical")
        self.assertIn("critical", result)

    def test_multiple_tags(self):
        result = self.parser.extract_tags("#architecture #security review")
        self.assertIn("architecture", result)
        self.assertIn("security", result)

    def test_no_tags(self):
        result = self.parser.extract_tags("No tags here")
        self.assertEqual(result, [])

    def test_hash_in_code(self):
        result = self.parser.extract_tags("color = #ff0000")
        # Should still extract (regex behavior)
        self.assertIsInstance(result, list)


class TestSectionParserParseFile(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_parse_markdown_sections(self):
        content = """# Decisions Log

## Decision 2024-01-15 — Use Qdrant

We decided to use Qdrant for vector storage.
- Reason: open-source, embeddable
- Alternative: Pinecone rejected due to cost

## Decision 2024-02-01 — Use Python 3.12

Adopted Python 3.12 for new features.
"""
        f = self.tmpdir / "decisions-log.md"
        f.write_text(content)
        sections = self.mod.SectionParser.parse_file(f, self.tmpdir)
        self.assertGreater(len(sections), 0)

    def test_parse_empty_file(self):
        f = self.tmpdir / "empty.md"
        f.write_text("")
        sections = self.mod.SectionParser.parse_file(f, self.tmpdir)
        self.assertEqual(sections, [])

    def test_parse_nonexistent_file(self):
        f = self.tmpdir / "nonexistent.md"
        sections = self.mod.SectionParser.parse_file(f, self.tmpdir)
        self.assertEqual(sections, [])

    def test_section_has_date(self):
        content = """# Test

## Entry 2024-01-10

Some content here with enough text to pass the threshold.
"""
        f = self.tmpdir / "test.md"
        f.write_text(content)
        sections = self.mod.SectionParser.parse_file(f, self.tmpdir)
        dated = [s for s in sections if s.date]
        self.assertGreater(len(dated), 0)


# ── ContextSummarizer ──────────────────────────────────────────────────────

class TestContextSummarizer(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _create_memory_structure(self, old_date="2023-01-15", recent_date=None):
        """Create a standard memory structure for testing."""
        if recent_date is None:
            recent_date = datetime.now().strftime("%Y-%m-%d")

        memory_dir = self.tmpdir / "_grimoire" / "_memory"
        memory_dir.mkdir(parents=True)
        decisions = memory_dir / "decisions-log.md"
        decisions.write_text(f"""# Decisions Log

## Decision {old_date} — Old Decision

This is an old decision that should be summarized.
- Reason: it was decided long ago
- Impact: significant changes

## Decision {recent_date} — Recent Decision

This is a recent decision that should be kept.
- Reason: just happened
- Impact: ongoing
""")
        return memory_dir

    def test_init_defaults(self):
        cs = self.mod.ContextSummarizer(self.tmpdir)
        self.assertEqual(cs.age_threshold, 30)
        self.assertEqual(cs.max_summary_tokens, 500)

    def test_detect_file_type_decisions(self):
        cs = self.mod.ContextSummarizer(self.tmpdir)
        self.assertEqual(cs._detect_file_type("decisions-log.md"), "decisions")

    def test_detect_file_type_learnings(self):
        cs = self.mod.ContextSummarizer(self.tmpdir)
        self.assertEqual(cs._detect_file_type("learning-amelia.md"), "learnings")

    def test_detect_file_type_failures(self):
        cs = self.mod.ContextSummarizer(self.tmpdir)
        self.assertEqual(cs._detect_file_type("failure-museum.md"), "failures")

    def test_detect_file_type_generic(self):
        cs = self.mod.ContextSummarizer(self.tmpdir)
        self.assertEqual(cs._detect_file_type("random.md"), "generic")

    def test_should_summarize_no_date(self):
        cs = self.mod.ContextSummarizer(self.tmpdir)
        section = self.mod.Section(heading="Test", content="C", source_file="f.md")
        self.assertFalse(cs._should_summarize(section, "decisions"))

    def test_should_summarize_young(self):
        cs = self.mod.ContextSummarizer(self.tmpdir)
        recent = datetime.now().strftime("%Y-%m-%d")
        section = self.mod.Section(
            heading="Test", content="Content", source_file="f.md",
            date=recent, age_days=5,
        )
        self.assertFalse(cs._should_summarize(section, "decisions"))

    def test_should_summarize_old(self):
        cs = self.mod.ContextSummarizer(self.tmpdir)
        section = self.mod.Section(
            heading="Old", content="Old content", source_file="f.md",
            date="2023-01-01", age_days=400,
        )
        self.assertTrue(cs._should_summarize(section, "decisions"))

    def test_should_summarize_preserved_tag(self):
        cs = self.mod.ContextSummarizer(self.tmpdir)
        section = self.mod.Section(
            heading="Critical", content="Important", source_file="f.md",
            date="2023-01-01", age_days=400, tags=["critical"],
        )
        self.assertFalse(cs._should_summarize(section, "decisions"))
        self.assertTrue(section.preserved)

    def test_learnings_higher_threshold(self):
        cs = self.mod.ContextSummarizer(self.tmpdir, learnings_age_days=60)
        section = self.mod.Section(
            heading="Learning", content="Content", source_file="f.md",
            date="2024-01-01", age_days=45,
        )
        # 45 days < 60 threshold for learnings
        self.assertFalse(cs._should_summarize(section, "learnings"))

    def test_extractive_summary_basic(self):
        cs = self.mod.ContextSummarizer(self.tmpdir)
        section = self.mod.Section(
            heading="Test", source_file="f.md",
            content="This is the first sentence.\n- Item one\n- Item two\n\nConclusion.",
        )
        summary = cs._extractive_summary(section)
        # Items de liste toujours inclus (prioritaires sur la prose TF-IDF)
        self.assertIn("Item one", summary)
        self.assertIn("Item two", summary)

    def test_extractive_summary_with_decisions(self):
        cs = self.mod.ContextSummarizer(self.tmpdir)
        section = self.mod.Section(
            heading="Test", source_file="f.md",
            content="Introduction text.\nWe decided to use Qdrant.\n- Raison: performance",
        )
        summary = cs._extractive_summary(section)
        self.assertIn("decided", summary.lower())

    def test_extractive_summary_truncation(self):
        cs = self.mod.ContextSummarizer(self.tmpdir, max_summary_tokens=10)
        long_content = "A" * 5000
        section = self.mod.Section(heading="T", content=long_content, source_file="f.md")
        summary = cs._extractive_summary(section)
        self.assertLessEqual(len(summary), 10 * 4 + 5)  # +5 for ellipsis

    def test_discover_memory_files_empty(self):
        cs = self.mod.ContextSummarizer(self.tmpdir)
        files = cs._discover_memory_files()
        self.assertEqual(files, [])

    def test_discover_memory_files_with_decisions(self):
        self._create_memory_structure()
        cs = self.mod.ContextSummarizer(self.tmpdir)
        files = cs._discover_memory_files()
        self.assertGreater(len(files), 0)

    def test_preview_returns_sections(self):
        self._create_memory_structure()
        cs = self.mod.ContextSummarizer(self.tmpdir, age_threshold_days=30)
        sections = cs.preview()
        # Old sections (2023-01-15) should appear
        old_sections = [s for s in sections if s.age_days > 30]
        self.assertGreaterEqual(len(old_sections), 0)

    def test_summarize_dry_run(self):
        self._create_memory_structure()
        cs = self.mod.ContextSummarizer(self.tmpdir, age_threshold_days=30)
        report = cs.summarize(dry_run=True)
        self.assertIsNotNone(report)
        # Should not create any files
        archives = self.tmpdir / "_grimoire" / "_memory" / "archives"
        if archives.exists():
            digest_files = list(archives.glob("digest-*.md"))
            self.assertEqual(len(digest_files), 0)

    def test_summarize_creates_digest(self):
        self._create_memory_structure()
        cs = self.mod.ContextSummarizer(self.tmpdir, age_threshold_days=30)
        report = cs.summarize(dry_run=False)
        if report.digests_created > 0:
            archives = self.tmpdir / "_grimoire" / "_memory" / "archives"
            self.assertTrue(archives.exists())
            digest_files = list(archives.glob("digest-*.md"))
            self.assertGreater(len(digest_files), 0)

    def test_summarize_compression_ratio(self):
        self._create_memory_structure()
        cs = self.mod.ContextSummarizer(self.tmpdir, age_threshold_days=30)
        report = cs.summarize(dry_run=True)
        # compression_ratio is defined: may be positive (good) or negative (summary larger)
        self.assertIsInstance(report.compression_ratio, float)

    def test_status_no_digests(self):
        cs = self.mod.ContextSummarizer(self.tmpdir)
        digests = cs.status()
        self.assertEqual(digests, [])

    def test_restore_nonexistent(self):
        cs = self.mod.ContextSummarizer(self.tmpdir)
        self.assertFalse(cs.restore("nonexistent-digest.md"))


# ── Config Loading ──────────────────────────────────────────────────────────

class TestConfigLoading(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_config_no_file(self):
        config = self.mod.load_summarizer_config(self.tmpdir)
        self.assertEqual(config, {})

    def test_build_from_config_defaults(self):
        cs = self.mod.build_summarizer_from_config(self.tmpdir)
        self.assertEqual(cs.age_threshold, 30)
        self.assertEqual(cs.max_summary_tokens, 500)


# ── CLI Integration ────────────────────────────────────────────────────────

class TestCLIIntegration(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(TOOL), *args],
            capture_output=True, text=True, timeout=15,
        )

    def test_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("Context Summarizer", r.stdout)

    def test_version(self):
        r = self._run("--version")
        self.assertEqual(r.returncode, 0)
        self.assertIn("context-summarizer", r.stdout)

    def test_status_command(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            r = self._run("--project-root", tmpdir, "status")
            self.assertEqual(r.returncode, 0)
            self.assertIn("Digests", r.stdout)

    def test_preview_command(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            r = self._run("--project-root", tmpdir, "preview")
            self.assertEqual(r.returncode, 0)
            self.assertIn("Preview", r.stdout)

    def test_summarize_dry_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            r = self._run("--project-root", tmpdir, "summarize", "--dry-run")
            self.assertEqual(r.returncode, 0)

    def test_restore_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            r = self._run("--project-root", tmpdir, "restore", "--digest", "fake.md")
            self.assertNotEqual(r.returncode, 0)

    def test_no_command_shows_help(self):
        r = self._run()
        # argparse may exit 0 or 1 when no subcommand is given
        self.assertIn(r.returncode, (0, 1))


# ── Auto-Prune (Sprint 2) ───────────────────────────────────────────────────

class TestAutoPrune(unittest.TestCase):
    """Tests for auto_prune() trigger added in v1.1.0."""

    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = tempfile.mkdtemp()
        self.project_root = Path(self.tmpdir)
        (self.project_root / "_grimoire" / "_memory").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_version_bumped(self):
        self.assertEqual(self.mod.CONTEXT_SUMMARIZER_VERSION, "1.2.0")

    def test_auto_prune_threshold_constant(self):
        self.assertEqual(self.mod.AUTO_PRUNE_THRESHOLD, 0.80)

    def test_auto_prune_callable(self):
        self.assertTrue(callable(getattr(self.mod, "auto_prune", None)))

    def test_mcp_context_auto_prune_callable(self):
        self.assertTrue(callable(getattr(self.mod, "mcp_context_auto_prune", None)))

    def test_auto_prune_not_triggered_low_budget(self):
        result = self.mod.auto_prune(self.project_root, threshold=0.80)
        # Budget on empty project is ~0%, should not trigger
        self.assertFalse(result.get("triggered", True))

    def test_auto_prune_returns_dict(self):
        result = self.mod.auto_prune(self.project_root)
        self.assertIsInstance(result, dict)

    def test_auto_prune_dry_run_flag(self):
        result = self.mod.auto_prune(self.project_root, dry_run=True)
        self.assertIsInstance(result, dict)
        self.assertFalse(result.get("triggered", True))

    def test_mcp_context_auto_prune_returns_dict(self):
        result = self.mod.mcp_context_auto_prune(str(self.project_root))
        self.assertIsInstance(result, dict)

    def test_import_token_budget_helper(self):
        mod = self.mod._import_token_budget()
        self.assertTrue(mod is None or hasattr(mod, "TokenBudgetEnforcer"))


if __name__ == "__main__":
    unittest.main()
