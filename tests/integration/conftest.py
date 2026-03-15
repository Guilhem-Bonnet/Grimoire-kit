"""Shared fixtures for integration tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def grimoire_project(tmp_path: Path) -> Path:
    """Create a realistic temporary Grimoire project with config + directories."""
    yaml_content = (
        "project:\n"
        "  name: integration-test\n"
        "  description: Integration test project\n"
        "  type: webapp\n"
        "  stack: [python]\n"
        "  repos:\n"
        "    - name: integration-test\n"
        "      path: .\n"
        "      default_branch: main\n"
        "\n"
        "user:\n"
        "  name: tester\n"
        "  language: Français\n"
        "  skill_level: expert\n"
        "\n"
        "memory:\n"
        "  backend: local\n"
        "\n"
        "agents:\n"
        "  archetype: minimal\n"
        "  custom_agents: []\n"
        "\n"
        "installed_archetypes: []\n"
    )
    (tmp_path / "project-context.yaml").write_text(yaml_content, encoding="utf-8")
    (tmp_path / "_grimoire" / "_memory").mkdir(parents=True)
    (tmp_path / "_grimoire-output").mkdir()
    return tmp_path
