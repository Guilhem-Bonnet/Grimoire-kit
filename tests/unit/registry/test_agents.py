"""Tests for grimoire.registry.agents — AgentRegistry + DNA loading."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from grimoire.core.exceptions import GrimoireAgentError, GrimoireRegistryError
from grimoire.registry.agents import AgentDef, AgentRegistry, ArchetypeDNA

# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_archetype(root: Path, arch_id: str, agents: list[dict[str, str]]) -> Path:
    """Create a minimal archetype with DNA + agent files."""
    arch_dir = root / "archetypes" / arch_id
    arch_dir.mkdir(parents=True)
    agents_dir = arch_dir / "agents"
    agents_dir.mkdir()

    agent_lines: list[str] = []
    for a in agents:
        name = a["name"]
        (agents_dir / f"{name}.md").write_text(f"# Agent {name}\nPersona for {name}.")
        agent_lines.append(f'  - path: "agents/{name}.md"')
        agent_lines.append(f"    required: {a.get('required', 'true')}")
        agent_lines.append(f'    description: "{a.get("description", name)}"')

    dna_content = (
        f'$schema: "grimoire-archetype-dna/v1"\n'
        f"id: {arch_id}\n"
        f'name: "{arch_id.title()}"\n'
        f'version: "1.0.0"\n'
        f'description: "Test archetype"\n'
        f"tags: [test]\n"
        f"agents:\n"
        + "\n".join(agent_lines) + "\n"
        "compatible_with: [minimal]\n"
        "incompatible_with: []\n"
    )
    (arch_dir / "archetype.dna.yaml").write_text(dna_content)
    return root


@pytest.fixture()
def kit_root(tmp_path: Path) -> Path:
    """Create a kit root with two archetypes."""
    _make_archetype(tmp_path, "test-arch", [
        {"name": "dev-agent", "description": "Developer"},
        {"name": "qa-agent", "description": "QA"},
    ])
    _make_archetype(tmp_path, "mini", [
        {"name": "solo", "description": "Solo agent"},
    ])
    return tmp_path


@pytest.fixture()
def registry(kit_root: Path) -> AgentRegistry:
    return AgentRegistry(kit_root)


# ── AgentDef ──────────────────────────────────────────────────────────────────

class TestAgentDef:
    def test_exists(self, tmp_path: Path) -> None:
        f = tmp_path / "agent.md"
        f.write_text("# Agent")
        ad = AgentDef(id="test", path=f)
        assert ad.exists

    def test_not_exists(self, tmp_path: Path) -> None:
        ad = AgentDef(id="ghost", path=tmp_path / "nope.md")
        assert not ad.exists

    def test_load_persona(self, tmp_path: Path) -> None:
        f = tmp_path / "agent.md"
        f.write_text("# My Agent\nDoes stuff.")
        ad = AgentDef(id="my", path=f)
        assert "My Agent" in ad.load_persona()

    def test_load_persona_missing(self, tmp_path: Path) -> None:
        ad = AgentDef(id="ghost", path=tmp_path / "nope.md")
        with pytest.raises(GrimoireAgentError, match="not found"):
            ad.load_persona()


# ── ArchetypeDNA ──────────────────────────────────────────────────────────────

class TestArchetypeDNA:
    def test_from_yaml(self, kit_root: Path) -> None:
        dna_path = kit_root / "archetypes" / "test-arch" / "archetype.dna.yaml"
        dna = ArchetypeDNA.from_yaml(dna_path)
        assert dna.id == "test-arch"
        assert dna.name == "Test-Arch"
        assert len(dna.agents) == 2
        assert dna.agents[0].id == "dev-agent"
        assert dna.agents[1].id == "qa-agent"

    def test_agents_resolved(self, kit_root: Path) -> None:
        dna_path = kit_root / "archetypes" / "test-arch" / "archetype.dna.yaml"
        dna = ArchetypeDNA.from_yaml(dna_path)
        for a in dna.agents:
            assert a.exists
            assert a.path.is_absolute()

    def test_missing_dna(self, tmp_path: Path) -> None:
        with pytest.raises(GrimoireRegistryError, match="not found"):
            ArchetypeDNA.from_yaml(tmp_path / "nope.yaml")

    def test_invalid_dna(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("not: a: valid: yaml: {{")
        with pytest.raises(GrimoireRegistryError):
            ArchetypeDNA.from_yaml(bad)

    def test_tags_and_compat(self, kit_root: Path) -> None:
        dna_path = kit_root / "archetypes" / "test-arch" / "archetype.dna.yaml"
        dna = ArchetypeDNA.from_yaml(dna_path)
        assert "test" in dna.tags
        assert "minimal" in dna.compatible_with


# ── AgentRegistry ─────────────────────────────────────────────────────────────

class TestAgentRegistry:
    def test_list_archetypes(self, registry: AgentRegistry) -> None:
        archs = registry.list_archetypes()
        assert "test-arch" in archs
        assert "mini" in archs

    def test_get_dna(self, registry: AgentRegistry) -> None:
        dna = registry.get_dna("test-arch")
        assert dna.id == "test-arch"
        assert len(dna.agents) == 2

    def test_get_dna_cached(self, registry: AgentRegistry) -> None:
        dna1 = registry.get_dna("test-arch")
        dna2 = registry.get_dna("test-arch")
        assert dna1 is dna2

    def test_get_dna_not_found(self, registry: AgentRegistry) -> None:
        with pytest.raises(GrimoireRegistryError, match="not found"):
            registry.get_dna("nonexistent")

    def test_get_agent(self, registry: AgentRegistry) -> None:
        agent = registry.get_agent("test-arch", "dev-agent")
        assert agent.id == "dev-agent"
        assert agent.description == "Developer"

    def test_get_agent_not_found(self, registry: AgentRegistry) -> None:
        with pytest.raises(GrimoireAgentError, match="not found"):
            registry.get_agent("test-arch", "nonexistent-agent")

    def test_resolve_agents(self, registry: AgentRegistry) -> None:
        agents = registry.resolve_agents("test-arch")
        assert len(agents) == 2
        assert all(a.exists for a in agents)

    def test_resolve_agents_missing_required(self, tmp_path: Path) -> None:
        """Test that missing required agents raise GrimoireAgentError."""
        arch_dir = tmp_path / "archetypes" / "broken"
        arch_dir.mkdir(parents=True)
        dna = dedent("""\
            id: broken
            name: Broken
            version: "1.0.0"
            description: "Missing agents"
            agents:
              - path: "agents/ghost.md"
                required: true
                description: "Does not exist"
        """)
        (arch_dir / "archetype.dna.yaml").write_text(dna)
        reg = AgentRegistry(tmp_path)
        with pytest.raises(GrimoireAgentError, match="Required agents missing"):
            reg.resolve_agents("broken")

    def test_empty_archetypes_dir(self, tmp_path: Path) -> None:
        (tmp_path / "archetypes").mkdir()
        reg = AgentRegistry(tmp_path)
        assert reg.list_archetypes() == []


# ── Live archetypes ──────────────────────────────────────────────────────────

class TestLiveArchetypes:
    """Test against the real archetypes/ directory."""

    @pytest.fixture()
    def real_kit_root(self) -> Path | None:
        """Find the kit root by walking up from this test file."""
        p = Path(__file__).resolve()
        for parent in [p, *p.parents]:
            if (parent / "archetypes").is_dir():
                return parent
        return None

    def test_list_real_archetypes(self, real_kit_root: Path | None) -> None:
        if real_kit_root is None:
            pytest.skip("No archetypes/ found")
        reg = AgentRegistry(real_kit_root)
        archs = reg.list_archetypes()
        assert len(archs) >= 3
        assert "minimal" in archs

    def test_load_minimal_dna(self, real_kit_root: Path | None) -> None:
        if real_kit_root is None:
            pytest.skip("No archetypes/ found")
        reg = AgentRegistry(real_kit_root)
        dna = reg.get_dna("minimal")
        assert dna.id == "minimal"
        assert len(dna.agents) >= 1
