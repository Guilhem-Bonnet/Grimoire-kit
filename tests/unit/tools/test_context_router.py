"""Tests for grimoire.tools.context_router — ContextRouter tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.tools.context_router import (
    DEFAULT_MODEL,
    MODEL_WINDOWS,
    ContextRouter,
    FileEntry,
    LoadPlan,
    Priority,
    calculate_plan,
    compute_relevance,
    discover_context_files,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """Grimoire project with agent files and memory."""
    (tmp_path / "project-context.yaml").write_text("project:\n  name: test\n")
    (tmp_path / "_grimoire" / "_config" / "agents").mkdir(parents=True)
    (tmp_path / "_grimoire" / "_config" / "custom").mkdir(parents=True)
    (tmp_path / "_grimoire" / "_memory" / "agent-learnings").mkdir(parents=True)

    # Agent base
    (tmp_path / "_grimoire" / "_config" / "custom" / "agent-base.md").write_text(
        "# Agent Base Protocol\nRules here.\n"
    )
    # Agent file
    (tmp_path / "_grimoire" / "_config" / "agents" / "analyst.md").write_text(
        "# Analyst\nname: analyst\nAnalysis agent.\n"
    )
    # Session context
    (tmp_path / "_grimoire" / "_memory" / "shared-context.md").write_text(
        "# Shared Context\n- Project uses Python 3.12\n"
    )
    # Decisions
    (tmp_path / "_grimoire" / "_memory" / "decisions-log.md").write_text(
        "# Decisions\n- [2024-01-01] Use Python\n"
    )
    # Agent learnings
    (tmp_path / "_grimoire" / "_memory" / "agent-learnings" / "analyst.md").write_text(
        "# Analyst Learnings\n- [2024-01-01] Analysis patterns\n"
    )
    return tmp_path


# ── FileEntry ─────────────────────────────────────────────────────────────────

class TestFileEntry:
    def test_priority_label(self) -> None:
        e = FileEntry(path="a.md", priority=Priority.P0_ALWAYS)
        assert e.priority_label == "P0-ALWAYS"

    def test_unknown_priority_label(self) -> None:
        e = FileEntry(path="a.md", priority=99)
        assert e.priority_label == "P99"

    def test_to_dict(self) -> None:
        e = FileEntry(path="test.md", priority=1, estimated_tokens=100,
                      reason="test", relevance_score=0.8, loaded=True)
        d = e.to_dict()
        assert d["path"] == "test.md"
        assert d["priority"] == "P1-SESSION"
        assert d["tokens"] == 100
        assert d["loaded"] is True
        assert d["relevance"] == 0.8


# ── LoadPlan ──────────────────────────────────────────────────────────────────

class TestLoadPlan:
    def test_usage_pct(self) -> None:
        p = LoadPlan(agent="a", model="copilot", model_window=200_000,
                     loaded_tokens=40_000)
        assert p.usage_pct == 20.0

    def test_status_ok(self) -> None:
        p = LoadPlan(agent="a", model="copilot", model_window=200_000,
                     loaded_tokens=10_000)
        assert p.status == "OK"

    def test_status_warning(self) -> None:
        p = LoadPlan(agent="a", model="copilot", model_window=100_000,
                     loaded_tokens=65_000)
        assert p.status == "WARNING"

    def test_status_critical(self) -> None:
        p = LoadPlan(agent="a", model="copilot", model_window=100_000,
                     loaded_tokens=85_000)
        assert p.status == "CRITICAL"

    def test_zero_window(self) -> None:
        p = LoadPlan(agent="a", model="x", model_window=0, loaded_tokens=100)
        assert p.usage_pct == 0

    def test_to_dict(self) -> None:
        p = LoadPlan(agent="analyst", model="copilot", model_window=200_000,
                     loaded_tokens=5_000, total_tokens=10_000)
        d = p.to_dict()
        assert d["agent"] == "analyst"
        assert d["status"] == "OK"
        assert "files" in d


# ── Priority ──────────────────────────────────────────────────────────────────

class TestPriority:
    def test_ordering(self) -> None:
        assert Priority.P0_ALWAYS < Priority.P1_SESSION < Priority.P2_TASK
        assert Priority.P2_TASK < Priority.P3_LAZY < Priority.P4_ON_REQUEST

    def test_labels(self) -> None:
        assert Priority.LABELS[0] == "P0-ALWAYS"
        assert Priority.LABELS[4] == "P4-ON_REQUEST"


# ── discover_context_files ───────────────────────────────────────────────────

class TestDiscoverContextFiles:
    def test_discovers_agent_base(self, project: Path) -> None:
        entries = discover_context_files(project, "analyst")
        paths = [e.path for e in entries]
        assert any("agent-base.md" in p for p in paths)

    def test_discovers_session_files(self, project: Path) -> None:
        entries = discover_context_files(project, "analyst")
        paths = [e.path for e in entries]
        assert any("shared-context" in p for p in paths)
        assert any("decisions-log" in p for p in paths)

    def test_discovers_agent_learnings(self, project: Path) -> None:
        entries = discover_context_files(project, "analyst")
        kinds = {e.reason for e in entries}
        assert any("learnings" in k.lower() for k in kinds)

    def test_empty_project(self, tmp_path: Path) -> None:
        (tmp_path / "_grimoire" / "_memory").mkdir(parents=True)
        entries = discover_context_files(tmp_path, "analyst")
        assert entries == []

    def test_discovers_archives(self, project: Path) -> None:
        arch = project / "_grimoire" / "_memory" / "archives"
        arch.mkdir()
        (arch / "old-decisions.md").write_text("# Archived\n")
        entries = discover_context_files(project, "analyst")
        archive_entries = [e for e in entries if e.priority == Priority.P4_ON_REQUEST]
        assert len(archive_entries) == 1


# ── compute_relevance ────────────────────────────────────────────────────────

class TestComputeRelevance:
    def test_no_query(self) -> None:
        entries = [FileEntry(path="a.md", priority=0)]
        result = compute_relevance(entries, "")
        assert result[0].relevance_score == 1.0

    def test_matching_query(self) -> None:
        entries = [
            FileEntry(path="decisions-log.md", priority=Priority.P1_SESSION, reason="Decisions"),
            FileEntry(path="archive.md", priority=Priority.P3_LAZY, reason="Old stuff"),
        ]
        result = compute_relevance(entries, "decisions")
        assert result[0].relevance_score > result[1].relevance_score

    def test_session_files_stay_relevant(self) -> None:
        entries = [
            FileEntry(path="shared.md", priority=Priority.P1_SESSION, reason="Context"),
        ]
        result = compute_relevance(entries, "terraform")
        assert result[0].relevance_score == 0.8


# ── calculate_plan ───────────────────────────────────────────────────────────

class TestCalculatePlan:
    def test_basic_plan(self, project: Path) -> None:
        plan = calculate_plan(project, "analyst")
        assert plan.agent == "analyst"
        assert plan.model == DEFAULT_MODEL
        assert plan.model_window == MODEL_WINDOWS[DEFAULT_MODEL]
        assert plan.loaded_tokens >= 0

    def test_model_selection(self, project: Path) -> None:
        plan = calculate_plan(project, "analyst", model="gpt-4o")
        assert plan.model_window == 128_000

    def test_unknown_model_fallback(self, project: Path) -> None:
        plan = calculate_plan(project, "analyst", model="unknown-llm")
        assert plan.model_window == MODEL_WINDOWS[DEFAULT_MODEL]

    def test_files_sorted_by_priority(self, project: Path) -> None:
        plan = calculate_plan(project, "analyst")
        priorities = [e.priority for e in plan.entries]
        assert priorities == sorted(priorities)

    def test_task_query(self, project: Path) -> None:
        plan = calculate_plan(project, "analyst", task_query="decisions analysis")
        assert plan.total_tokens >= 0

    def test_recommendations_empty_small_budget(self, project: Path) -> None:
        plan = calculate_plan(project, "analyst")
        # Small project → OK status → no critical recommendations
        assert plan.status == "OK"


# ── ContextRouter Tool ───────────────────────────────────────────────────────

class TestContextRouter:
    def test_run_default(self, project: Path) -> None:
        cr = ContextRouter(project)
        plan = cr.run(agent="analyst")
        assert isinstance(plan, LoadPlan)
        assert plan.agent == "analyst"

    def test_run_with_model(self, project: Path) -> None:
        cr = ContextRouter(project)
        plan = cr.run(agent="analyst", model="gpt-4o")
        assert plan.model == "gpt-4o"

    def test_run_with_task(self, project: Path) -> None:
        cr = ContextRouter(project)
        plan = cr.run(agent="analyst", task="review architecture")
        assert isinstance(plan, LoadPlan)

    def test_plan_to_dict(self, project: Path) -> None:
        cr = ContextRouter(project)
        plan = cr.run(agent="analyst")
        d = plan.to_dict()
        assert "agent" in d
        assert "status" in d
        assert "files" in d
        assert isinstance(d["files"], list)
