"""Tests for bmad.tools.memory_lint — MemoryLint tool."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from bmad.tools.memory_lint import (
    LintIssue,
    LintReport,
    MemoryFile,
    MemoryLint,
    _extract_keywords,
    _has_polarity,
    _parse_jsonl,
    _parse_markdown,
    _parse_trace,
    check_chronological,
    check_contradictions,
    check_duplicates,
    check_freshness,
    check_orphan_decisions,
    collect_memory_files,
    similarity,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "project-context.yaml").write_text("project:\n  name: test\n")
    (tmp_path / "_bmad" / "_memory" / "agent-learnings").mkdir(parents=True)
    (tmp_path / "_bmad-output").mkdir(parents=True)
    return tmp_path


# ── LintIssue ─────────────────────────────────────────────────────────────────

class TestLintIssue:
    def test_to_dict(self) -> None:
        i = LintIssue(
            issue_id="ML-001", severity="error", category="contradiction",
            title="t", description="d", files=("a.md",), entries=("e1",),
            fix_suggestion="fix",
        )
        d = i.to_dict()
        assert d["issue_id"] == "ML-001"
        assert d["files"] == ["a.md"]
        assert d["entries"] == ["e1"]

    def test_frozen(self) -> None:
        i = LintIssue(issue_id="x", severity="info", category="c", title="t", description="d")
        with pytest.raises(AttributeError):
            i.severity = "error"  # type: ignore[misc]


# ── MemoryFile ────────────────────────────────────────────────────────────────

class TestMemoryFile:
    def test_frozen(self) -> None:
        mf = MemoryFile(path="a.md", kind="decisions", entries=(("2024-01-01", "text"),))
        with pytest.raises(AttributeError):
            mf.kind = "x"  # type: ignore[misc]


# ── LintReport ────────────────────────────────────────────────────────────────

class TestLintReport:
    def test_counts(self) -> None:
        r = LintReport(issues=[
            LintIssue(issue_id="1", severity="error", category="c", title="t", description="d"),
            LintIssue(issue_id="2", severity="warning", category="c", title="t", description="d"),
            LintIssue(issue_id="3", severity="info", category="c", title="t", description="d"),
            LintIssue(issue_id="4", severity="warning", category="c", title="t", description="d"),
        ])
        assert r.error_count == 1
        assert r.warning_count == 2
        assert r.info_count == 1

    def test_to_dict(self) -> None:
        r = LintReport(files_scanned=3, entries_scanned=10)
        d = r.to_dict()
        assert d["files_scanned"] == 3
        assert d["summary"]["total"] == 0


# ── NLP Helpers ───────────────────────────────────────────────────────────────

class TestNlpHelpers:
    def test_extract_keywords(self) -> None:
        kw = _extract_keywords("the quick brown fox jumps")
        assert "quick" in kw
        assert "brown" in kw
        assert "the" not in kw  # stopword

    def test_similarity_identical(self) -> None:
        assert similarity("adopted validation pattern", "adopted validation pattern") == 1.0

    def test_similarity_different(self) -> None:
        assert similarity("adopted pattern", "rejected approach") < 0.5

    def test_similarity_empty(self) -> None:
        assert similarity("", "hello") == 0.0

    def test_polarity_positive(self) -> None:
        pos, neg = _has_polarity("Feature validated and adopted")
        assert pos is True
        assert neg is False

    def test_polarity_negative(self) -> None:
        pos, neg = _has_polarity("Approach rejected as obsolete")
        assert pos is False
        assert neg is True

    def test_polarity_mixed(self) -> None:
        pos, neg = _has_polarity("adopted then rejected later")
        assert pos is True
        assert neg is True


# ── Parsing ───────────────────────────────────────────────────────────────────

class TestParsing:
    def test_parse_markdown(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text("# Title\n- [2024-01-15] Entry one\n- [2024-02-01] Entry two\n")
        entries = _parse_markdown(f)
        assert len(entries) == 2
        assert entries[0] == ("2024-01-15", "[2024-01-15] Entry one")
        assert entries[1][0] == "2024-02-01"

    def test_parse_markdown_missing(self, tmp_path: Path) -> None:
        entries = _parse_markdown(tmp_path / "nonexistent.md")
        assert entries == []

    def test_parse_trace(self, tmp_path: Path) -> None:
        f = tmp_path / "trace.md"
        f.write_text("[2024-03-01 10:00] [INFO] [analyst] Started analysis\n")
        entries = _parse_trace(f)
        assert len(entries) == 1
        assert entries[0][0] == "2024-03-01"
        assert "[analyst]" in entries[0][1]

    def test_parse_trace_missing(self, tmp_path: Path) -> None:
        assert _parse_trace(tmp_path / "nope.md") == []

    def test_parse_jsonl(self, tmp_path: Path) -> None:
        f = tmp_path / "data.jsonl"
        f.write_text('{"timestamp":"2024-01-01T00:00:00","title":"Test entry"}\n')
        entries = _parse_jsonl(f)
        assert len(entries) == 1
        assert entries[0] == ("2024-01-01", "Test entry")

    def test_parse_jsonl_invalid(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.jsonl"
        f.write_text("not json\n")
        entries = _parse_jsonl(f)
        assert entries == []


# ── collect_memory_files ──────────────────────────────────────────────────────

class TestCollectMemoryFiles:
    def test_empty_project(self, project: Path) -> None:
        files = collect_memory_files(project)
        assert files == []

    def test_finds_learnings(self, project: Path) -> None:
        learn = project / "_bmad" / "_memory" / "agent-learnings" / "dev.md"
        learn.write_text("# Dev Learnings\n- [2024-06-01] Learned TDD\n")
        files = collect_memory_files(project)
        assert len(files) == 1
        assert files[0].kind == "learnings"

    def test_finds_decisions(self, project: Path) -> None:
        dec = project / "_bmad" / "_memory" / "decisions-log.md"
        dec.write_text("# Decisions\n- [2024-01-01] Use Python 3.12\n")
        files = collect_memory_files(project)
        assert len(files) == 1
        assert files[0].kind == "decisions"

    def test_finds_trace(self, project: Path) -> None:
        trace = project / "_bmad-output" / "BMAD_TRACE.md"
        trace.write_text("[2024-03-01 10:00] [INFO] [analyst] Hello\n")
        files = collect_memory_files(project)
        assert len(files) == 1
        assert files[0].kind == "trace"

    def test_finds_jsonl(self, project: Path) -> None:
        cc = project / "_bmad" / "_memory" / "cc-feedback.jsonl"
        cc.write_text('{"timestamp":"2024-01-01","title":"Feedback"}\n')
        files = collect_memory_files(project)
        assert len(files) == 1
        assert files[0].kind == "cc-feedback"


# ── check_contradictions ─────────────────────────────────────────────────────

class TestCheckContradictions:
    def test_no_contradictions(self) -> None:
        files = [
            MemoryFile(path="a.md", kind="decisions",
                       entries=(("2024-01-01", "Approach adopted successfully"),)),
            MemoryFile(path="b.md", kind="learnings",
                       entries=(("2024-01-01", "Framework installed correctly"),)),
        ]
        issues = check_contradictions(files)
        assert len(issues) == 0

    def test_detects_contradiction(self) -> None:
        files = [
            MemoryFile(path="a.md", kind="decisions",
                       entries=(("2024-01-01", "TDD approach adopted and validated for the project"),)),
            MemoryFile(path="b.md", kind="learnings",
                       entries=(("2024-01-02", "TDD approach rejected and abandoned for the project"),)),
        ]
        issues = check_contradictions(files)
        assert len(issues) >= 1
        assert issues[0].category == "contradiction"
        assert issues[0].severity == "error"

    def test_same_file_ignored(self) -> None:
        files = [
            MemoryFile(path="a.md", kind="decisions", entries=(
                ("2024-01-01", "Feature adopted and validated"),
                ("2024-01-02", "Feature rejected and abandoned"),
            )),
        ]
        issues = check_contradictions(files)
        assert len(issues) == 0


# ── check_duplicates ─────────────────────────────────────────────────────────

class TestCheckDuplicates:
    def test_no_duplicates(self) -> None:
        files = [
            MemoryFile(path="a.md", kind="decisions",
                       entries=(("2024-01-01", "Use Python for backend"),)),
            MemoryFile(path="b.md", kind="learnings",
                       entries=(("2024-01-01", "Terraform drift detection important"),)),
        ]
        issues = check_duplicates(files)
        assert len(issues) == 0

    def test_detects_duplicate(self) -> None:
        text = "Adopted ruamel.yaml as primary YAML parser for the project config"
        files = [
            MemoryFile(path="a.md", kind="decisions",
                       entries=(("2024-01-01", text),)),
            MemoryFile(path="b.md", kind="learnings",
                       entries=(("2024-01-02", text),)),
        ]
        issues = check_duplicates(files)
        assert len(issues) == 1
        assert issues[0].category == "duplicate"
        assert issues[0].severity == "warning"


# ── check_orphan_decisions ───────────────────────────────────────────────────

class TestCheckOrphanDecisions:
    def test_no_trace(self) -> None:
        files = [MemoryFile(path="d.md", kind="decisions", entries=(("", "x"),))]
        assert check_orphan_decisions(files) == []

    def test_no_orphan(self) -> None:
        files = [
            MemoryFile(path="trace", kind="trace", entries=(
                ("2024-01-01", "[analyst] [DECISION] Use Python 3.12 for project"),
            )),
            MemoryFile(path="dec", kind="decisions", entries=(
                ("2024-01-01", "Use Python 3.12 for project development"),
            )),
        ]
        issues = check_orphan_decisions(files)
        assert len(issues) == 0

    def test_detects_orphan(self) -> None:
        files = [
            MemoryFile(path="trace", kind="trace", entries=(
                ("2024-01-01", "[analyst] [DECISION] Switch to Rust backend entirely"),
            )),
            MemoryFile(path="dec", kind="decisions", entries=(
                ("2024-01-01", "Use Python for backend"),
            )),
        ]
        issues = check_orphan_decisions(files)
        assert len(issues) == 1
        assert issues[0].category == "orphan"


# ── check_chronological ─────────────────────────────────────────────────────

class TestCheckChronological:
    def test_ordered_asc(self) -> None:
        files = [MemoryFile(path="a.md", kind="decisions", entries=(
            ("2024-01-01", "a"), ("2024-02-01", "b"), ("2024-03-01", "c"), ("2024-04-01", "d"),
        ))]
        assert check_chronological(files) == []

    def test_disordered(self) -> None:
        files = [MemoryFile(path="a.md", kind="decisions", entries=(
            ("2024-03-01", "c"), ("2024-01-01", "a"), ("2024-04-01", "d"), ("2024-02-01", "b"),
        ))]
        issues = check_chronological(files)
        assert len(issues) == 1
        assert issues[0].category == "chrono"

    def test_too_few_entries(self) -> None:
        files = [MemoryFile(path="a.md", kind="decisions", entries=(
            ("2024-01-01", "a"), ("2024-03-01", "c"),
        ))]
        assert check_chronological(files) == []


# ── check_freshness ──────────────────────────────────────────────────────────

class TestCheckFreshness:
    def test_fresh_entries(self) -> None:
        now = datetime(2024, 6, 1)
        files = [MemoryFile(path="a.md", kind="decisions", entries=(
            ("2024-05-15", "recent entry"),
            ("2024-05-20", "another recent"),
        ))]
        issues = check_freshness(files, now=now)
        assert len(issues) == 0

    def test_stale_entries(self) -> None:
        now = datetime(2024, 12, 1)
        files = [MemoryFile(path="a.md", kind="decisions", entries=(
            ("2024-01-01", "old entry one"),
            ("2024-01-15", "old entry two"),
            ("2024-02-01", "old entry three"),
        ))]
        issues = check_freshness(files, now=now)
        assert len(issues) == 1
        assert issues[0].category == "staleness"

    def test_no_dated_entries(self) -> None:
        files = [MemoryFile(path="a.md", kind="decisions", entries=(
            ("", "undated one"),
            ("", "undated two"),
        ))]
        assert check_freshness(files) == []


# ── MemoryLint Tool ──────────────────────────────────────────────────────────

class TestMemoryLint:
    def test_empty_project(self, project: Path) -> None:
        ml = MemoryLint(project)
        report = ml.run()
        assert isinstance(report, LintReport)
        assert report.files_scanned == 0
        assert len(report.issues) == 0

    def test_with_learnings(self, project: Path) -> None:
        learn = project / "_bmad" / "_memory" / "agent-learnings" / "dev.md"
        learn.write_text("# Dev\n- [2024-06-01] Learned TDD patterns\n")
        ml = MemoryLint(project)
        report = ml.run()
        assert report.files_scanned == 1
        assert report.entries_scanned == 1

    def test_issues_sorted(self, project: Path) -> None:
        # Create files that produce both errors and warnings
        learn = project / "_bmad" / "_memory" / "agent-learnings" / "dev.md"
        learn.write_text("# Dev\n- [2024-01-01] TDD approach adopted and validated for backend\n")
        dec = project / "_bmad" / "_memory" / "decisions-log.md"
        dec.write_text("# Dec\n- [2024-01-02] TDD approach rejected and abandoned for backend\n")
        ml = MemoryLint(project)
        report = ml.run()
        if len(report.issues) >= 2:
            severity_order = {"error": 0, "warning": 1, "info": 2}
            for i in range(1, len(report.issues)):
                assert severity_order.get(report.issues[i].severity, 9) >= \
                       severity_order.get(report.issues[i - 1].severity, 9)

    def test_report_to_dict(self, project: Path) -> None:
        ml = MemoryLint(project)
        report = ml.run()
        d = report.to_dict()
        assert "files_scanned" in d
        assert "summary" in d
        assert "issues" in d
