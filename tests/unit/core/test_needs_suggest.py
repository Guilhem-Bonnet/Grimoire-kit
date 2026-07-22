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

    def test_mcp_config_suggests_tool_mediation(self, tmp_path: Path) -> None:
        (tmp_path / ".mcp.json").write_text("{}", encoding="utf-8")
        ids = [s.need_id for s in suggest_needs(_scan(tmp_path), _CATALOG)]
        assert "tool-mediation-security" in ids

    def test_unknown_ids_never_suggested(self, tmp_path: Path) -> None:
        (tmp_path / ".mcp.json").write_text("{}", encoding="utf-8")
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
