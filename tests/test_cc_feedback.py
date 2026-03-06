"""Tests for cc-feedback.py — CC feedback loop."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "framework" / "tools"
sys.path.insert(0, str(TOOLS))

import importlib

# Re-import fresh for each test module
if "cc_feedback" in sys.modules:
    del sys.modules["cc_feedback"]

# Module uses dashes in filename — import via spec
import importlib.util

_spec = importlib.util.spec_from_file_location("cc_feedback", TOOLS / "cc-feedback.py")
cc_feedback = importlib.util.module_from_spec(_spec)
sys.modules["cc_feedback"] = cc_feedback
_spec.loader.exec_module(cc_feedback)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_project(tmp_path):
    """Crée un projet temporaire avec .bmad-memory."""
    (tmp_path / ".bmad-memory").mkdir()
    return tmp_path


# ── Record & Load ────────────────────────────────────────────────────────────


class TestRecordLoad:
    def test_record_pass(self, tmp_project):
        rec = cc_feedback.record_cc(tmp_project, "pass", "python", details="42 tests OK")
        assert rec.record_id == "CC-001"
        assert rec.result == "pass"
        assert rec.stack == "python"

    def test_record_fail(self, tmp_project):
        rec = cc_feedback.record_cc(tmp_project, "fail", "go", details="TestAuth FAIL", root_cause="nil pointer")
        assert rec.result == "fail"
        assert rec.root_cause == "nil pointer"

    def test_load_empty(self, tmp_project):
        records = cc_feedback.load_records(tmp_project)
        assert records == []

    def test_load_after_save(self, tmp_project):
        cc_feedback.record_cc(tmp_project, "pass", "python")
        cc_feedback.record_cc(tmp_project, "fail", "go", root_cause="test")
        records = cc_feedback.load_records(tmp_project)
        assert len(records) == 2
        assert records[0].record_id == "CC-001"
        assert records[1].record_id == "CC-002"

    def test_sequential_ids(self, tmp_project):
        for i in range(5):
            cc_feedback.record_cc(tmp_project, "pass", "python")
        records = cc_feedback.load_records(tmp_project)
        assert records[-1].record_id == "CC-005"


# ── Stats ────────────────────────────────────────────────────────────────────


class TestStats:
    def test_empty_stats(self, tmp_project):
        stats = cc_feedback.compute_stats([])
        assert stats["total"] == 0
        assert stats["pass_rate"] == 0.0

    def test_stats_with_records(self, tmp_project):
        for _ in range(3):
            cc_feedback.record_cc(tmp_project, "pass", "python")
        cc_feedback.record_cc(tmp_project, "fail", "go")

        records = cc_feedback.load_records(tmp_project)
        stats = cc_feedback.compute_stats(records)
        assert stats["total"] == 4
        assert stats["pass"] == 3
        assert stats["fail"] == 1
        assert stats["pass_rate"] == 0.75

    def test_stats_by_stack(self, tmp_project):
        cc_feedback.record_cc(tmp_project, "pass", "python")
        cc_feedback.record_cc(tmp_project, "pass", "python")
        cc_feedback.record_cc(tmp_project, "fail", "go")

        records = cc_feedback.load_records(tmp_project)
        stats = cc_feedback.compute_stats(records)
        assert "python" in stats["by_stack"]
        assert stats["by_stack"]["python"]["total"] == 2


# ── Trend ────────────────────────────────────────────────────────────────────


class TestTrend:
    def test_trend_empty(self):
        trend = cc_feedback.compute_trend([])
        assert trend["trend"] == "neutral"

    def test_trend_with_records(self, tmp_project):
        for _ in range(10):
            cc_feedback.record_cc(tmp_project, "pass", "python")
        records = cc_feedback.load_records(tmp_project)
        trend = cc_feedback.compute_trend(records)
        assert trend["pass_rate"] == 1.0
        assert trend["count"] == 10


# ── MCP Interface ────────────────────────────────────────────────────────────


class TestMCP:
    def test_mcp_record(self, tmp_project):
        result = cc_feedback.mcp_cc_feedback(
            str(tmp_project), action="record",
            result="pass", stack="python", details="OK"
        )
        assert result["status"] == "ok"
        assert result["record"]["record_id"] == "CC-001"

    def test_mcp_record_missing_fields(self, tmp_project):
        result = cc_feedback.mcp_cc_feedback(str(tmp_project), action="record")
        assert result["status"] == "error"

    def test_mcp_stats(self, tmp_project):
        result = cc_feedback.mcp_cc_feedback(str(tmp_project), action="stats")
        assert result["status"] == "ok"
        assert "total" in result

    def test_mcp_history(self, tmp_project):
        result = cc_feedback.mcp_cc_feedback(str(tmp_project), action="history")
        assert result["status"] == "ok"

    def test_mcp_trend(self, tmp_project):
        result = cc_feedback.mcp_cc_feedback(str(tmp_project), action="trend")
        assert result["status"] == "ok"

    def test_mcp_unknown_action(self, tmp_project):
        result = cc_feedback.mcp_cc_feedback(str(tmp_project), action="nope")
        assert result["status"] == "error"


# ── CLI ──────────────────────────────────────────────────────────────────────


class TestCLI:
    def test_record_cli(self, capsys, tmp_project):
        ret = cc_feedback.main(["--project-root", str(tmp_project), "record",
                                "--result", "pass", "--stack", "python", "--details", "OK"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "CC-001" in out

    def test_history_cli(self, capsys, tmp_project):
        ret = cc_feedback.main(["--project-root", str(tmp_project), "history"])
        assert ret == 0

    def test_stats_cli(self, capsys, tmp_project):
        ret = cc_feedback.main(["--project-root", str(tmp_project), "stats"])
        assert ret == 0

    def test_trend_cli(self, capsys, tmp_project):
        ret = cc_feedback.main(["--project-root", str(tmp_project), "trend"])
        assert ret == 0
