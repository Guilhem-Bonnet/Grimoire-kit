"""Tests for bmad.tools.context_guard — Token budget analyzer."""

from __future__ import annotations

from pathlib import Path

import pytest

from bmad.tools.context_guard import (
    DEFAULT_MODEL,
    MODEL_WINDOWS,
    AgentBudget,
    ContextGuard,
    FileLoad,
    GuardReport,
    compute_budget,
    find_agents,
    resolve_agent_loads,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    """Minimal BMAD project structure."""
    (tmp_path / "_bmad/_config/agents").mkdir(parents=True)
    (tmp_path / "_bmad/_memory").mkdir(parents=True)
    (tmp_path / "_bmad-output").mkdir()
    (tmp_path / "project-context.yaml").write_text("project: test\n")
    return tmp_path


@pytest.fixture()
def agent_file(root: Path) -> Path:
    p = root / "_bmad/_config/agents/analyst.md"
    p.write_text("# Analyst Agent\n\nSome instructions here.\n" * 10)
    return p


# ── FileLoad Model ────────────────────────────────────────────────────────────


class TestFileLoad:
    def test_to_dict(self) -> None:
        fl = FileLoad(path="src/foo.md", role="agent-definition", tokens=500)
        d = fl.to_dict()
        assert d["path"] == "src/foo.md"
        assert d["tokens"] == 500
        assert d["loaded"] is True

    def test_unloaded(self) -> None:
        fl = FileLoad(path="x.md", role="memory", tokens=0, loaded=False)
        assert fl.loaded is False


# ── AgentBudget Model ─────────────────────────────────────────────────────────


class TestAgentBudget:
    def test_total_tokens(self) -> None:
        ab = AgentBudget(agent_id="dev", model="copilot", model_window=200_000,
                         loads=[
                             FileLoad("a.md", "agent-definition", 1000),
                             FileLoad("b.md", "memory", 500),
                         ])
        assert ab.total_tokens == 1500

    def test_pct(self) -> None:
        ab = AgentBudget(agent_id="dev", model="copilot", model_window=10_000,
                         loads=[FileLoad("a.md", "agent-definition", 4000)])
        assert ab.pct == pytest.approx(40.0)

    def test_status_ok(self) -> None:
        ab = AgentBudget(agent_id="dev", model="copilot", model_window=200_000,
                         loads=[FileLoad("a.md", "x", 1000)])
        assert ab.status == "OK"

    def test_status_warning(self) -> None:
        ab = AgentBudget(agent_id="dev", model="copilot", model_window=10_000,
                         loads=[FileLoad("a.md", "x", 4500)])
        assert ab.status == "WARNING"

    def test_status_critical(self) -> None:
        ab = AgentBudget(agent_id="dev", model="copilot", model_window=10_000,
                         loads=[FileLoad("a.md", "x", 7500)])
        assert ab.status == "CRITICAL"

    def test_remaining_tokens(self) -> None:
        ab = AgentBudget(agent_id="dev", model="copilot", model_window=10_000,
                         loads=[FileLoad("a.md", "x", 3000)])
        assert ab.remaining_tokens == 7000

    def test_biggest(self) -> None:
        ab = AgentBudget(agent_id="dev", model="copilot", model_window=200_000,
                         loads=[
                             FileLoad("a.md", "x", 100),
                             FileLoad("b.md", "x", 5000),
                             FileLoad("c.md", "x", 2000),
                             FileLoad("d.md", "x", 8000),
                         ])
        top = ab.biggest(2)
        assert len(top) == 2
        assert top[0].tokens == 8000
        assert top[1].tokens == 5000

    def test_to_dict(self) -> None:
        ab = AgentBudget(agent_id="dev", model="copilot", model_window=200_000,
                         loads=[FileLoad("a.md", "x", 1000)])
        d = ab.to_dict()
        assert d["agent_id"] == "dev"
        assert d["status"] == "OK"
        assert "loads" in d

    def test_pct_zero_window(self) -> None:
        ab = AgentBudget(agent_id="x", model="?", model_window=0)
        assert ab.pct == 0

    def test_only_loaded_counted(self) -> None:
        ab = AgentBudget(agent_id="dev", model="copilot", model_window=200_000,
                         loads=[
                             FileLoad("a.md", "x", 1000, loaded=True),
                             FileLoad("b.md", "x", 9999, loaded=False),
                         ])
        assert ab.total_tokens == 1000


# ── GuardReport Model ────────────────────────────────────────────────────────


class TestGuardReport:
    def test_overbudget_count(self) -> None:
        r = GuardReport(budgets=[
            AgentBudget("a", "copilot", 10_000,
                        [FileLoad("x", "x", 8000)]),  # CRITICAL
            AgentBudget("b", "copilot", 200_000,
                        [FileLoad("x", "x", 100)]),  # OK
        ])
        assert r.overbudget_count == 1

    def test_to_dict(self) -> None:
        r = GuardReport()
        d = r.to_dict()
        assert d["agents_scanned"] == 0
        assert d["overbudget"] == 0


# ── resolve_agent_loads ───────────────────────────────────────────────────────


class TestResolveAgentLoads:
    def test_agent_definition_loaded(self, root: Path, agent_file: Path) -> None:
        loads = resolve_agent_loads(agent_file, root)
        agent_defs = [fl for fl in loads if fl.role == "agent-definition"]
        assert len(agent_defs) == 1
        assert agent_defs[0].tokens > 0

    def test_project_context_loaded(self, root: Path, agent_file: Path) -> None:
        loads = resolve_agent_loads(agent_file, root)
        projects = [fl for fl in loads if fl.role == "project"]
        assert len(projects) == 1

    def test_shared_context_loaded(self, root: Path, agent_file: Path) -> None:
        (root / "_bmad/_memory/shared-context.md").write_text("# Context\nStuff\n")
        loads = resolve_agent_loads(agent_file, root)
        mems = [fl for fl in loads if fl.role == "memory"]
        assert any("shared-context" in m.path for m in mems)

    def test_missing_agent_file(self, root: Path) -> None:
        fake = root / "_bmad/_config/agents/nonexistent.md"
        loads = resolve_agent_loads(fake, root)
        agent_defs = [fl for fl in loads if fl.role == "agent-definition"]
        assert len(agent_defs) == 0

    def test_agent_learnings_loaded(self, root: Path, agent_file: Path) -> None:
        ldir = root / "_bmad/_memory/agent-learnings"
        ldir.mkdir(parents=True, exist_ok=True)
        (ldir / "analyst.md").write_text("# Learnings\nSomething learned\n")
        loads = resolve_agent_loads(agent_file, root)
        assert any("analyst" in fl.path and fl.role == "memory" for fl in loads)

    def test_failure_museum_loaded(self, root: Path, agent_file: Path) -> None:
        (root / "_bmad/_memory/failure-museum.md").write_text("# Failures\n")
        loads = resolve_agent_loads(agent_file, root)
        assert any("failure-museum" in fl.path for fl in loads)

    def test_trace_loaded(self, root: Path, agent_file: Path) -> None:
        (root / "_bmad-output/BMAD_TRACE.md").write_text("# Trace\n" + "line\n" * 250)
        loads = resolve_agent_loads(agent_file, root)
        traces = [fl for fl in loads if fl.role == "trace"]
        assert len(traces) == 1
        assert traces[0].tokens > 0


# ── find_agents ───────────────────────────────────────────────────────────────


class TestFindAgents:
    def test_finds_agents(self, root: Path, agent_file: Path) -> None:
        agents = find_agents(root)
        assert any(a.stem == "analyst" for a in agents)

    def test_excludes_templates(self, root: Path) -> None:
        (root / "_bmad/_config/agents/tpl.base.md").write_text("template")
        (root / "_bmad/_config/agents/proposed.new.md").write_text("proposed")
        agents = find_agents(root)
        assert not any("tpl." in a.name for a in agents)
        assert not any("proposed." in a.name for a in agents)

    def test_multiple_dirs(self, root: Path) -> None:
        (root / "_bmad/bmm/agents").mkdir(parents=True)
        (root / "_bmad/bmm/agents/dev.md").write_text("# Dev")
        (root / "_bmad/core/agents").mkdir(parents=True)
        (root / "_bmad/core/agents/pm.md").write_text("# PM")
        agents = find_agents(root)
        stems = {a.stem for a in agents}
        assert "dev" in stems
        assert "pm" in stems

    def test_empty_project(self, tmp_path: Path) -> None:
        agents = find_agents(tmp_path)
        assert agents == []


# ── compute_budget ────────────────────────────────────────────────────────────


class TestComputeBudget:
    def test_basic(self, root: Path, agent_file: Path) -> None:
        budget = compute_budget(agent_file, root, "copilot")
        assert budget.agent_id == "analyst"
        assert budget.model == "copilot"
        assert budget.model_window == MODEL_WINDOWS["copilot"]
        assert budget.total_tokens > 0

    def test_unknown_model_uses_default(self, root: Path, agent_file: Path) -> None:
        budget = compute_budget(agent_file, root, "mystery-llm")
        assert budget.model_window == MODEL_WINDOWS[DEFAULT_MODEL]


# ── ContextGuard Tool ─────────────────────────────────────────────────────────


class TestContextGuardTool:
    def test_run_returns_report(self, root: Path, agent_file: Path) -> None:
        cg = ContextGuard(root)
        report = cg.run()
        assert isinstance(report, GuardReport)
        assert len(report.budgets) >= 1

    def test_run_filter_agent(self, root: Path, agent_file: Path) -> None:
        (root / "_bmad/_config/agents/dev.md").write_text("# Dev agent")
        cg = ContextGuard(root)
        report = cg.run(agent="analyst")
        assert all(b.agent_id == "analyst" for b in report.budgets)

    def test_run_with_model(self, root: Path, agent_file: Path) -> None:
        cg = ContextGuard(root)
        report = cg.run(model="llama3")
        assert report.budgets[0].model == "llama3"
        assert report.budgets[0].model_window == MODEL_WINDOWS["llama3"]

    def test_run_empty_project(self, tmp_path: Path) -> None:
        cg = ContextGuard(tmp_path)
        report = cg.run()
        assert len(report.budgets) == 0
