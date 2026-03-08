"""Tests for tool-advisor.py — Recommandation proactive d'outils Grimoire."""
from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import asdict
from io import StringIO
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
    (tmp_project / "_grimoire-output").mkdir(parents=True, exist_ok=True)
    stats = tmp_project / ta.USAGE_LOG
    stats.parent.mkdir(parents=True, exist_ok=True)
    with open(stats, "w", encoding="utf-8") as f:
        f.write(json.dumps({"tool": "dream.py", "ts": "2025-01-01"}) + "\n")
        f.write(json.dumps({"tool": "memory-lint.py", "ts": "2025-01-02"}) + "\n")
    return tmp_project


@pytest.fixture
def project_many_tools(tmp_path):
    """Projet avec >10 outils pour tester la troncature du rendu."""
    (tmp_path / "framework" / "tools").mkdir(parents=True)
    for i in range(15):
        (tmp_path / "framework" / "tools" / f"tool-{i:02d}.py").write_text("")
    return tmp_path


# ── Constants ────────────────────────────────────────────────────────────────


class TestConstants:
    def test_advisor_version_format(self):
        assert ta.ADVISOR_VERSION
        parts = ta.ADVISOR_VERSION.split(".")
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)

    def test_usage_log_path(self):
        assert ta.USAGE_LOG
        assert ta.USAGE_LOG.endswith(".jsonl")

    def test_context_rules_structure(self):
        for rule in ta.CONTEXT_RULES:
            assert "id" in rule
            assert "pattern" in rule
            assert "tools" in rule
            assert "reason" in rule
            assert rule["id"].startswith("CTX-")
            assert isinstance(rule["tools"], list)
            assert len(rule["tools"]) > 0

    def test_workflows_structure(self):
        assert len(ta.WORKFLOWS) >= 5
        for wf in ta.WORKFLOWS:
            assert "name" in wf
            assert "description" in wf
            assert "steps" in wf
            assert len(wf["steps"]) > 0


# ── Data Model ───────────────────────────────────────────────────────────────


class TestToolSuggestion:
    def test_defaults(self):
        s = ta.ToolSuggestion(tool="foo.py", reason="test")
        assert s.priority == "medium"
        assert s.context_rule == ""

    def test_custom_fields(self):
        s = ta.ToolSuggestion(tool="bar.py", reason="r", priority="high",
                               context_rule="CTX-001")
        assert s.tool == "bar.py"
        assert s.priority == "high"
        assert s.context_rule == "CTX-001"

    def test_asdict(self):
        s = ta.ToolSuggestion(tool="x.py", reason="y")
        d = asdict(s)
        assert set(d.keys()) == {"tool", "reason", "priority", "context_rule"}


class TestAdvisorReport:
    def test_empty_report(self):
        r = ta.AdvisorReport(timestamp="now")
        assert r.suggestions == []
        assert r.unused_tools == []
        assert r.workflows == []

    def test_to_dict_keys(self):
        r = ta.AdvisorReport(timestamp="t")
        d = r.to_dict()
        assert set(d.keys()) == {"timestamp", "suggestions", "unused_tools", "workflows"}

    def test_to_dict_with_suggestions(self):
        s = ta.ToolSuggestion(tool="a.py", reason="b", priority="high")
        r = ta.AdvisorReport(timestamp="t", suggestions=[s])
        d = r.to_dict()
        assert len(d["suggestions"]) == 1
        assert d["suggestions"][0]["tool"] == "a.py"

    def test_to_dict_preserves_workflows(self):
        wf = [{"name": "wf", "steps": ["a", "b"]}]
        r = ta.AdvisorReport(timestamp="t", workflows=wf)
        assert r.to_dict()["workflows"] == wf


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

    # ── Toutes les règles de contexte ──

    def test_ctx_003_architecture(self):
        suggestions = ta.suggest_for_context("refactoring de l'architecture")
        tools = [s.tool for s in suggestions]
        assert "digital-twin.py" in tools
        assert "project-graph.py" in tools

    def test_ctx_005_release(self):
        suggestions = ta.suggest_for_context("préparer la release et le changelog")
        tools = [s.tool for s in suggestions]
        assert "preflight-check.py" in tools
        assert "cc-verify.sh" in tools

    def test_ctx_006_performance(self):
        suggestions = ta.suggest_for_context("optimiser les tokens et le coût")
        tools = [s.tool for s in suggestions]
        assert "token-budget.py" in tools
        assert "synapse-dashboard.py" in tools

    def test_ctx_007_orchestration(self):
        suggestions = ta.suggest_for_context("configurer l'orchestrateur d'agents")
        tools = [s.tool for s in suggestions]
        assert "orchestrator.py" in tools

    def test_ctx_008_innovation(self):
        suggestions = ta.suggest_for_context("brainstormer une nouvelle idée")
        tools = [s.tool for s in suggestions]
        assert "incubator.py" in tools
        assert "quantum-branch.py" in tools

    def test_ctx_009_monitoring(self):
        suggestions = ta.suggest_for_context("vérifier le dashboard de santé")
        tools = [s.tool for s in suggestions]
        assert "synapse-dashboard.py" in tools
        assert "fitness-tracker.py" in tools

    def test_ctx_010_session(self):
        suggestions = ta.suggest_for_context("reprendre la session et le contexte")
        tools = [s.tool for s in suggestions]
        assert "session-state.py" in tools
        assert "shared-context.py" in tools

    def test_case_insensitive(self):
        lower = ta.suggest_for_context("debug")
        upper = ta.suggest_for_context("DEBUG")
        assert [s.tool for s in lower] == [s.tool for s in upper]

    def test_empty_context(self):
        suggestions = ta.suggest_for_context("")
        assert suggestions == []

    def test_multiple_rules_combined(self):
        # Matches CTX-001 (test) + CTX-005 (release) — should merge
        suggestions = ta.suggest_for_context("tests avant la release")
        tools = [s.tool for s in suggestions]
        assert "preflight-check.py" in tools  # from both CTX-001 and CTX-005
        assert "cc-verify.sh" in tools  # from CTX-001 or CTX-005
        # Still unique
        assert len(tools) == len(set(tools))

    def test_each_suggestion_carries_context_rule_id(self):
        suggestions = ta.suggest_for_context("erreur de debug")
        for s in suggestions:
            assert s.context_rule.startswith("CTX-")


# ── Load Usage Stats ─────────────────────────────────────────────────────────


class TestLoadUsageStats:
    def test_no_file(self, tmp_path):
        stats = ta._load_usage_stats(tmp_path)
        assert stats == {}

    def test_valid_entries(self, project_with_usage):
        stats = ta._load_usage_stats(project_with_usage)
        assert stats["dream.py"] == 1
        assert stats["memory-lint.py"] == 1

    def test_multiple_entries_same_tool(self, tmp_project):
        stats_path = tmp_project / ta.USAGE_LOG
        stats_path.parent.mkdir(parents=True, exist_ok=True)
        with open(stats_path, "w", encoding="utf-8") as f:
            for _ in range(5):
                f.write(json.dumps({"tool": "dream.py", "ts": "2025"}) + "\n")
        stats = ta._load_usage_stats(tmp_project)
        assert stats["dream.py"] == 5

    def test_malformed_json_lines_skipped(self, tmp_project):
        stats_path = tmp_project / ta.USAGE_LOG
        stats_path.parent.mkdir(parents=True, exist_ok=True)
        with open(stats_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"tool": "dream.py", "ts": "2025"}) + "\n")
            f.write("NOT JSON\n")
            f.write("{broken json\n")
            f.write(json.dumps({"tool": "self-healing.py"}) + "\n")
        stats = ta._load_usage_stats(tmp_project)
        assert stats["dream.py"] == 1
        assert stats["self-healing.py"] == 1
        assert len(stats) == 2

    def test_empty_lines_skipped(self, tmp_project):
        stats_path = tmp_project / ta.USAGE_LOG
        stats_path.parent.mkdir(parents=True, exist_ok=True)
        with open(stats_path, "w", encoding="utf-8") as f:
            f.write("\n\n")
            f.write(json.dumps({"tool": "dream.py"}) + "\n")
            f.write("\n")
        stats = ta._load_usage_stats(tmp_project)
        assert stats == {"dream.py": 1}

    def test_entry_without_tool_key(self, tmp_project):
        stats_path = tmp_project / ta.USAGE_LOG
        stats_path.parent.mkdir(parents=True, exist_ok=True)
        with open(stats_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"action": "run"}) + "\n")
        stats = ta._load_usage_stats(tmp_project)
        assert stats == {}

    def test_entry_with_empty_tool(self, tmp_project):
        stats_path = tmp_project / ta.USAGE_LOG
        stats_path.parent.mkdir(parents=True, exist_ok=True)
        with open(stats_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"tool": ""}) + "\n")
        stats = ta._load_usage_stats(tmp_project)
        assert stats == {}


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

    def test_sorted_output(self, tmp_project):
        unused = ta.find_unused_tools(tmp_project)
        assert unused == sorted(unused)

    def test_usage_with_path_prefix(self, tmp_project):
        """Usage stocké avec chemin complet → doit quand même matcher."""
        stats_path = tmp_project / ta.USAGE_LOG
        stats_path.parent.mkdir(parents=True, exist_ok=True)
        with open(stats_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"tool": "framework/tools/dream.py"}) + "\n")
        unused = ta.find_unused_tools(tmp_project)
        assert "dream.py" not in unused


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

    def test_discovers_sh_files(self, tmp_project):
        (tmp_project / "framework" / "tools" / "cc-verify.sh").write_text("#!/bin/bash\n")
        tools = ta._discover_tools(tmp_project)
        assert "cc-verify.sh" in tools

    def test_ignores_non_script_files(self, tmp_project):
        (tmp_project / "framework" / "tools" / "notes.md").write_text("")
        (tmp_project / "framework" / "tools" / "data.json").write_text("{}")
        tools = ta._discover_tools(tmp_project)
        assert "notes.md" not in tools
        assert "data.json" not in tools

    def test_returns_set(self, tmp_project):
        tools = ta._discover_tools(tmp_project)
        assert isinstance(tools, set)


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

    def test_default_suggestions_low_priority(self, tmp_project):
        """Sans contexte → suggestions par défaut avec priorité low."""
        report = ta.build_advice(tmp_project, context="")
        for s in report.suggestions:
            assert s.priority == "low"

    def test_default_suggestions_include_core_tools(self, tmp_project):
        report = ta.build_advice(tmp_project)
        tool_names = [s.tool for s in report.suggestions]
        assert "synapse-dashboard.py" in tool_names
        assert "fitness-tracker.py" in tool_names

    def test_report_includes_unused(self, tmp_project):
        report = ta.build_advice(tmp_project)
        assert len(report.unused_tools) == 5

    def test_report_includes_workflows(self, tmp_project):
        report = ta.build_advice(tmp_project)
        assert len(report.workflows) == len(ta.WORKFLOWS)

    def test_timestamp_is_iso(self, tmp_project):
        report = ta.build_advice(tmp_project)
        # Should be parseable as ISO datetime
        from datetime import datetime
        datetime.fromisoformat(report.timestamp)

    def test_context_overrides_defaults(self, tmp_project):
        """Avec contexte valide → pas de suggestions par défaut low."""
        report = ta.build_advice(tmp_project, context="debug erreur crash")
        priorities = {s.priority for s in report.suggestions}
        assert "high" in priorities


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

    def test_unknown_action_error_message(self, tmp_project):
        result = ta.mcp_tool_advisor(str(tmp_project), action="xyz")
        assert "xyz" in result["error"]

    def test_suggest_with_context_returns_tools(self, tmp_project):
        result = ta.mcp_tool_advisor(str(tmp_project), action="suggest",
                                      context="mémoire stale")
        tools = [s["tool"] for s in result["suggestions"]]
        assert "dream.py" in tools

    def test_unused_returns_list(self, tmp_project):
        result = ta.mcp_tool_advisor(str(tmp_project), action="unused")
        assert isinstance(result["unused_tools"], list)
        assert result["count"] == len(result["unused_tools"])


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
        assert "Workflows Grimoire" in text
        assert "Pre-release" in text

    def test_render_high_priority_icon(self):
        s = ta.ToolSuggestion(tool="x.py", reason="r", priority="high")
        report = ta.AdvisorReport(timestamp="now", suggestions=[s])
        text = ta.render_suggestions(report)
        assert "🔴" in text

    def test_render_medium_priority_icon(self):
        s = ta.ToolSuggestion(tool="x.py", reason="r", priority="medium")
        report = ta.AdvisorReport(timestamp="now", suggestions=[s])
        text = ta.render_suggestions(report)
        assert "🟡" in text

    def test_render_low_priority_icon(self):
        s = ta.ToolSuggestion(tool="x.py", reason="r", priority="low")
        report = ta.AdvisorReport(timestamp="now", suggestions=[s])
        text = ta.render_suggestions(report)
        assert "🟢" in text

    def test_render_unused_tools_section(self, tmp_project):
        report = ta.build_advice(tmp_project)
        text = ta.render_suggestions(report)
        assert "non utilisés" in text

    def test_render_unused_truncation(self, project_many_tools):
        report = ta.build_advice(project_many_tools)
        text = ta.render_suggestions(report)
        assert "et " in text and "autres" in text

    def test_render_tool_name_in_output(self):
        s = ta.ToolSuggestion(tool="my-special-tool.py", reason="because")
        report = ta.AdvisorReport(timestamp="now", suggestions=[s])
        text = ta.render_suggestions(report)
        assert "my-special-tool.py" in text
        assert "because" in text

    def test_render_all_workflows(self):
        text = ta.render_workflows()
        for wf in ta.WORKFLOWS:
            assert wf["name"] in text

    def test_render_workflow_steps_numbered(self):
        text = ta.render_workflows()
        assert "1." in text
        assert "2." in text


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

    def test_suggest_with_context(self, capsys, tmp_project):
        ret = ta.main(["--project-root", str(tmp_project), "suggest",
                        "--context", "debug erreur"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "self-healing.py" in out

    def test_suggest_json_with_context(self, capsys, tmp_project):
        ret = ta.main(["--project-root", str(tmp_project), "--json", "suggest",
                        "--context", "mémoire"])
        assert ret == 0
        data = json.loads(capsys.readouterr().out)
        tools = [s["tool"] for s in data["suggestions"]]
        assert "dream.py" in tools

    def test_unused_cli(self, capsys, tmp_project):
        ret = ta.main(["--project-root", str(tmp_project), "unused"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "non utilisés" in out

    def test_unused_json(self, capsys, tmp_project):
        ret = ta.main(["--project-root", str(tmp_project), "--json", "unused"])
        assert ret == 0
        data = json.loads(capsys.readouterr().out)
        assert "unused_tools" in data
        assert isinstance(data["unused_tools"], list)

    def test_unused_all_used(self, capsys, project_with_usage):
        """Quand tous les outils sont utilisés, message vert."""
        # Mark all tools as used
        stats_path = project_with_usage / ta.USAGE_LOG
        with open(stats_path, "a", encoding="utf-8") as f:
            for name in ["preflight-check.py", "synapse-dashboard.py",
                         "self-healing.py"]:
                f.write(json.dumps({"tool": name}) + "\n")
        ret = ta.main(["--project-root", str(project_with_usage), "unused"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "Tous les outils" in out

    def test_workflows_cli(self, capsys, tmp_project):
        ret = ta.main(["--project-root", str(tmp_project), "workflows"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "Workflows" in out

    def test_workflows_json(self, capsys, tmp_project):
        ret = ta.main(["--project-root", str(tmp_project), "--json", "workflows"])
        assert ret == 0
        data = json.loads(capsys.readouterr().out)
        assert "workflows" in data
        assert len(data["workflows"]) == len(ta.WORKFLOWS)

    def test_default_command(self, capsys, tmp_project):
        """Sans sous-commande → se comporte comme 'suggest'."""
        ret = ta.main(["--project-root", str(tmp_project)])
        assert ret == 0


# ── Parser ───────────────────────────────────────────────────────────────────


class TestParser:
    def test_build_parser(self):
        parser = ta.build_parser()
        assert parser is not None
        assert parser.prog == "tool-advisor"

    def test_parse_suggest(self):
        parser = ta.build_parser()
        args = parser.parse_args(["suggest", "--context", "debug"])
        assert args.command == "suggest"
        assert args.context == "debug"

    def test_parse_unused(self):
        parser = ta.build_parser()
        args = parser.parse_args(["unused"])
        assert args.command == "unused"

    def test_parse_workflows(self):
        parser = ta.build_parser()
        args = parser.parse_args(["workflows"])
        assert args.command == "workflows"

    def test_parse_json_flag(self):
        parser = ta.build_parser()
        args = parser.parse_args(["--json", "suggest"])
        assert args.json is True

    def test_parse_project_root(self):
        parser = ta.build_parser()
        args = parser.parse_args(["--project-root", "/tmp/test", "suggest"])
        assert args.project_root == Path("/tmp/test")


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

    def test_rule_ids_unique(self):
        ids = [r["id"] for r in ta.CONTEXT_RULES]
        assert len(ids) == len(set(ids))

    def test_all_ten_rules_present(self):
        ids = {r["id"] for r in ta.CONTEXT_RULES}
        for i in range(1, 11):
            assert f"CTX-{i:03d}" in ids

    def test_each_rule_suggests_at_least_one_tool(self):
        for rule in ta.CONTEXT_RULES:
            assert len(rule["tools"]) >= 1

    def test_reasons_are_non_empty(self):
        for rule in ta.CONTEXT_RULES:
            assert rule["reason"].strip()
