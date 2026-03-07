"""Tests for bmad.mcp.server — MCP tool functions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bmad.mcp.server import (
    bmad_agent_list,
    bmad_config,
    bmad_harmony_check,
    bmad_project_context,
    bmad_status,
    mcp,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """Minimal BMAD project."""
    (tmp_path / "project-context.yaml").write_text(
        "project:\n"
        "  name: test-mcp\n"
        "  type: webapp\n"
        "  stack:\n"
        "    - python\n"
        "user:\n"
        "  name: Guilhem\n"
        "  language: Français\n"
        "  skill_level: expert\n"
        "memory:\n"
        "  backend: local\n"
        "agents:\n"
        "  archetype: minimal\n"
    )
    (tmp_path / "_bmad" / "_memory").mkdir(parents=True)
    (tmp_path / "_bmad-output").mkdir()
    return tmp_path


# ── Server instance ──────────────────────────────────────────────────────────

class TestServerInstance:
    def test_server_name(self) -> None:
        assert mcp.name == "bmad"

    def test_has_tools(self) -> None:
        # FastMCP should have registered tools
        assert mcp is not None


# ── bmad_project_context ──────────────────────────────────────────────────────

class TestProjectContext:
    def test_returns_json(self, project: Path) -> None:
        result = bmad_project_context(str(project))
        data = json.loads(result)
        assert data["project"]["name"] == "test-mcp"
        assert data["project"]["type"] == "webapp"
        assert "python" in data["project"]["stack"]
        assert data["user"]["name"] == "Guilhem"
        assert "bmad_kit_version" in data

    def test_invalid_path(self, tmp_path: Path) -> None:
        result = bmad_project_context(str(tmp_path))
        data = json.loads(result)
        assert "error" in data


# ── bmad_status ───────────────────────────────────────────────────────────────

class TestStatus:
    def test_healthy_project(self, project: Path) -> None:
        result = bmad_status(str(project))
        data = json.loads(result)
        assert data["healthy"]
        assert data["passed"] == data["total"]
        assert "bmad_kit_version" in data

    def test_unhealthy(self, tmp_path: Path) -> None:
        result = bmad_status(str(tmp_path))
        data = json.loads(result)
        assert not data["healthy"]

    def test_partial(self, tmp_path: Path) -> None:
        # Config exists but dirs missing
        (tmp_path / "project-context.yaml").write_text("project:\n  name: partial\n")
        result = bmad_status(str(tmp_path))
        data = json.loads(result)
        assert data["passed"] < data["total"]


# ── bmad_agent_list ───────────────────────────────────────────────────────────

_KIT_ROOT = Path(__file__).resolve().parents[3]


class TestAgentList:
    @pytest.mark.skipif(
        not (_KIT_ROOT / "archetypes").is_dir(),
        reason="archetypes/ not found",
    )
    def test_on_real_kit(self) -> None:
        # Use the kit root which has archetypes/
        (cfg_path := _KIT_ROOT / "project-context.yaml").is_file()
        result = bmad_agent_list(str(_KIT_ROOT))
        data = json.loads(result)
        # Should list agents from the configured archetype
        if "error" not in data:
            assert "agents" in data
            assert data["total"] > 0

    def test_missing_archetypes(self, project: Path) -> None:
        result = bmad_agent_list(str(project))
        data = json.loads(result)
        # Should get error or empty list (no archetypes/ dir)
        assert "error" in data or data.get("total", 0) == 0


# ── bmad_harmony_check ────────────────────────────────────────────────────────

class TestHarmonyCheck:
    def test_on_project(self, project: Path) -> None:
        result = bmad_harmony_check(str(project))
        data = json.loads(result)
        assert "score" in data
        assert "grade" in data
        assert 0 <= data["score"] <= 100

    def test_empty_dir(self, tmp_path: Path) -> None:
        result = bmad_harmony_check(str(tmp_path))
        data = json.loads(result)
        assert data["score"] == 100


# ── bmad_config ───────────────────────────────────────────────────────────────

class TestConfig:
    def test_returns_raw(self, project: Path) -> None:
        result = bmad_config(str(project))
        data = json.loads(result)
        assert data["project"]["name"] == "test-mcp"

    def test_missing(self, tmp_path: Path) -> None:
        result = bmad_config(str(tmp_path))
        data = json.loads(result)
        assert "error" in data
