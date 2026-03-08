"""Tests for grimoire.core.project — GrimoireProject central class."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.core.exceptions import GrimoireProjectError
from grimoire.core.project import GrimoireProject

# ── Helpers ───────────────────────────────────────────────────────────────────

_MINIMAL_YAML = """\
project:
  name: "test-project"
  type: "webapp"
  stack: ["python", "docker"]
  repos:
    - name: test-project
      path: "."

user:
  name: "Guilhem"
  language: "Français"
  skill_level: "expert"

memory:
  backend: "local"

agents:
  archetype: "minimal"
  custom_agents: ["my-custom-agent"]
"""


@pytest.fixture()
def project_dir(tmp_path: Path) -> Path:
    """Create a fully initialised project directory."""
    (tmp_path / "project-context.yaml").write_text(_MINIMAL_YAML)
    (tmp_path / "_grimoire").mkdir()
    (tmp_path / "_grimoire" / "_memory").mkdir()
    (tmp_path / "_grimoire-output").mkdir()
    # Deployed agents
    agents_dir = tmp_path / "_grimoire" / "agents"
    agents_dir.mkdir()
    (agents_dir / "architect.md").write_text("# Architect\n")
    (agents_dir / "developer.md").write_text("# Developer\n")
    # Source files for context counting
    (tmp_path / "main.py").write_text("print('hello')")
    (tmp_path / "README.md").write_text("# Project")
    (tmp_path / "src").mkdir()
    return tmp_path


@pytest.fixture()
def project(project_dir: Path) -> GrimoireProject:
    return GrimoireProject(project_dir)


# ── Construction tests ────────────────────────────────────────────────────────


class TestConstruction:
    def test_valid_project(self, project: GrimoireProject, project_dir: Path) -> None:
        assert project.root == project_dir
        assert project.config.project.name == "test-project"

    def test_no_config_strict(self, tmp_path: Path) -> None:
        with pytest.raises(GrimoireProjectError, match="Not a Grimoire project"):
            GrimoireProject(tmp_path)

    def test_no_config_lenient(self, tmp_path: Path) -> None:
        p = GrimoireProject(tmp_path, strict=False)
        assert not p.is_initialized()

    def test_invalid_config_strict(self, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text("invalid: true\n")
        with pytest.raises(GrimoireProjectError, match="Invalid project config"):
            GrimoireProject(tmp_path)

    def test_invalid_config_lenient(self, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text("invalid: true\n")
        p = GrimoireProject(tmp_path, strict=False)
        assert p._config is None

    def test_config_property_raises_if_none(self, tmp_path: Path) -> None:
        p = GrimoireProject(tmp_path, strict=False)
        with pytest.raises(GrimoireProjectError, match="not loaded"):
            _ = p.config


# ── Properties ────────────────────────────────────────────────────────────────


class TestProperties:
    def test_root(self, project: GrimoireProject, project_dir: Path) -> None:
        assert project.root == project_dir

    def test_grimoire_dir(self, project: GrimoireProject, project_dir: Path) -> None:
        assert project.grimoire_dir == project_dir / "_grimoire"

    def test_config_path(self, project: GrimoireProject, project_dir: Path) -> None:
        assert project.config_path == project_dir / "project-context.yaml"

    def test_resolver(self, project: GrimoireProject, project_dir: Path) -> None:
        assert project.resolver.root == project_dir


# ── is_initialized ────────────────────────────────────────────────────────────


class TestIsInitialized:
    def test_initialized(self, project: GrimoireProject) -> None:
        assert project.is_initialized()

    def test_not_initialized_no_grimoire_dir(self, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text(_MINIMAL_YAML)
        p = GrimoireProject(tmp_path, strict=False)
        assert not p.is_initialized()

    def test_not_initialized_no_config(self, tmp_path: Path) -> None:
        (tmp_path / "_grimoire").mkdir()
        p = GrimoireProject(tmp_path, strict=False)
        assert not p.is_initialized()


# ── agents() ──────────────────────────────────────────────────────────────────


class TestAgents:
    def test_lists_deployed_agents(self, project: GrimoireProject) -> None:
        agents = project.agents()
        ids = [a.id for a in agents]
        assert "architect" in ids
        assert "developer" in ids

    def test_includes_custom_agents(self, project: GrimoireProject) -> None:
        agents = project.agents()
        ids = [a.id for a in agents]
        assert "my-custom-agent" in ids

    def test_agent_info_fields(self, project: GrimoireProject) -> None:
        agents = project.agents()
        arch = next(a for a in agents if a.id == "architect")
        assert arch.source == "local"
        assert arch.path.name == "architect.md"
        assert arch.name == "Architect"

    def test_custom_agent_source(self, project: GrimoireProject) -> None:
        agents = project.agents()
        custom = next(a for a in agents if a.id == "my-custom-agent")
        assert custom.source == "custom"

    def test_no_agents_dir(self, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text(_MINIMAL_YAML)
        (tmp_path / "_grimoire").mkdir()
        p = GrimoireProject(tmp_path, strict=False)
        agents = p.agents()
        # Should still include custom agents from config
        assert len(agents) == 1
        assert agents[0].id == "my-custom-agent"

    def test_no_duplicate_agents(self, project_dir: Path) -> None:
        """If agent is both deployed and in custom_agents, it shouldn't duplicate."""
        yaml_with_deployed = _MINIMAL_YAML.replace(
            'custom_agents: ["my-custom-agent"]',
            'custom_agents: ["architect"]',
        )
        (project_dir / "project-context.yaml").write_text(yaml_with_deployed)
        p = GrimoireProject(project_dir)
        agents = p.agents()
        arch_count = sum(1 for a in agents if a.id == "architect")
        assert arch_count == 1


# ── status() ──────────────────────────────────────────────────────────────────


class TestStatus:
    def test_initialized_status(self, project: GrimoireProject) -> None:
        s = project.status()
        assert s.initialized is True
        assert s.config_valid is True
        assert s.agents_count >= 2
        assert s.memory_backend == "local"
        assert s.archetype == "minimal"
        assert "_grimoire" in s.directories_ok

    def test_missing_dirs(self, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text(_MINIMAL_YAML)
        p = GrimoireProject(tmp_path, strict=False)
        s = p.status()
        assert "_grimoire" in s.directories_missing

    def test_custom_agents_count(self, project: GrimoireProject) -> None:
        s = project.status()
        assert s.custom_agents_count == 1


# ── context() ─────────────────────────────────────────────────────────────────


class TestContext:
    def test_context_fields(self, project: GrimoireProject) -> None:
        ctx = project.context()
        assert ctx.name == "test-project"
        assert ctx.project_type == "webapp"
        assert "python" in ctx.stack
        assert ctx.user_name == "Guilhem"
        assert ctx.language == "Français"
        assert ctx.archetype == "minimal"

    def test_context_file_count(self, project: GrimoireProject) -> None:
        ctx = project.context()
        # main.py + README.md + project-context.yaml = 3 files
        assert ctx.file_count >= 2

    def test_context_directory_count(self, project: GrimoireProject) -> None:
        ctx = project.context()
        # src/ = 1 directory (hidden + _grimoire excluded)
        assert ctx.directory_count >= 1

    def test_context_requires_config(self, tmp_path: Path) -> None:
        p = GrimoireProject(tmp_path, strict=False)
        with pytest.raises(GrimoireProjectError):
            p.context()
