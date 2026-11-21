"""Configuration MCP de Copilot : `.vscode/mcp.json` (épique #1280, lot P2-2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.cli.doctor_copilot import check_copilot_mcp
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts.capabilities import GovernanceFunction, ParityLevel, function_states
from grimoire.hosts.collect import build_surface
from grimoire.hosts.emitters import emitter_for
from grimoire.hosts.emitters.base import apply_plans
from grimoire.hosts.emitters.copilot_mcp import server_entry, workspace_command
from grimoire.hosts.surface import McpServerSpec

pytestmark = pytest.mark.covers("host:github-copilot", level="integration")

MCP = Path(".vscode/mcp.json")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    return tmp_path


def _sync(root: Path, host: HostId = HostId.GITHUB_COPILOT, preserve: frozenset[str] = frozenset()):
    emitter = emitter_for(host)
    return apply_plans([emitter.plan(build_surface(root), root)], root, preserve=preserve)[0]


def _servers(root: Path) -> dict:
    return json.loads((root / MCP).read_text(encoding="utf-8"))["servers"]


def test_new_file_declares_the_kit_server_in_the_vscode_shape(project: Path) -> None:
    result = _sync(project)
    assert result.ok, result.conflicts
    entry = _servers(project)["grimoire"]
    assert entry["type"] == "stdio"
    assert entry["command"].endswith("grimoire-mcp")
    assert "${CLAUDE_PROJECT_DIR" not in entry["command"]
    assert "mcpServers" not in json.loads((project / MCP).read_text(encoding="utf-8"))


def test_existing_servers_and_keys_are_preserved(project: Path) -> None:
    (project / ".vscode").mkdir()
    original = {
        "inputs": [{"id": "k", "type": "promptString", "description": "x"}],
        "servers": {
            "mine": {"type": "http", "url": "https://exemple.test/mcp"},
            "grimoire": {"command": "${workspaceFolder}/.venv/bin/python", "args": ["-m", "grimoire.mcp.server"]},
        },
    }
    (project / MCP).write_text(json.dumps(original), encoding="utf-8")
    _sync(project)
    data = json.loads((project / MCP).read_text(encoding="utf-8"))
    assert data == original  # ni retrait, ni réécriture d'une entrée écrite à la main


def test_missing_server_is_added_next_to_the_users(project: Path) -> None:
    (project / ".vscode").mkdir()
    (project / MCP).write_text(json.dumps({"servers": {"mine": {"command": "x"}}}), encoding="utf-8")
    _sync(project)
    servers = _servers(project)
    assert servers["mine"] == {"command": "x"}
    assert "grimoire" in servers


def test_second_sync_is_idempotent(project: Path) -> None:
    _sync(project)
    first = (project / MCP).read_text(encoding="utf-8")
    again = _sync(project)
    assert (project / MCP).read_text(encoding="utf-8") == first
    assert ".vscode/mcp.json" in again.unchanged


def test_preserved_file_is_never_written(project: Path) -> None:
    result = _sync(project, preserve=frozenset({".vscode/mcp.json"}))
    assert not (project / MCP).exists()
    assert ".vscode/mcp.json" in result.preserved


def test_invalid_json_is_a_conflict_not_an_overwrite(project: Path) -> None:
    (project / ".vscode").mkdir()
    (project / MCP).write_text("{ pas du json", encoding="utf-8")
    result = _sync(project)
    assert (project / MCP).read_text(encoding="utf-8") == "{ pas du json"
    assert not result.ok


def test_remote_server_and_workspace_variable() -> None:
    assert server_entry(McpServerSpec(name="r", command="", url="https://h/mcp")) == {
        "type": "http",
        "url": "https://h/mcp",
    }
    assert (
        workspace_command("${CLAUDE_PROJECT_DIR:-.}/.venv/bin/grimoire-mcp")
        == "${workspaceFolder}/.venv/bin/grimoire-mcp"
    )
    assert workspace_command("/abs/grimoire-mcp") == "/abs/grimoire-mcp"


def test_matrix_state_is_rich_and_cloud_limit_is_a_declared_degradation(project: Path) -> None:
    assert function_states(HostId.GITHUB_COPILOT)[GovernanceFunction.MCP_SERVER].level is ParityLevel.RICH
    # Les hôtes en prose restent dégradés : rien d'écrit pour eux.
    assert function_states(HostId.CODEX)[GovernanceFunction.MCP_SERVER].level is ParityLevel.DEGRADED
    plan = emitter_for(HostId.GITHUB_COPILOT).plan(build_surface(project), project)
    assert [m.relpath for m in plan.merges] == [MCP]
    assert any(d.surface == "mcp cloud agent" for d in plan.degradations)


def test_claude_code_mcp_json_is_untouched_by_copilot_and_claude_sync(project: Path) -> None:
    seed = {"mcpServers": {"grimoire": {"command": "grimoire-mcp", "args": [], "env": {}}}}
    (project / ".mcp.json").write_text(json.dumps(seed), encoding="utf-8")
    _sync(project)
    _sync(project, HostId.CLAUDE_CODE_CLI)
    assert json.loads((project / ".mcp.json").read_text(encoding="utf-8")) == seed
    plan = emitter_for(HostId.CLAUDE_CODE_CLI).plan(build_surface(project), project)
    assert MCP not in [m.relpath for m in plan.merges]


class TestDoctor:
    def test_absent_config_warns_when_copilot_is_detected(self, project: Path) -> None:
        (project / ".github").mkdir()
        (project / ".github" / "copilot-instructions.md").write_text("x", encoding="utf-8")
        (check,) = check_copilot_mcp(project)
        assert check.level == "warn"
        assert ".vscode/mcp.json" in check.detail

    def test_emitted_config_is_ok_and_a_divergent_one_is_informational(self, project: Path) -> None:
        _sync(project)
        (project / ".github" / "copilot-instructions.md").write_text("x", encoding="utf-8")
        (check,) = check_copilot_mcp(project)
        assert check.level == "ok"
        data = json.loads((project / MCP).read_text(encoding="utf-8"))
        data["servers"]["grimoire"] = {"command": "autre"}
        (project / MCP).write_text(json.dumps(data), encoding="utf-8")
        (check,) = check_copilot_mcp(project)
        assert check.level == "info"

    def test_no_check_without_copilot(self, tmp_path: Path) -> None:
        assert check_copilot_mcp(tmp_path) == []
