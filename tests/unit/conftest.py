"""Shared pytest fixtures for Grimoire SDK unit tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.core.config import GrimoireConfig


@pytest.fixture
def minimal_yaml(tmp_path: Path) -> Path:
    """Create a minimal valid ``project-context.yaml`` and return its path."""
    yaml_file = tmp_path / "project-context.yaml"
    yaml_file.write_text(
        "project:\n"
        "  name: test-project\n"
        "  description: Unit test fixture\n"
        "  type: library\n"
        "  stack: [python]\n",
        encoding="utf-8",
    )
    return yaml_file


@pytest.fixture
def sample_config(minimal_yaml: Path) -> GrimoireConfig:
    """Return a :class:`GrimoireConfig` loaded from a minimal YAML fixture."""
    return GrimoireConfig.from_yaml(minimal_yaml)


@pytest.fixture
def grimoire_project(tmp_path: Path, minimal_yaml: Path) -> Path:
    """Create a temporary Grimoire project structure and return its root."""
    (tmp_path / "_grimoire" / "_memory").mkdir(parents=True)
    (tmp_path / "_grimoire-output").mkdir(parents=True)
    return tmp_path
