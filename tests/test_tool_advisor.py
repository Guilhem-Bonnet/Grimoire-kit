"""Tests for tool-advisor.py — Recommandation proactive d'outils BMAD."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "framework" / "tools"
sys.path.insert(0, str(TOOLS))

_spec = importlib.util.spec_from_file_location("tool_advisor", TOOLS / "tool-advisor.py")
ta = importlib.util.module_from_spec(_spec)
sys.modules["tool_advisor"] = ta
_spec.loader.exec_module(ta)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_project(tmp_path):
    (tmp_path / "framework" / "tools").mkdir(parents=True)
    # Create some fake tools
    for name in ["dream.py", "memory-lint.py", "preflight-check.py",
                 "synapse-dashboard.py", "self-healing.py"]:
        (tmp_path / "framework" / "tools" / name).write_text(f"# {name}\n")
    return tmp_path


@pytest.fixture
def project_with_usage(tmp_project):
    """Projet avec stats d'utilisation."""
    (tmp_project / "_bmad-output").mkdir(parents=True, exist_ok=True)
    stats = tmp_project / ta.USAGE_LOG
    stats.parent.mkdir(parents=True, exist_ok=True)
    with open(stats, "w", encoding="utf-8") as f:
        f.write(json.dumps({"tool": "dream.py", "ts": "2025-01-01"}) + "\n")
        f.write(json.dumps({"tool": "memory-lint.py", "ts": "2025-01-02"}) + "\n")
    return tmp_project


# ── Context Suggestions ─────────────────────────────────────────────────────


class TestContextSuggestions:
    def test_test_context(self):
        suggestions = ta.suggest_for_context("écrire des tests unitaires")
        tools = [s.tool for s in suggestions]
        assert "preflight-check.py" in tools

    def test_memory_context(self):
        suggestions = ta.suggest_for_context("la mémoire est stale")
        tools = [s.tool for s in suggestions]
        assert "dream.py" in tools
        assert "memory-lint.py" in tools

    def test_debug_context(self):
        suggestions = ta.suggest_for_context("j'ai une erreur dans le workflow")
        tools = [s.tool for s in suggestions]
        assert "self-healing.py" in tools

    def test_no_match(self):
        suggestions = ta.suggest_for_context("bonjour comment ça va")
        assert len(suggestions) == 0

    def test_suggestion_has_priority(self):
        suggestions = ta.suggest_for_context("tests et coverage")
        for s in suggestions:
            assert s.priority == "high"

    def test_no_duplicates(self):
        # "test debug" matches CTX-001 + CTX-004 but tools should be unique
        suggestions = ta.suggest_for_context("test debug erreur")
        tools = [s.tool for s in suggestions]
        assert len(tools) == len(set(tools))


# ── Unused Tools ─────────────────────────────────────────────────────────────


class TestUnusedTools:
    def test_all_unused(self, tmp_project):
        unused = ta.find_unused_tools(tmp_project)
        assert len(unused) == 5  # all tools are unused
        assert "dream.py" in unused

    def test_some_used(self, project_with_usage):
        unused = ta.find_unused_tools(project_with_usage)
        assert "dream.py" not in unused
        assert "memory-lint.py" not in unused
        assert "preflight-check.py" in unused

    def test_no_tools_dir(self, tmp_path):
        unused = ta.find_unused_tools(tmp_path)
        assert unused == []


# ── Discover Tools ───────────────────────────────────────────────────────────


class TestDiscoverTools:
    def test_discovers_py_files(self, tmp_project):
        tools = ta._discover_tools(tmp_project)
        assert "dream.py" in tools

    def test_ignores_underscore(self, tmp_project):
        (tmp_project / "framework" / "tools" / "_private.py").write_text("")
        tools = ta._discover_tools(tmp_project)
        assert "_private.py" not in tools

    def test_empty_dir(self, tmp_path):
        tools = ta._discover_tools(tmp_path)
        assert tools == set()


# ── Build Advice ─────────────────────────────────────────────────────────────


class TestBuildAdvice:
    def test_with_context(self, tmp_project):
        report = ta.build_advice(tmp_project, context="debug erreur")
        assert len(report.suggestions) > 0
        assert report.timestamp

    def test_without_context(self, tmp_project):
        report = ta.build_advice(tmp_project)
        # Should give default suggestions
        assert len(report.suggestions) > 0

    def test_report_to_dict(self, tmp_project):
        report = ta.build_advice(tmp_project, context="tests")
        d = report.to_dict()
        assert "suggestions" in d
        assert "unused_tools" in d
        assert "workflows" in d


# ── MCP Interface ────────────────────────────────────────────────────────────


class TestMCP:
    def test_suggest(self, tmp_project):
        result = ta.mcp_tool_advisor(str(tmp_project), action="suggest",
                                      context="debug")
        assert result["status"] == "ok"
        assert result["suggestion_count"] > 0

    def test_suggest_no_context(self, tmp_project):
        result = ta.mcp_tool_advisor(str(tmp_project), action="suggest")
        assert result["status"] == "ok"

    def test_unused(self, tmp_project):
        result = ta.mcp_tool_advisor(str(tmp_project), action="unused")
        assert result["status"] == "ok"
        assert "unused_tools" in result
        assert result["count"] > 0

    def test_workflows(self, tmp_project):
        result = ta.mcp_tool_advisor(str(tmp_project), action="workflows")
        assert result["status"] == "ok"
        assert len(result["workflows"]) > 0

    def test_unknown_action(self, tmp_project):
        result = ta.mcp_tool_advisor(str(tmp_project), action="nope")
        assert result["status"] == "error"


# ── Render ───────────────────────────────────────────────────────────────────


class TestRender:
    def test_render_suggestions(self, tmp_project):
        report = ta.build_advice(tmp_project, context="tests")
        text = ta.render_suggestions(report)
        assert "Tool Advisor" in text
        assert "recommandés" in text

    def test_render_no_suggestions(self, tmp_project):
        report = ta.AdvisorReport(timestamp="now")
        text = ta.render_suggestions(report)
        assert "Aucune suggestion" in text

    def test_render_workflows(self):
        text = ta.render_workflows()
        assert "Workflows BMAD" in text
        assert "Pre-release" in text


# ── Workflows ────────────────────────────────────────────────────────────────


class TestWorkflows:
    def test_workflows_have_steps(self):
        for wf in ta.WORKFLOWS:
            assert "name" in wf
            assert "steps" in wf
            assert len(wf["steps"]) > 0

    def test_workflows_count(self):
        assert len(ta.WORKFLOWS) >= 4


# ── CLI ──────────────────────────────────────────────────────────────────────


class TestCLI:
    def test_suggest_cli(self, capsys, tmp_project):
        ret = ta.main(["--project-root", str(tmp_project), "suggest"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "Tool Advisor" in out or "Outils" in out or "suggestion" in out.lower()

    def test_suggest_json(self, capsys, tmp_project):
        ret = ta.main(["--project-root", str(tmp_project), "--json", "suggest"])
        assert ret == 0
        data = json.loads(capsys.readouterr().out)
        assert "suggestions" in data

    def test_unused_cli(self, capsys, tmp_project):
        ret = ta.main(["--project-root", str(tmp_project), "unused"])
        assert ret == 0

    def test_workflows_cli(self, capsys, tmp_project):
        ret = ta.main(["--project-root", str(tmp_project), "workflows"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "Workflows" in out

    def test_default_command(self, capsys, tmp_project):
        ret = ta.main(["--project-root", str(tmp_project)])
        assert ret == 0


# ── Context Rules ────────────────────────────────────────────────────────────


class TestContextRules:
    def test_all_rules_have_required_fields(self):
        for rule in ta.CONTEXT_RULES:
            assert "id" in rule
            assert "pattern" in rule
            assert "tools" in rule
            assert "reason" in rule
            assert rule["id"].startswith("CTX-")

    def test_patterns_are_valid_regex(self):
        import re
        for rule in ta.CONTEXT_RULES:
            re.compile(rule["pattern"])  # Should not raise
