#!/usr/bin/env python3
"""
Tests pour memory-lint.py — BMAD Memory Lint.

Fonctions testées :
  - collect_memory_files()
  - check_contradictions()
  - check_duplicates()
  - check_orphan_decisions()
  - check_failure_without_lesson()
  - check_chronological_consistency()
  - lint_memory()
  - emit_to_stigmergy()
  - render_report()
  - report_to_dict()
"""

import importlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

KIT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(KIT_DIR / "framework" / "tools"))


def _import_mod():
    return importlib.import_module("memory-lint")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _setup_memory(root: Path, *, learnings=None, decisions=None,
                  trace=None, failures=None, contradictions=None):
    """Crée un arbre mémoire minimal."""
    mem = root / "_bmad" / "_memory"
    mem.mkdir(parents=True, exist_ok=True)

    if learnings:
        ld = mem / "agent-learnings"
        ld.mkdir(exist_ok=True)
        for name, content in learnings.items():
            (ld / name).write_text(content, encoding="utf-8")

    if decisions:
        (mem / "decisions-log.md").write_text(decisions, encoding="utf-8")

    if trace:
        out_dir = root / "_bmad-output"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "BMAD_TRACE.md").write_text(trace, encoding="utf-8")

    if failures:
        (mem / "failure-museum.md").write_text(failures, encoding="utf-8")

    if contradictions:
        (mem / "contradiction-log.md").write_text(contradictions, encoding="utf-8")


# ── Tests collect_memory_files ────────────────────────────────────────────────

class TestCollectMemoryFiles(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_empty_project(self):
        files = self.mod.collect_memory_files(self.tmpdir)
        self.assertEqual(files, [])

    def test_collects_learnings(self):
        _setup_memory(self.tmpdir, learnings={
            "dev.md": "- [2026-02-28] learned about caching\n"
        })
        files = self.mod.collect_memory_files(self.tmpdir)
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].kind, "learnings")

    def test_collects_all_types(self):
        _setup_memory(
            self.tmpdir,
            learnings={"dev.md": "- learned caching\n"},
            decisions="- [2026-02-01] chose Redis\n",
            failures="- [2026-02-01] cache invalidation bug\n",
        )
        files = self.mod.collect_memory_files(self.tmpdir)
        kinds = {f.kind for f in files}
        self.assertIn("learnings", kinds)
        self.assertIn("decisions", kinds)
        self.assertIn("failure-museum", kinds)

    def test_collects_trace(self):
        _setup_memory(self.tmpdir, trace=(
            "[2026-02-28 10:00] [DECISION] [dev] chose caching strategy\n"
        ))
        files = self.mod.collect_memory_files(self.tmpdir)
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].kind, "trace")


# ── Tests check_contradictions ────────────────────────────────────────────────

class TestCheckContradictions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_detects_contradiction(self):
        files = [
            self.mod.MemoryFile(
                path="learnings/dev.md", kind="learnings",
                entries=[("2026-02-01",
                          "caching database must always remain enabled")],
            ),
            self.mod.MemoryFile(
                path="failure-museum.md", kind="failure-museum",
                entries=[("2026-02-02",
                          "danger caching database remain enabled caused stale data")],
            ),
        ]
        issues = self.mod.check_contradictions(files)
        self.assertGreater(len(issues), 0)
        self.assertEqual(issues[0].severity, "error")
        self.assertEqual(issues[0].category, "contradiction")

    def test_no_contradiction_same_file(self):
        files = [
            self.mod.MemoryFile(
                path="learnings/dev.md", kind="learnings",
                entries=[
                    ("", "always cache data"),
                    ("", "avoid caching large objects"),
                ],
            ),
        ]
        issues = self.mod.check_contradictions(files)
        self.assertEqual(len(issues), 0)

    def test_no_contradiction_different_topics(self):
        files = [
            self.mod.MemoryFile(
                path="a.md", kind="learnings",
                entries=[("", "always validate input fields")],
            ),
            self.mod.MemoryFile(
                path="b.md", kind="decisions",
                entries=[("", "avoid using deprecated APIs")],
            ),
        ]
        issues = self.mod.check_contradictions(files)
        self.assertEqual(len(issues), 0)


# ── Tests check_duplicates ───────────────────────────────────────────────────

class TestCheckDuplicates(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_detects_duplicate(self):
        files = [
            self.mod.MemoryFile(
                path="learnings/dev.md", kind="learnings",
                entries=[("", "implemented caching layer for database queries")],
            ),
            self.mod.MemoryFile(
                path="decisions-log.md", kind="decisions",
                entries=[("", "implemented caching layer for database queries")],
            ),
        ]
        issues = self.mod.check_duplicates(files)
        self.assertGreater(len(issues), 0)
        self.assertEqual(issues[0].category, "duplicate")

    def test_no_duplicate_different_text(self):
        files = [
            self.mod.MemoryFile(
                path="a.md", kind="learnings",
                entries=[("", "caching is great")],
            ),
            self.mod.MemoryFile(
                path="b.md", kind="decisions",
                entries=[("", "authentication system redesigned")],
            ),
        ]
        issues = self.mod.check_duplicates(files)
        self.assertEqual(len(issues), 0)


# ── Tests check_orphan_decisions ──────────────────────────────────────────────

class TestCheckOrphanDecisions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_detects_orphan(self):
        files = [
            self.mod.MemoryFile(
                path="BMAD_TRACE.md", kind="trace",
                entries=[("2026-02-28",
                          "[dev] [DECISION] switch to new caching strategy")],
            ),
            self.mod.MemoryFile(
                path="decisions-log.md", kind="decisions",
                entries=[("2026-02-01", "unrelated decision about logging")],
            ),
        ]
        issues = self.mod.check_orphan_decisions(files)
        self.assertGreater(len(issues), 0)
        self.assertEqual(issues[0].category, "orphan")

    def test_no_orphan_when_matched(self):
        files = [
            self.mod.MemoryFile(
                path="BMAD_TRACE.md", kind="trace",
                entries=[("2026-02-28",
                          "[dev] [DECISION] chose caching strategy Redis")],
            ),
            self.mod.MemoryFile(
                path="decisions-log.md", kind="decisions",
                entries=[("2026-02-28",
                          "chose caching Redis strategy for sessions")],
            ),
        ]
        issues = self.mod.check_orphan_decisions(files)
        self.assertEqual(len(issues), 0)

    def test_no_trace_returns_empty(self):
        files = [
            self.mod.MemoryFile(
                path="decisions-log.md", kind="decisions",
                entries=[("2026-02-01", "some decision")],
            ),
        ]
        issues = self.mod.check_orphan_decisions(files)
        self.assertEqual(len(issues), 0)


# ── Tests check_failure_without_lesson ────────────────────────────────────────

class TestCheckFailureWithoutLesson(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_detects_uncapitalized_failure(self):
        files = [
            self.mod.MemoryFile(
                path="failure-museum.md", kind="failure-museum",
                entries=[("2026-02-01",
                          "cache invalidation bug caused production outage")],
            ),
            self.mod.MemoryFile(
                path="learnings/dev.md", kind="learnings",
                entries=[("2026-02-01", "learned about python typing")],
            ),
        ]
        issues = self.mod.check_failure_without_lesson(files)
        self.assertGreater(len(issues), 0)

    def test_no_issue_when_lesson_exists(self):
        files = [
            self.mod.MemoryFile(
                path="failure-museum.md", kind="failure-museum",
                entries=[("2026-02-01",
                          "cache invalidation bug production outage")],
            ),
            self.mod.MemoryFile(
                path="learnings/dev.md", kind="learnings",
                entries=[("2026-02-02",
                          "cache invalidation production needs TTL strategy")],
            ),
        ]
        issues = self.mod.check_failure_without_lesson(files)
        self.assertEqual(len(issues), 0)


# ── Tests check_chronological_consistency ─────────────────────────────────────

class TestCheckChronological(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_ordered_dates_no_issue(self):
        files = [
            self.mod.MemoryFile(
                path="a.md", kind="learnings",
                entries=[
                    ("2026-02-01", "first"),
                    ("2026-02-02", "second"),
                    ("2026-02-03", "third"),
                    ("2026-02-04", "fourth"),
                ],
            ),
        ]
        issues = self.mod.check_chronological_consistency(files)
        self.assertEqual(len(issues), 0)

    def test_scrambled_dates_detected(self):
        files = [
            self.mod.MemoryFile(
                path="a.md", kind="learnings",
                entries=[
                    ("2026-02-05", "e"),
                    ("2026-02-01", "a"),
                    ("2026-02-04", "d"),
                    ("2026-02-02", "b"),
                    ("2026-02-03", "c"),
                ],
            ),
        ]
        issues = self.mod.check_chronological_consistency(files)
        # Scrambled dates should trigger a warning
        self.assertGreater(len(issues), 0)

    def test_too_few_entries_no_issue(self):
        files = [
            self.mod.MemoryFile(
                path="a.md", kind="learnings",
                entries=[("2026-02-01", "only one")],
            ),
        ]
        issues = self.mod.check_chronological_consistency(files)
        self.assertEqual(len(issues), 0)


# ── Tests lint_memory orchestration ───────────────────────────────────────────

class TestLintMemory(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_empty_project_clean(self):
        report = self.mod.lint_memory(self.tmpdir)
        self.assertEqual(len(report.issues), 0)
        self.assertEqual(report.files_scanned, 0)

    def test_clean_memory_no_issues(self):
        _setup_memory(self.tmpdir, learnings={
            "dev.md": "- [2026-02-01] learned caching\n"
        })
        report = self.mod.lint_memory(self.tmpdir)
        self.assertEqual(report.error_count, 0)

    def test_report_has_counts(self):
        report = self.mod.lint_memory(self.tmpdir)
        self.assertIsInstance(report.error_count, int)
        self.assertIsInstance(report.warning_count, int)
        self.assertIsInstance(report.info_count, int)


# ── Tests render_report ──────────────────────────────────────────────────────

class TestRenderReport(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_clean_report(self):
        report = self.mod.LintReport(files_scanned=3, entries_scanned=10)
        text = self.mod.render_report(report)
        self.assertIn("Aucun problème", text)

    def test_report_with_issues(self):
        report = self.mod.LintReport(files_scanned=2, entries_scanned=5)
        report.issues.append(self.mod.LintIssue(
            issue_id="ML-001", severity="error",
            category="contradiction", title="Test issue",
            description="Test description", files=["a.md"],
        ))
        text = self.mod.render_report(report)
        self.assertIn("ML-001", text)
        self.assertIn("Test issue", text)

    def test_fix_suggestions_shown(self):
        report = self.mod.LintReport()
        report.issues.append(self.mod.LintIssue(
            issue_id="ML-001", severity="warning",
            category="duplicate", title="T",
            description="D", files=["a.md"],
            fix_suggestion="Do this fix",
        ))
        text = self.mod.render_report(report, show_fix=True)
        self.assertIn("Do this fix", text)


# ── Tests report_to_dict ─────────────────────────────────────────────────────

class TestReportToDict(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_dict_structure(self):
        report = self.mod.LintReport(files_scanned=2, entries_scanned=10)
        d = self.mod.report_to_dict(report)
        self.assertIn("version", d)
        self.assertIn("summary", d)
        self.assertIn("issues", d)
        self.assertEqual(d["summary"]["total"], 0)

    def test_issues_serialized(self):
        report = self.mod.LintReport()
        report.issues.append(self.mod.LintIssue(
            issue_id="ML-001", severity="error",
            category="contradiction", title="T",
            description="D", files=["a.md"],
        ))
        d = self.mod.report_to_dict(report)
        self.assertEqual(len(d["issues"]), 1)
        self.assertEqual(d["issues"][0]["issue_id"], "ML-001")


# ── Tests data classes ────────────────────────────────────────────────────────

class TestDataClasses(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_lint_issue_defaults(self):
        issue = self.mod.LintIssue(
            issue_id="ML-001", severity="error", category="contradiction",
            title="T", description="D", files=["a.md"],
        )
        self.assertEqual(issue.entries, [])
        self.assertEqual(issue.fix_suggestion, "")

    def test_memory_file_defaults(self):
        mf = self.mod.MemoryFile(path="a.md", kind="learnings")
        self.assertEqual(mf.entries, [])

    def test_report_counts(self):
        report = self.mod.LintReport()
        report.issues = [
            self.mod.LintIssue("1", "error", "c", "T", "D", []),
            self.mod.LintIssue("2", "warning", "d", "T", "D", []),
            self.mod.LintIssue("3", "info", "e", "T", "D", []),
            self.mod.LintIssue("4", "error", "c", "T", "D", []),
        ]
        self.assertEqual(report.error_count, 2)
        self.assertEqual(report.warning_count, 1)
        self.assertEqual(report.info_count, 1)


# ── Tests similarity and keywords ─────────────────────────────────────────────

class TestKeywordsAndSimilarity(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_extract_keywords_filters_stopwords(self):
        kws = self.mod._extract_keywords("the quick brown fox")
        self.assertNotIn("the", kws)
        self.assertIn("quick", kws)

    def test_similarity_identical(self):
        sim = self.mod._similarity("caching layer", "caching layer")
        self.assertEqual(sim, 1.0)

    def test_similarity_different(self):
        sim = self.mod._similarity("caching database layer", "authentication user login")
        self.assertLess(sim, 0.2)

    def test_has_polarity_positive(self):
        pos, neg = self.mod._has_polarity("must always validate")
        self.assertTrue(pos)
        self.assertFalse(neg)

    def test_has_polarity_negative(self):
        pos, neg = self.mod._has_polarity("avoid danger risk")
        self.assertFalse(pos)
        self.assertTrue(neg)


class TestCheckMemoryFreshness(unittest.TestCase):
    """Tests for memory decay / freshness scoring (QW5)."""

    def setUp(self):
        self.mod = _import_mod()

    def test_function_exists(self):
        self.assertTrue(callable(self.mod.check_memory_freshness))

    def test_no_dated_entries_no_issue(self):
        files = [self.mod.MemoryFile(
            path="learnings/dev.md", kind="learnings",
            entries=[("", "no date entry")],
        )]
        issues = self.mod.check_memory_freshness(files)
        self.assertEqual(len(issues), 0)

    def test_recent_entries_no_issue(self):
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        files = [self.mod.MemoryFile(
            path="learnings/dev.md", kind="learnings",
            entries=[(today, "fresh entry 1"), (today, "fresh entry 2")],
        )]
        issues = self.mod.check_memory_freshness(files)
        self.assertEqual(len(issues), 0)

    def test_old_entries_flagged(self):
        files = [self.mod.MemoryFile(
            path="learnings/dev.md", kind="learnings",
            entries=[("2020-01-01", "ancient entry 1"), ("2020-02-01", "ancient entry 2")],
        )]
        issues = self.mod.check_memory_freshness(files)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].category, "staleness")
        self.assertIn("périmé", issues[0].title)

    def test_staleness_in_lint_report(self):
        """Verify check_memory_freshness is called by lint_memory."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mem = root / "_bmad" / "_memory" / "agent-learnings"
            mem.mkdir(parents=True, exist_ok=True)
            (mem / "dev.md").write_text(
                "# Dev\n- [2020-01-01] ancient\n- [2020-02-01] old\n",
                encoding="utf-8",
            )
            report = self.mod.lint_memory(root)
            stale = [i for i in report.issues if i.category == "staleness"]
            self.assertGreaterEqual(len(stale), 1)

    def test_constants_defined(self):
        self.assertEqual(self.mod.STALENESS_DAYS, 90)
        self.assertEqual(self.mod.DECAY_HALFLIFE_DAYS, 30)


if __name__ == "__main__":
    unittest.main()
