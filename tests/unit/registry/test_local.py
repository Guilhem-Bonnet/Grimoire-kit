"""Tests for bmad.registry.local — LocalRegistry catalog."""

from __future__ import annotations

from pathlib import Path

import pytest

from bmad.core.exceptions import BmadRegistryError
from bmad.registry.local import LocalRegistry

# ── Fixtures ──────────────────────────────────────────────────────────────────

_DNA_YAML = """\
id: "{id}"
name: "{name}"
version: "1.0.0"
description: "{desc}"
tags: [{tags}]
agents:
  - path: agents/agent-a.md
    required: true
    description: "Agent Alpha"
  - path: agents/agent-b.md
    required: false
    description: "Agent Beta"
"""


@pytest.fixture()
def kit_root(tmp_path: Path) -> Path:
    """Create a kit root with 2 archetypes."""
    archetypes = tmp_path / "archetypes"

    # minimal archetype
    minimal = archetypes / "minimal"
    agents_dir = minimal / "agents"
    agents_dir.mkdir(parents=True)
    (minimal / "archetype.dna.yaml").write_text(
        _DNA_YAML.format(id="minimal", name="Minimal", desc="Bare bones setup", tags='"starter"')
    )
    (agents_dir / "agent-a.md").write_text("# Agent A\n")
    (agents_dir / "agent-b.md").write_text("# Agent B\n")

    # infra-ops archetype
    infra = archetypes / "infra-ops"
    infra_agents = infra / "agents"
    infra_agents.mkdir(parents=True)
    (infra / "archetype.dna.yaml").write_text(
        _DNA_YAML.format(id="infra-ops", name="Infra Ops", desc="Infrastructure team", tags='"infra", "kubernetes"')
    )
    (infra_agents / "agent-a.md").write_text("# Agent A\n")
    (infra_agents / "agent-b.md").write_text("# Agent B\n")

    return tmp_path


@pytest.fixture()
def registry(kit_root: Path) -> LocalRegistry:
    return LocalRegistry(kit_root)


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestListArchetypes:
    def test_lists_all(self, registry: LocalRegistry) -> None:
        archs = registry.list_archetypes()
        assert "minimal" in archs
        assert "infra-ops" in archs

    def test_empty_kit(self, tmp_path: Path) -> None:
        reg = LocalRegistry(tmp_path)
        assert reg.list_archetypes() == []


class TestListAgents:
    def test_all_agents(self, registry: LocalRegistry) -> None:
        agents = registry.list_agents()
        assert len(agents) == 4  # 2 per archetype

    def test_agent_fields(self, registry: LocalRegistry) -> None:
        agents = registry.list_agents()
        a = next(a for a in agents if a.id == "agent-a" and a.archetype == "minimal")
        assert a.description == "Agent Alpha"
        assert a.required is True

    def test_tags_propagated(self, registry: LocalRegistry) -> None:
        agents = registry.list_agents()
        infra_agent = next(a for a in agents if a.archetype == "infra-ops")
        assert "kubernetes" in infra_agent.tags


class TestGet:
    def test_found(self, registry: LocalRegistry) -> None:
        item = registry.get("agent-a")
        assert item.id == "agent-a"

    def test_not_found(self, registry: LocalRegistry) -> None:
        with pytest.raises(BmadRegistryError, match="not found"):
            registry.get("nonexistent")


class TestSearch:
    def test_by_id(self, registry: LocalRegistry) -> None:
        results = registry.search("agent-b")
        assert len(results) >= 1
        assert all(r.id == "agent-b" for r in results)

    def test_by_archetype(self, registry: LocalRegistry) -> None:
        results = registry.search("infra-ops")
        assert len(results) == 2

    def test_by_tag(self, registry: LocalRegistry) -> None:
        results = registry.search("kubernetes")
        assert len(results) >= 1
        assert all(r.archetype == "infra-ops" for r in results)

    def test_by_description(self, registry: LocalRegistry) -> None:
        results = registry.search("Alpha")
        assert len(results) >= 1

    def test_no_match(self, registry: LocalRegistry) -> None:
        results = registry.search("zzz-nonexistent")
        assert results == []

    def test_case_insensitive(self, registry: LocalRegistry) -> None:
        r1 = registry.search("MINIMAL")
        r2 = registry.search("minimal")
        assert len(r1) == len(r2)


class TestInspectArchetype:
    def test_inspect(self, registry: LocalRegistry) -> None:
        dna = registry.inspect_archetype("minimal")
        assert dna.id == "minimal"
        assert len(dna.agents) == 2

    def test_inspect_not_found(self, registry: LocalRegistry) -> None:
        with pytest.raises(BmadRegistryError):
            registry.inspect_archetype("nonexistent")


class TestCaching:
    def test_index_cached(self, registry: LocalRegistry) -> None:
        a1 = registry.list_agents()
        a2 = registry.list_agents()
        # Internal index is cached — new list objects but same content
        assert a1 == a2
        assert len(a1) == len(a2)
