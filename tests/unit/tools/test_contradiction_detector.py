"""Tests for grimoire.tools.contradiction_detector — BM-31 V4.4.c."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grimoire.tools.contradiction_detector import (
    DEFAULT_CONTRADICTION_LOG_PATH,
    DEFAULT_DECISIONS_PATH,
    Contradiction,
    DecisionEntry,
    DetectionReport,
    append_to_contradiction_log,
    detect_contradictions,
    format_markdown,
    main,
    parse_decisions,
    scan,
)


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "_grimoire" / "_memory").mkdir(parents=True)
    return tmp_path


def write_decisions(project: Path, body: str) -> Path:
    path = project / DEFAULT_DECISIONS_PATH
    path.write_text(body, encoding="utf-8")
    return path


# ── DecisionEntry ────────────────────────────────────────────────────────────

class TestDecisionEntry:
    def test_is_revision_detects_markers(self) -> None:
        for marker in ("[REVISED]", "[SUPERSEDES BM-99]", "[ADR-042]", "revised by ADR-007"):
            entry = DecisionEntry(line_index=0, date="", text=f"foo {marker} bar")
            assert entry.is_revision(), marker

    def test_is_revision_false_when_no_marker(self) -> None:
        entry = DecisionEntry(line_index=0, date="", text="adopté Postgres")
        assert not entry.is_revision()


# ── parse_decisions ──────────────────────────────────────────────────────────

class TestParseDecisions:
    def test_returns_empty_list_when_file_missing(self, tmp_path: Path) -> None:
        assert parse_decisions(tmp_path / "missing.md") == []

    def test_parses_bullet_entries_with_dates(self, project: Path) -> None:
        body = (
            "# Decisions log\n"
            "\n"
            "- [2026-04-21] Adopté: Postgres\n"
            "- [2026-04-22] Abandonné: Postgres au profit de SQLite\n"
            "ignored line without bullet\n"
        )
        path = write_decisions(project, body)
        entries = parse_decisions(path)
        assert len(entries) == 2
        assert entries[0].date == "2026-04-21"
        assert "Adopté" in entries[0].text
        assert entries[1].line_index == 1


# ── detect_contradictions ────────────────────────────────────────────────────

class TestDetectContradictions:
    def test_no_polarity_no_contradictions(self) -> None:
        entries = [
            DecisionEntry(0, "", "Use Postgres"),
            DecisionEntry(1, "", "Use SQLite"),
        ]
        assert detect_contradictions(entries) == []

    def test_detects_positive_negative_pair_on_same_subject(self) -> None:
        entries = [
            DecisionEntry(0, "2026-04-21", "Adopté Postgres pour la persistance"),
            DecisionEntry(1, "2026-04-22", "Rejeté Postgres pour la persistance"),
        ]
        contradictions = detect_contradictions(entries)
        assert len(contradictions) == 1
        c = contradictions[0]
        assert c.positive.line_index == 0
        assert c.negative.line_index == 1
        assert c.similarity >= 0.30
        assert not c.revised
        assert c.contradiction_id == "BM31-001"

    def test_skips_pair_on_different_subjects(self) -> None:
        entries = [
            DecisionEntry(0, "", "Adopté Postgres"),
            DecisionEntry(1, "", "Rejeté Tailwind"),
        ]
        # Both polarised but no shared keywords → similarity below threshold.
        assert detect_contradictions(entries) == []

    def test_flags_revised_when_later_entry_has_marker(self) -> None:
        entries = [
            DecisionEntry(0, "", "Adopté Postgres pour la persistance"),
            DecisionEntry(1, "", "Rejeté Postgres pour la persistance [ADR-042 superseding]"),
        ]
        contradictions = detect_contradictions(entries)
        assert len(contradictions) == 1
        assert contradictions[0].revised is True

    def test_does_not_flag_revised_when_only_older_has_marker(self) -> None:
        entries = [
            DecisionEntry(0, "", "Adopté Postgres pour la persistance [REVISED]"),
            DecisionEntry(1, "", "Rejeté Postgres pour la persistance"),
        ]
        contradictions = detect_contradictions(entries)
        assert len(contradictions) == 1
        assert contradictions[0].revised is False

    def test_threshold_override(self) -> None:
        entries = [
            DecisionEntry(0, "", "Adopté Postgres"),
            DecisionEntry(1, "", "Rejeté Postgres"),
        ]
        # Very high threshold should drop the pair.
        assert detect_contradictions(entries, threshold=0.99) == []


# ── DetectionReport ──────────────────────────────────────────────────────────

class TestDetectionReport:
    def test_unresolved_filters_revised(self) -> None:
        c1 = Contradiction(
            "BM31-001", 0.5,
            DecisionEntry(0, "", "a"), DecisionEntry(1, "", "b"),
            revised=False,
        )
        c2 = Contradiction(
            "BM31-002", 0.5,
            DecisionEntry(2, "", "c"), DecisionEntry(3, "", "d"),
            revised=True,
        )
        report = DetectionReport("decisions-log.md", 4, (c1, c2))
        assert report.unresolved == (c1,)

    def test_to_dict_shape(self) -> None:
        report = DetectionReport("decisions-log.md", 0, ())
        d = report.to_dict()
        assert d["source_path"] == "decisions-log.md"
        assert d["entry_count"] == 0
        assert d["contradiction_count"] == 0
        assert d["unresolved_count"] == 0


# ── scan ─────────────────────────────────────────────────────────────────────

class TestScan:
    def test_scan_returns_empty_report_when_log_missing(self, project: Path) -> None:
        # No decisions-log.md created.
        report = scan(project)
        assert report.entry_count == 0
        assert report.contradictions == ()

    def test_scan_detects_contradictions_end_to_end(self, project: Path) -> None:
        write_decisions(
            project,
            (
                "- [2026-04-21] Adopté Postgres pour la persistance\n"
                "- [2026-04-22] Rejeté Postgres pour la persistance\n"
            ),
        )
        report = scan(project)
        assert report.entry_count == 2
        assert len(report.contradictions) == 1
        assert report.unresolved == report.contradictions

    def test_scan_honours_custom_decisions_path(self, project: Path) -> None:
        custom = project / "alt" / "decisions.md"
        custom.parent.mkdir(parents=True)
        custom.write_text(
            "- Adopté Postgres pour la persistance\n"
            "- Rejeté Postgres pour la persistance\n",
            encoding="utf-8",
        )
        report = scan(project, decisions_path=Path("alt/decisions.md"))
        assert report.entry_count == 2
        assert len(report.contradictions) == 1


# ── format_markdown ──────────────────────────────────────────────────────────

class TestFormatMarkdown:
    def test_empty_report(self) -> None:
        out = format_markdown(DetectionReport("decisions-log.md", 0, ()))
        assert "_No contradictions detected._" in out
        assert "Entries scanned: 0" in out

    def test_renders_resolved_and_unresolved(self) -> None:
        c = Contradiction(
            "BM31-001", 0.42,
            DecisionEntry(0, "2026-04-21", "Adopté Postgres"),
            DecisionEntry(1, "2026-04-22", "Rejeté Postgres"),
            revised=False,
        )
        out = format_markdown(DetectionReport("decisions-log.md", 2, (c,)))
        assert "BM31-001" in out
        assert "UNRESOLVED" in out
        assert "Adopté Postgres" in out


# ── append_to_contradiction_log ──────────────────────────────────────────────

class TestAppendLog:
    def test_returns_none_when_no_unresolved(self, project: Path) -> None:
        report = DetectionReport("decisions-log.md", 0, ())
        assert append_to_contradiction_log(project, report) is None

    def test_appends_unresolved_findings(self, project: Path) -> None:
        c = Contradiction(
            "BM31-001", 0.5,
            DecisionEntry(0, "", "Adopté Postgres"),
            DecisionEntry(1, "", "Rejeté Postgres"),
            revised=False,
        )
        report = DetectionReport("decisions-log.md", 2, (c,))
        target = append_to_contradiction_log(project, report)
        assert target is not None
        assert target == project / DEFAULT_CONTRADICTION_LOG_PATH
        body = target.read_text(encoding="utf-8")
        assert "BM31-001" in body
        assert "Adopté Postgres" in body

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        c = Contradiction(
            "BM31-001", 0.5,
            DecisionEntry(0, "", "Adopté X"),
            DecisionEntry(1, "", "Rejeté X"),
            revised=False,
        )
        report = DetectionReport("decisions-log.md", 2, (c,))
        target = append_to_contradiction_log(tmp_path, report)
        assert target is not None
        assert target.exists()


# ── CLI main ─────────────────────────────────────────────────────────────────

class TestCLI:
    def test_main_returns_0_when_no_contradictions(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        write_decisions(project, "- Adopté Postgres\n")
        rc = main(["--project-root", str(project), "--format", "json"])
        assert rc == 0
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["unresolved_count"] == 0

    def test_main_returns_1_when_unresolved_contradictions(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        write_decisions(
            project,
            "- Adopté Postgres pour la persistance\n"
            "- Rejeté Postgres pour la persistance\n",
        )
        rc = main(["--project-root", str(project), "--format", "json"])
        assert rc == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["unresolved_count"] == 1

    def test_main_markdown_format(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        write_decisions(project, "- Adopté Postgres\n")
        rc = main(["--project-root", str(project), "--format", "markdown"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Contradiction scan" in out

    def test_main_write_log_flag_creates_log(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        write_decisions(
            project,
            "- Adopté Postgres pour la persistance\n"
            "- Rejeté Postgres pour la persistance\n",
        )
        rc = main(
            ["--project-root", str(project), "--format", "json", "--write-log"]
        )
        assert rc == 1
        log = project / DEFAULT_CONTRADICTION_LOG_PATH
        assert log.exists()
        assert "BM31-001" in log.read_text(encoding="utf-8")
