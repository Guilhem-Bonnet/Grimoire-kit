"""Tests for grimoire.core.config — GrimoireConfig dataclasses."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from grimoire.core.config import (
    AgentsConfig,
    GrimoireConfig,
    MemoryConfig,
    ProjectConfig,
    RepoConfig,
    UserConfig,
)
from grimoire.core.exceptions import GrimoireConfigError

# ── Fixtures ──────────────────────────────────────────────────────────────────

def _minimal_dict(**overrides: Any) -> dict[str, Any]:
    """Return a minimal valid config dict."""
    base: dict[str, Any] = {
        "project": {"name": "test-project"},
    }
    base.update(overrides)
    return base


@pytest.fixture()
def tmp_yaml(tmp_path: Path) -> Path:
    """Create a temporary project-context.yaml."""
    p = tmp_path / "project-context.yaml"
    p.write_text(
        "project:\n"
        "  name: fixture-project\n"
        "  type: webapp\n"
        "  stack: [python, react]\n"
        "user:\n"
        "  name: Alice\n"
        "  skill_level: expert\n"
        "memory:\n"
        "  backend: local\n"
        "agents:\n"
        "  archetype: web-app\n"
        "installed_archetypes: [web-app, minimal]\n"
    )
    return p


# ── RepoConfig ────────────────────────────────────────────────────────────────

class TestRepoConfig:
    def test_from_dict_full(self) -> None:
        rc = RepoConfig.from_dict({"name": "api", "path": "./api", "default_branch": "develop"})
        assert rc.name == "api"
        assert rc.path == "./api"
        assert rc.default_branch == "develop"

    def test_from_dict_defaults(self) -> None:
        rc = RepoConfig.from_dict({"name": "main"})
        assert rc.path == "."
        assert rc.default_branch == "main"

    def test_frozen(self) -> None:
        rc = RepoConfig(name="x")
        with pytest.raises(AttributeError):
            rc.name = "y"  # type: ignore[misc]


# ── ProjectConfig ─────────────────────────────────────────────────────────────

class TestProjectConfig:
    def test_from_dict_full(self) -> None:
        pc = ProjectConfig.from_dict({
            "name": "my-app",
            "description": "Cool app",
            "type": "infrastructure",
            "metaphor": "forteresse",
            "stack": ["terraform", "docker"],
            "repos": [{"name": "infra", "path": "./infra"}],
        })
        assert pc.name == "my-app"
        assert pc.type == "infrastructure"
        assert pc.stack == ("terraform", "docker")
        assert len(pc.repos) == 1
        assert pc.repos[0].name == "infra"

    def test_from_dict_minimal(self) -> None:
        pc = ProjectConfig.from_dict({"name": "bare"})
        assert pc.name == "bare"
        assert pc.type == "webapp"
        assert pc.stack == ()
        assert pc.repos == ()

    def test_stack_is_tuple(self) -> None:
        pc = ProjectConfig.from_dict({"name": "t", "stack": ["a", "b"]})
        assert isinstance(pc.stack, tuple)


# ── UserConfig ────────────────────────────────────────────────────────────────

class TestUserConfig:
    def test_from_dict_full(self) -> None:
        uc = UserConfig.from_dict({
            "name": "Bob",
            "language": "English",
            "document_language": "English",
            "skill_level": "expert",
        })
        assert uc.name == "Bob"
        assert uc.skill_level == "expert"

    def test_defaults(self) -> None:
        uc = UserConfig.from_dict({})
        assert uc.language == "Français"
        assert uc.skill_level == "intermediate"

    def test_invalid_skill_level(self) -> None:
        with pytest.raises(GrimoireConfigError, match="Invalid skill_level"):
            UserConfig.from_dict({"skill_level": "god-mode"})


# ── MemoryConfig ──────────────────────────────────────────────────────────────

class TestMemoryConfig:
    def test_from_dict_full(self) -> None:
        mc = MemoryConfig.from_dict({
            "backend": "qdrant-local",
            "collection_prefix": "myproj",
            "embedding_model": "all-MiniLM-L6-v2",
        })
        assert mc.backend == "qdrant-local"
        assert mc.collection_prefix == "myproj"

    def test_defaults(self) -> None:
        mc = MemoryConfig.from_dict({})
        assert mc.backend == "auto"
        assert mc.collection_prefix == "grimoire"

    def test_invalid_backend(self) -> None:
        with pytest.raises(GrimoireConfigError, match="Invalid memory backend"):
            MemoryConfig.from_dict({"backend": "redis"})

    @pytest.mark.parametrize("backend", ["auto", "local", "qdrant-local", "qdrant-server", "ollama"])
    def test_all_valid_backends(self, backend: str) -> None:
        mc = MemoryConfig.from_dict({"backend": backend})
        assert mc.backend == backend


# ── AgentsConfig ──────────────────────────────────────────────────────────────

class TestAgentsConfig:
    def test_from_dict(self) -> None:
        ac = AgentsConfig.from_dict({"archetype": "web-app", "custom_agents": ["my-agent"]})
        assert ac.archetype == "web-app"
        assert ac.custom_agents == ("my-agent",)

    def test_defaults(self) -> None:
        ac = AgentsConfig.from_dict({})
        assert ac.archetype == "minimal"
        assert ac.custom_agents == ()


# ── GrimoireConfig — from_dict ───────────────────────────────────────────────────

class TestGrimoireConfigFromDict:
    def test_minimal(self) -> None:
        cfg = GrimoireConfig.from_dict(_minimal_dict())
        assert cfg.project.name == "test-project"
        assert cfg.user.skill_level == "intermediate"
        assert cfg.memory.backend == "auto"
        assert cfg.agents.archetype == "minimal"
        assert cfg.installed_archetypes == ()
        assert cfg.extra == {}

    def test_full(self) -> None:
        cfg = GrimoireConfig.from_dict({
            "project": {"name": "full", "type": "game", "stack": ["unity"]},
            "user": {"name": "Charlie", "skill_level": "beginner"},
            "memory": {"backend": "qdrant-server", "qdrant_url": "http://localhost:6333"},
            "agents": {"archetype": "creative-studio", "custom_agents": ["narrator"]},
            "installed_archetypes": ["creative-studio"],
            "llm_router": {"enabled": True},
        })
        assert cfg.project.name == "full"
        assert cfg.user.name == "Charlie"
        assert cfg.memory.qdrant_url == "http://localhost:6333"
        assert cfg.agents.custom_agents == ("narrator",)
        assert cfg.installed_archetypes == ("creative-studio",)
        assert "llm_router" in cfg.extra

    def test_extra_fields_preserved(self) -> None:
        cfg = GrimoireConfig.from_dict({
            **_minimal_dict(),
            "llm_router": {"enabled": True},
            "rag": {"max_chunks": 5},
            "platform": {"type": "microservices"},
        })
        assert set(cfg.extra.keys()) == {"llm_router", "rag", "platform"}

    def test_missing_project_raises(self) -> None:
        with pytest.raises(GrimoireConfigError, match=r"project.*section"):
            GrimoireConfig.from_dict({})

    def test_missing_project_name_raises(self) -> None:
        with pytest.raises(GrimoireConfigError, match=r"project.*name"):
            GrimoireConfig.from_dict({"project": {}})

    def test_non_dict_raises(self) -> None:
        with pytest.raises(GrimoireConfigError, match="mapping"):
            GrimoireConfig.from_dict("not a dict")  # type: ignore[arg-type]

    def test_frozen(self) -> None:
        cfg = GrimoireConfig.from_dict(_minimal_dict())
        with pytest.raises(AttributeError):
            cfg.project = ProjectConfig(name="hacked")  # type: ignore[misc]


# ── GrimoireConfig — from_yaml ──────────────────────────────────────────────────

class TestGrimoireConfigFromYaml:
    def test_load(self, tmp_yaml: Path) -> None:
        cfg = GrimoireConfig.from_yaml(tmp_yaml)
        assert cfg.project.name == "fixture-project"
        assert cfg.project.type == "webapp"
        assert cfg.project.stack == ("python", "react")
        assert cfg.user.name == "Alice"
        assert cfg.user.skill_level == "expert"
        assert cfg.memory.backend == "local"
        assert cfg.agents.archetype == "web-app"
        assert cfg.installed_archetypes == ("web-app", "minimal")

    def test_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(GrimoireConfigError, match="not found"):
            GrimoireConfig.from_yaml(tmp_path / "nope.yaml")

    def test_empty_file(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty.yaml"
        empty.write_text("")
        with pytest.raises(GrimoireConfigError, match="empty"):
            GrimoireConfig.from_yaml(empty)

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("{{{{not yaml")
        with pytest.raises(GrimoireConfigError, match="Cannot parse"):
            GrimoireConfig.from_yaml(bad)


# ── GrimoireConfig — find_and_load ───────────────────────────────────────────────

class TestGrimoireConfigFindAndLoad:
    def test_finds_in_current_dir(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "project-context.yaml"
        cfg_file.write_text("project:\n  name: found-here\n")
        cfg = GrimoireConfig.find_and_load(tmp_path)
        assert cfg.project.name == "found-here"

    def test_finds_in_parent(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "project-context.yaml"
        cfg_file.write_text("project:\n  name: parent-found\n")
        child = tmp_path / "src" / "deep"
        child.mkdir(parents=True)
        cfg = GrimoireConfig.find_and_load(child)
        assert cfg.project.name == "parent-found"

    def test_not_found_raises(self, tmp_path: Path) -> None:
        isolated = tmp_path / "nowhere"
        isolated.mkdir()
        with pytest.raises(GrimoireConfigError, match=r"No.*project-context.yaml"):
            GrimoireConfig.find_and_load(isolated)


# ── Live project-context.yaml ────────────────────────────────────────────────

class TestConfigValidation:
    """Tests for GrimoireConfig.validate() semantic checks."""

    def test_valid_minimal_config(self) -> None:
        cfg = GrimoireConfig.from_dict(_minimal_dict())
        assert cfg.validate() == []

    def test_warns_qdrant_server_without_url(self) -> None:
        d = _minimal_dict()
        d["memory"] = {"backend": "qdrant-server", "qdrant_url": ""}
        cfg = GrimoireConfig.from_dict(d)
        warnings = cfg.validate()
        assert any("qdrant" in w.lower() for w in warnings)

    def test_warns_ollama_without_url(self) -> None:
        d = _minimal_dict()
        d["memory"] = {"backend": "ollama", "ollama_url": ""}
        cfg = GrimoireConfig.from_dict(d)
        warnings = cfg.validate()
        assert any("ollama" in w.lower() for w in warnings)

    def test_warns_blank_project_name(self) -> None:
        d = _minimal_dict()
        d["project"]["name"] = "   "
        cfg = GrimoireConfig.from_dict(d)
        warnings = cfg.validate()
        assert any("blank" in w.lower() for w in warnings)

    def test_no_warnings_fully_configured(self) -> None:
        d = _minimal_dict()
        d["memory"] = {
            "backend": "qdrant-server",
            "qdrant_url": "http://localhost:6333",
        }
        cfg = GrimoireConfig.from_dict(d)
        assert cfg.validate() == []

    def test_no_warning_default_backend(self) -> None:
        cfg = GrimoireConfig.from_dict(_minimal_dict())
        assert cfg.validate() == []


class TestLiveConfig:
    """Load the real project-context.yaml to ensure it parses."""

    @pytest.fixture()
    def real_config_path(self) -> Path | None:
        p = Path(__file__).resolve()
        for parent in [p, *p.parents]:
            candidate = parent / "project-context.yaml"
            if candidate.is_file():
                return candidate
        return None

    def test_real_config_loads(self, real_config_path: Path | None) -> None:
        if real_config_path is None:
            pytest.skip("No project-context.yaml found in tree")
        cfg = GrimoireConfig.from_yaml(real_config_path)
        assert cfg.project.name
