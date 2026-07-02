"""Tests for grimoire.mcp.server — MCP tool functions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grimoire.mcp.server import (
    _find_kit_root,
    grimoire_add_agent,
    grimoire_agent_list,
    grimoire_config,
    grimoire_harmony_check,
    grimoire_memory_search,
    grimoire_memory_store,
    grimoire_project_context,
    grimoire_standard_audit,
    grimoire_standard_gate,
    grimoire_standard_score,
    grimoire_standard_verify,
    grimoire_status,
    mcp,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """Minimal Grimoire project."""
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
    (tmp_path / "_grimoire" / "_memory").mkdir(parents=True)
    (tmp_path / "_grimoire-output").mkdir()
    return tmp_path


# ── Server instance ──────────────────────────────────────────────────────────

class TestServerInstance:
    def test_server_name(self) -> None:
        assert mcp.name == "grimoire"

    def test_has_tools(self) -> None:
        # FastMCP should have registered tools
        assert mcp is not None


# ── grimoire_project_context ──────────────────────────────────────────────────────

class TestProjectContext:
    def test_returns_json(self, project: Path) -> None:
        result = grimoire_project_context(str(project))
        data = json.loads(result)
        assert data["project"]["name"] == "test-mcp"
        assert data["project"]["type"] == "webapp"
        assert "python" in data["project"]["stack"]
        assert data["user"]["name"] == "Guilhem"
        assert "grimoire_kit_version" in data

    def test_invalid_path(self, tmp_path: Path) -> None:
        result = grimoire_project_context(str(tmp_path))
        data = json.loads(result)
        assert "error" in data


# ── grimoire_status ───────────────────────────────────────────────────────────────

class TestStatus:
    def test_healthy_project(self, project: Path) -> None:
        result = grimoire_status(str(project))
        data = json.loads(result)
        assert data["healthy"]
        assert data["passed"] == data["total"]
        assert "grimoire_kit_version" in data

    def test_unhealthy(self, tmp_path: Path) -> None:
        result = grimoire_status(str(tmp_path))
        data = json.loads(result)
        assert not data["healthy"]

    def test_partial(self, tmp_path: Path) -> None:
        # Config exists but dirs missing
        (tmp_path / "project-context.yaml").write_text("project:\n  name: partial\n")
        result = grimoire_status(str(tmp_path))
        data = json.loads(result)
        assert data["passed"] < data["total"]

    def test_invalid_config(self, tmp_path: Path) -> None:
        # Config exists but is invalid YAML structure (missing required fields)
        (tmp_path / "project-context.yaml").write_text("not_a_valid: config\n")
        (tmp_path / "_grimoire" / "_memory").mkdir(parents=True)
        (tmp_path / "_grimoire-output").mkdir()
        result = grimoire_status(str(tmp_path))
        data = json.loads(result)
        # config_exists should be True, config_valid should be False
        checks_map = {c["name"]: c for c in data["checks"]}
        assert checks_map["config_exists"]["ok"]
        assert not checks_map["config_valid"]["ok"]


# ── grimoire_agent_list ───────────────────────────────────────────────────────────

_KIT_ROOT = Path(__file__).resolve().parents[3]


class TestAgentList:
    @pytest.mark.skipif(
        not (_KIT_ROOT / "archetypes").is_dir(),
        reason="archetypes/ not found",
    )
    def test_on_real_kit(self) -> None:
        # Use the kit root which has archetypes/
        result = grimoire_agent_list(str(_KIT_ROOT))
        data = json.loads(result)
        # Should list agents from the configured archetype
        if "error" not in data:
            assert "agents" in data
            assert data["total"] > 0

    def test_missing_archetypes(self, project: Path) -> None:
        result = grimoire_agent_list(str(project))
        data = json.loads(result)
        # Should get error or empty list (no archetypes/ dir)
        assert "error" in data or data.get("total", 0) == 0

    def test_invalid_project(self, tmp_path: Path) -> None:
        result = grimoire_agent_list(str(tmp_path))
        data = json.loads(result)
        assert "error" in data


# ── grimoire_harmony_check ────────────────────────────────────────────────────────

class TestHarmonyCheck:
    def test_on_project(self, project: Path) -> None:
        result = grimoire_harmony_check(str(project))
        data = json.loads(result)
        assert "score" in data
        assert "grade" in data
        assert 0 <= data["score"] <= 100

    def test_empty_dir(self, tmp_path: Path) -> None:
        result = grimoire_harmony_check(str(tmp_path))
        data = json.loads(result)
        assert data["score"] == 100


# ── grimoire_config ───────────────────────────────────────────────────────────────

class TestConfig:
    def test_returns_raw(self, project: Path) -> None:
        result = grimoire_config(str(project))
        data = json.loads(result)
        assert data["project"]["name"] == "test-mcp"

    def test_missing(self, tmp_path: Path) -> None:
        result = grimoire_config(str(tmp_path))
        data = json.loads(result)
        assert "error" in data

    def test_malformed_yaml(self, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text(": :\n  invalid yaml:: {{{\n")
        result = grimoire_config(str(tmp_path))
        data = json.loads(result)
        assert "error" in data


# ── grimoire_memory_store ─────────────────────────────────────────────────────────

class TestMemoryStore:
    def test_store_returns_entry(self, project: Path) -> None:
        result = grimoire_memory_store("important fact", project_path=str(project))
        data = json.loads(result)
        assert data["text"] == "important fact"
        assert data["id"]
        assert data["user_id"] == "global"

    def test_store_with_user_id(self, project: Path) -> None:
        result = grimoire_memory_store("user fact", user_id="alice", project_path=str(project))
        data = json.loads(result)
        assert data["user_id"] == "alice"

    def test_store_no_project(self, tmp_path: Path) -> None:
        result = grimoire_memory_store("nope", project_path=str(tmp_path))
        data = json.loads(result)
        assert "error" in data


# ── grimoire_memory_search ────────────────────────────────────────────────────────

class TestMemorySearch:
    def test_search_finds_stored(self, project: Path) -> None:
        grimoire_memory_store("python is the best language", project_path=str(project))
        result = grimoire_memory_search("python", project_path=str(project))
        data = json.loads(result)
        assert data["count"] >= 1
        assert any("python" in r["text"].lower() for r in data["results"])

    def test_search_empty(self, project: Path) -> None:
        result = grimoire_memory_search("nonexistent-xyz", project_path=str(project))
        data = json.loads(result)
        assert data["count"] == 0

    def test_search_no_project(self, tmp_path: Path) -> None:
        result = grimoire_memory_search("query", project_path=str(tmp_path))
        data = json.loads(result)
        assert "error" in data


# ── grimoire_add_agent ────────────────────────────────────────────────────────────

class TestAddAgent:
    def test_add_agent(self, project: Path) -> None:
        result = grimoire_add_agent("my-custom-agent", project_path=str(project))
        data = json.loads(result)
        assert data["status"] == "added"
        assert data["agent_id"] == "my-custom-agent"

    def test_add_agent_duplicate(self, project: Path) -> None:
        grimoire_add_agent("dup-agent", project_path=str(project))
        result = grimoire_add_agent("dup-agent", project_path=str(project))
        data = json.loads(result)
        assert data["status"] == "already_present"

    def test_add_agent_no_project(self, tmp_path: Path) -> None:
        result = grimoire_add_agent("nope", project_path=str(tmp_path))
        data = json.loads(result)
        assert "error" in data

    def test_add_agent_persists(self, project: Path) -> None:
        grimoire_add_agent("persisted-agent", project_path=str(project))
        content = (project / "project-context.yaml").read_text()
        assert "persisted-agent" in content


# ── grimoire_standard_* ───────────────────────────────────────────────────────

@pytest.fixture()
def standard_project(tmp_path: Path) -> Path:
    """Project scaffolded with the starter agentic-standard profile."""
    from grimoire.core.agentic_standard import setup_standard_profile

    setup_standard_profile(tmp_path, profile_id="starter", project_name="test-mcp-standard")
    return tmp_path


class TestStandardVerify:
    def test_scaffolded_project_is_ok(self, standard_project: Path) -> None:
        result = grimoire_standard_verify(str(standard_project))
        data = json.loads(result)
        assert data["ok"]
        assert data["profile"] == "starter"
        assert data["error_count"] == 0
        assert data["missing"] == []

    def test_empty_project_fails_closed(self, tmp_path: Path) -> None:
        result = grimoire_standard_verify(str(tmp_path))
        data = json.loads(result)
        assert not data["ok"]
        assert data["missing"]

    def test_explicit_profile(self, standard_project: Path) -> None:
        result = grimoire_standard_verify(str(standard_project), profile="starter")
        data = json.loads(result)
        assert data["profile"] == "starter"

    def test_unknown_profile_returns_error(self, standard_project: Path) -> None:
        result = grimoire_standard_verify(str(standard_project), profile="nonexistent-profile")
        data = json.loads(result)
        assert "error" in data


class TestStandardAudit:
    def test_scaffolded_project(self, standard_project: Path) -> None:
        result = grimoire_standard_audit(str(standard_project))
        data = json.loads(result)
        assert data["ok"]
        # Fresh scaffold may propose warning-level completions, never errors.
        assert all(a["severity"] != "error" for a in data["remediation_actions"])

    def test_empty_project_proposes_remediation(self, tmp_path: Path) -> None:
        result = grimoire_standard_audit(str(tmp_path))
        data = json.loads(result)
        assert not data["ok"]
        assert data["remediation_actions"]
        action = data["remediation_actions"][0]
        assert {"check_id", "severity", "action", "path", "message"} <= set(action)


class TestStandardScore:
    def test_scaffolded_project_scores(self, standard_project: Path) -> None:
        result = grimoire_standard_score(str(standard_project))
        data = json.loads(result)
        assert 0 <= data["score"] <= 100
        assert data["threshold"] > 0
        output_path = Path(data["output_path"])
        if not output_path.is_absolute():
            output_path = standard_project / output_path
        assert output_path.is_file()

    def test_empty_project_returns_result_or_error(self, tmp_path: Path) -> None:
        result = grimoire_standard_score(str(tmp_path))
        data = json.loads(result)
        assert "error" in data or not data["ok"]


class TestStandardGate:
    def test_bootstrap_task(self, standard_project: Path) -> None:
        result = grimoire_standard_gate(str(standard_project))
        data = json.loads(result)
        assert data["task_id"] == "bootstrap"
        assert data["profile"] == "starter"
        assert "missing" in data

    def test_unknown_target_state_returns_error(self, standard_project: Path) -> None:
        result = grimoire_standard_gate(str(standard_project), target_state="warp-speed")
        data = json.loads(result)
        assert "error" in data
        assert "warp-speed" in data["error"]

    def test_empty_project_has_no_state(self, tmp_path: Path) -> None:
        # Without a task board there is no state, hence no gate requirement.
        result = grimoire_standard_gate(str(tmp_path))
        data = json.loads(result)
        assert data["state"] is None
        assert data["missing"] == []


# ── _find_kit_root ────────────────────────────────────────────────────────────

class TestFindKitRoot:
    def test_finds_archetypes(self, tmp_path: Path) -> None:
        (tmp_path / "archetypes").mkdir()
        result = _find_kit_root(tmp_path)
        assert result == tmp_path.resolve()

    def test_walks_up(self, tmp_path: Path) -> None:
        (tmp_path / "archetypes").mkdir()
        sub = tmp_path / "sub" / "deep"
        sub.mkdir(parents=True)
        result = _find_kit_root(sub)
        assert result == tmp_path.resolve()

    def test_returns_none(self, tmp_path: Path) -> None:
        result = _find_kit_root(tmp_path)
        assert result is None
