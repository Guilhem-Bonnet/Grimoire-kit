"""Tests for grimoire.tools.agent_forge — Agent scaffold generator."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.tools.agent_forge import (
    DOMAIN_TAXONOMY,
    AgentForge,
    AgentProposal,
    check_overlap,
    detect_domain,
    extract_agent_name,
    find_existing_agents,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    (tmp_path / "_grimoire/_config/agents").mkdir(parents=True)
    (tmp_path / "_grimoire/core/agents").mkdir(parents=True)
    return tmp_path


@pytest.fixture()
def forge(root: Path) -> AgentForge:
    return AgentForge(root)


# ── detect_domain ─────────────────────────────────────────────────────────────


class TestDetectDomain:
    def test_database_domain(self) -> None:
        key, profile = detect_domain("I need an agent to handle database migrations and SQL schemas")
        assert key == "database"
        assert profile["icon"] == "🗄️"

    def test_security_domain(self) -> None:
        key, _ = detect_domain("Agent pour gérer la sécurité et les vulnérabilités")
        assert key == "security"

    def test_frontend_domain(self) -> None:
        key, _ = detect_domain("Build React components and CSS styles")
        assert key == "frontend"

    def test_api_domain(self) -> None:
        key, _ = detect_domain("Design REST API endpoints with OpenAPI swagger")
        assert key == "api"

    def test_testing_domain(self) -> None:
        key, _ = detect_domain("QA testing coverage regression e2e")
        assert key == "testing"

    def test_devops_domain(self) -> None:
        key, _ = detect_domain("Set up CI/CD pipeline with deploy automation")
        assert key == "devops"

    def test_monitoring_domain(self) -> None:
        key, _ = detect_domain("Set up observability monitoring with Grafana metrics")
        assert key == "monitoring"

    def test_data_domain(self) -> None:
        key, _ = detect_domain("Build ETL data pipeline with analytics")
        assert key == "data"

    def test_documentation_domain(self) -> None:
        key, _ = detect_domain("Write documentation guide tutorials and README wiki")
        assert key == "documentation"

    def test_performance_domain(self) -> None:
        key, _ = detect_domain("Optimize performance latency throughput profiling")
        assert key == "performance"

    def test_unknown_domain(self) -> None:
        key, profile = detect_domain("something completely random xyz abc")
        assert key == "custom"
        assert profile["icon"] == "🤖"

    def test_all_taxonomy_domains_exist(self) -> None:
        expected = {"database", "security", "frontend", "api", "testing",
                    "devops", "monitoring", "data", "documentation", "performance"}
        assert expected <= set(DOMAIN_TAXONOMY.keys())


# ── extract_agent_name ────────────────────────────────────────────────────────


class TestExtractAgentName:
    def test_basic_extraction(self) -> None:
        _, profile = detect_domain("database")
        name, tag = extract_agent_name("manage database migrations", "database", profile)
        assert len(name) > 0
        assert tag.startswith("db-")

    def test_french_description(self) -> None:
        _, profile = detect_domain("security")
        name, tag = extract_agent_name("gérer la sécurité des APIs",
                                        "security", profile)
        assert len(tag) > 0
        assert tag.startswith("sec-")

    def test_empty_description_uses_domain(self) -> None:
        _, profile = detect_domain("")
        name, tag = extract_agent_name("le la du", "custom", profile)
        # Should fallback to domain key
        assert len(name) > 0

    def test_grammatical_pattern(self) -> None:
        _, profile = detect_domain("api")
        name, tag = extract_agent_name("agent for API gateway routing",
                                        "api", profile)
        assert len(tag) > 3

    def test_accent_transliteration(self) -> None:
        _, profile = detect_domain("database")
        name, tag = extract_agent_name("gérer les données éphémères",
                                        "database", profile)
        # Should not contain accented chars
        assert all(c.isascii() for c in tag)


# ── find_existing_agents ──────────────────────────────────────────────────────


class TestFindExistingAgents:
    def test_finds_agents(self, root: Path) -> None:
        (root / "_grimoire/_config/agents/analyst.md").write_text("# Analyst")
        (root / "_grimoire/core/agents/dev.md").write_text("# Dev")
        agents = find_existing_agents(root)
        assert "analyst" in agents
        assert "dev" in agents

    def test_excludes_templates(self, root: Path) -> None:
        (root / "_grimoire/_config/agents/template-base.md").write_text("tpl")
        agents = find_existing_agents(root)
        assert "template-base" not in agents

    def test_empty_project(self, tmp_path: Path) -> None:
        agents = find_existing_agents(tmp_path)
        assert agents == []


# ── check_overlap ─────────────────────────────────────────────────────────────


class TestCheckOverlap:
    def test_overlap_detected(self) -> None:
        existing = ["database-admin", "sec-audit", "dev-frontend"]
        overlap = check_overlap("db-database-migration", existing)
        assert "database-admin" in overlap

    def test_no_overlap(self) -> None:
        existing = ["analyst", "pm", "qa"]
        overlap = check_overlap("db-migration-handler", existing)
        assert overlap == []

    def test_short_keywords_ignored(self) -> None:
        existing = ["db-admin"]
        overlap = check_overlap("db-x", existing)
        # "db" has length 2 → ignored, "x" has length 1 → ignored
        assert overlap == []


# ── AgentProposal Model ──────────────────────────────────────────────────────


class TestAgentProposal:
    def test_to_dict(self) -> None:
        p = AgentProposal(
            source="description", description="test", domain_key="api",
            agent_name="ApiGateway", agent_tag="api-gateway",
            agent_icon="🔌", agent_role="API Specialist",
            overlap=["api-old"],
        )
        d = p.to_dict()
        assert d["domain_key"] == "api"
        assert d["agent_tag"] == "api-gateway"
        assert d["overlap"] == ["api-old"]


# ── AgentForge Tool ──────────────────────────────────────────────────────────


class TestAgentForgeTool:
    def test_run_database(self, forge: AgentForge) -> None:
        proposal = forge.run(description="I need a database migration agent")
        assert isinstance(proposal, AgentProposal)
        assert proposal.domain_key == "database"
        assert proposal.source == "description"

    def test_run_security(self, forge: AgentForge) -> None:
        proposal = forge.run(description="Agent pour auditer la sécurité")
        assert proposal.domain_key == "security"

    def test_run_unknown(self, forge: AgentForge) -> None:
        proposal = forge.run(description="xyzzy plugh random")
        assert proposal.domain_key == "custom"

    def test_run_detects_overlap(self, root: Path) -> None:
        (root / "_grimoire/_config/agents/database-admin.md").write_text("# DB Admin")
        forge = AgentForge(root)
        proposal = forge.run(description="Handle database operations and migrations")
        assert len(proposal.overlap) > 0

    def test_run_empty_description(self, forge: AgentForge) -> None:
        proposal = forge.run(description="")
        assert isinstance(proposal, AgentProposal)
        assert proposal.domain_key == "custom"
