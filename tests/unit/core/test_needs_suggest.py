"""Tests des suggestions de needs pilotées par le projet (B3)."""

from __future__ import annotations

from pathlib import Path

from grimoire.core.needs_suggest import suggest_needs
from grimoire.core.scanner import StackScanner

_CATALOG = {
    "needs": [
        {"id": "solo-prototyping"},
        {"id": "semantic-memory-rag"},
        {"id": "production-release-gating"},
        {"id": "hooks-skills-governance"},
        {"id": "tool-mediation-security"},
        {"id": "multi-agent-orchestration"},
        {"id": "knowledge-graph"},
    ]
}


def _scan(root: Path):
    return StackScanner(root).scan()


class TestSuggestNeeds:
    def test_empty_project_falls_back_to_solo(self, tmp_path: Path) -> None:
        suggestions = suggest_needs(_scan(tmp_path), _CATALOG)
        assert [s.need_id for s in suggestions] == ["solo-prototyping"]

    def test_docs_corpus_suggests_rag(self, tmp_path: Path) -> None:
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "guide.md").write_text("# Guide\n", encoding="utf-8")
        ids = [s.need_id for s in suggest_needs(_scan(tmp_path), _CATALOG)]
        assert "semantic-memory-rag" in ids

    def test_ci_plus_containers_suggests_release_gating(self, tmp_path: Path) -> None:
        wf = tmp_path / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "ci.yml").write_text("name: ci\n", encoding="utf-8")
        (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        suggestions = suggest_needs(_scan(tmp_path), _CATALOG)
        by_id = {s.need_id: s for s in suggestions}
        assert "production-release-gating" in by_id
        assert ".github/workflows/ci.yml" in by_id["production-release-gating"].evidence

    def test_agents_and_hooks_surfaces(self, tmp_path: Path) -> None:
        agents = tmp_path / ".github" / "agents"
        agents.mkdir(parents=True)
        (agents / "dev.agent.md").write_text("# dev\n", encoding="utf-8")
        hooks = tmp_path / ".github" / "hooks"
        hooks.mkdir(parents=True)
        (hooks / "h.json").write_text("{}", encoding="utf-8")
        ids = [s.need_id for s in suggest_needs(_scan(tmp_path), _CATALOG)]
        assert "multi-agent-orchestration" in ids
        assert "hooks-skills-governance" in ids

    def test_kit_written_hooks_and_skills_do_not_suggest_governance(self, tmp_path: Path) -> None:
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "settings.json").write_text(
            '{"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "grimoire-hook --host claude"}]}]}}',
            encoding="utf-8",
        )
        (tmp_path / ".github" / "hooks").mkdir(parents=True)
        (tmp_path / ".github" / "hooks" / "k.json").write_text('{"bash": "grimoire-hook"}', encoding="utf-8")
        (tmp_path / ".github" / "skills" / "grimoire-evidence").mkdir(parents=True)
        ids = [s.need_id for s in suggest_needs(_scan(tmp_path), _CATALOG)]
        assert "hooks-skills-governance" not in ids

    def test_a_project_own_hook_or_skill_suggests_governance(self, tmp_path: Path) -> None:
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "settings.json").write_text(
            '{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "./lint.sh"}]}]}}', encoding="utf-8"
        )
        assert "hooks-skills-governance" in [s.need_id for s in suggest_needs(_scan(tmp_path), _CATALOG)]
        (tmp_path / ".claude" / "settings.json").write_text("{oops", encoding="utf-8")
        assert "hooks-skills-governance" in [s.need_id for s in suggest_needs(_scan(tmp_path), _CATALOG)]
        (tmp_path / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
        (tmp_path / ".github" / "skills" / "mine").mkdir(parents=True)
        assert "hooks-skills-governance" in [s.need_id for s in suggest_needs(_scan(tmp_path), _CATALOG)]

    def test_foreign_mcp_server_suggests_tool_mediation(self, tmp_path: Path) -> None:
        (tmp_path / ".mcp.json").write_text(
            '{"mcpServers": {"grimoire": {"command": "grimoire-mcp"}, "db": {"command": "dbmcp"}}}', encoding="utf-8"
        )
        ids = [s.need_id for s in suggest_needs(_scan(tmp_path), _CATALOG)]
        assert "tool-mediation-security" in ids

    def test_unparseable_mcp_config_still_suggests_tool_mediation(self, tmp_path: Path) -> None:
        (tmp_path / ".mcp.json").write_text("{not json", encoding="utf-8")
        ids = [s.need_id for s in suggest_needs(_scan(tmp_path), _CATALOG)]
        assert "tool-mediation-security" in ids

    def test_kit_written_mcp_config_does_not_suggest_tool_mediation(self, tmp_path: Path) -> None:
        # The `.mcp.json` `grimoire up` writes holds only the kit's own server.
        (tmp_path / ".mcp.json").write_text('{"mcpServers": {"grimoire": {"command": "grimoire-mcp"}}}', encoding="utf-8")
        (tmp_path / ".vscode").mkdir()
        (tmp_path / ".vscode" / "mcp.json").write_text('{"servers": {"grimoire": {}}}', encoding="utf-8")
        (tmp_path / "mcp.json").write_text("[]", encoding="utf-8")
        ids = [s.need_id for s in suggest_needs(_scan(tmp_path), _CATALOG)]
        assert "tool-mediation-security" not in ids

    def test_unknown_ids_never_suggested(self, tmp_path: Path) -> None:
        (tmp_path / ".mcp.json").write_text('{"mcpServers": {"db": {}}}', encoding="utf-8")
        tiny_catalog = {"needs": [{"id": "solo-prototyping"}]}
        ids = [s.need_id for s in suggest_needs(_scan(tmp_path), tiny_catalog)]
        assert ids == ["solo-prototyping"]  # mcp filtré (absent du catalogue)

    def test_real_catalog_ids_all_valid(self, tmp_path: Path) -> None:
        # Les ids codés dans le pont existent dans le vrai catalogue du kit.
        from grimoire.core.agentic_standard import load_needs_catalog

        catalog = load_needs_catalog()
        known = {n["id"] for n in catalog["needs"]}
        (tmp_path / ".mcp.json").write_text("{}", encoding="utf-8")
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "a.md").write_text("x\n", encoding="utf-8")
        for s in suggest_needs(_scan(tmp_path), catalog):
            assert s.need_id in known

    def test_empty_project_suggests_discovery_first(self, tmp_path: Path) -> None:
        catalog = {"needs": [{"id": "project-discovery"}, {"id": "solo-prototyping"}]}
        ids = [s.need_id for s in suggest_needs(_scan(tmp_path), catalog)]
        assert ids == ["project-discovery", "solo-prototyping"]

    def test_catalog_needs_null_does_not_crash(self, tmp_path: Path) -> None:
        assert suggest_needs(_scan(tmp_path), {"needs": None}) == []

    def test_catalog_non_dict_entries_skipped(self, tmp_path: Path) -> None:
        catalog = {"needs": ["solo-prototyping", None, {"id": "solo-prototyping"}]}
        ids = [s.need_id for s in suggest_needs(_scan(tmp_path), catalog)]
        assert ids == ["solo-prototyping"]
