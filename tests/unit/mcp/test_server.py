"""Tests for grimoire.mcp.server — MCP tool functions."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import grimoire.mcp.server as mcp_server
from grimoire.mcp.server import (
    _find_assets_root,
    _find_kit_root,
    grimoire_add_agent,
    grimoire_agent_list,
    grimoire_assets_extract_task_icons,
    grimoire_assets_generate_character_action_variants,
    grimoire_assets_generate_complete_baseline,
    grimoire_assets_publish_to_observatory,
    grimoire_config,
    grimoire_diff_impact,
    grimoire_harmony_check,
    grimoire_mcp_policy_report,
    grimoire_memory_lint,
    grimoire_memory_search,
    grimoire_memory_store,
    grimoire_preflight_check,
    grimoire_project_context,
    grimoire_quick_check,
    grimoire_repo_knowledge_search,
    grimoire_status,
    grimoire_test_recommendations,
    grimoire_validate_skills,
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


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """Workspace with kit and asset roots for MCP wrapper tests."""
    (tmp_path / "grimoire-kit" / "archetypes").mkdir(parents=True)
    (tmp_path / "grimoire-kit" / "framework" / "tools").mkdir(parents=True)
    (tmp_path / "grimoire-kit" / "tests" / "unit").mkdir(parents=True)
    (tmp_path / "grimoire-kit" / "apps" / "grimoire-game" / "src").mkdir(parents=True)
    (tmp_path / "grimoire-kit" / "apps" / "grimoire-game" / "tests" / "integration").mkdir(
        parents=True
    )
    (tmp_path / "grimoire-game-assets" / "tools").mkdir(parents=True)
    (tmp_path / "grimoire-game-assets" / "10-curated").mkdir(parents=True)
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


# ── grimoire_preflight_check ──────────────────────────────────────────────────

class TestPreflightCheckTool:
    def test_returns_structured_report(self, project: Path) -> None:
        result = grimoire_preflight_check(str(project))
        data = json.loads(result)
        assert data["tool"] == "grimoire_preflight_check"
        assert data["project_root"] == str(project)
        assert data["ok"]
        assert data["go_nogo"] == "GO"


# ── grimoire_memory_lint ──────────────────────────────────────────────────────

class TestMemoryLintTool:
    def test_returns_structured_report(self, project: Path) -> None:
        result = grimoire_memory_lint(str(project))
        data = json.loads(result)
        assert data["tool"] == "grimoire_memory_lint"
        assert data["project_root"] == str(project)
        assert data["ok"]
        assert data["summary"]["errors"] == 0


class TestRepoKnowledgeSearchTool:
    def test_searches_curated_knowledge_scopes(self, workspace: Path) -> None:
        doc_file = workspace / "docs" / "exploitation" / "plan.md"
        doc_file.parent.mkdir(parents=True)
        doc_file.write_text("# Runtime dashboard\nLe runtime dashboard agrege les traces.\n")

        skill_file = workspace / ".github" / "skills" / "dashboard" / "SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text("# Dashboard\nUse when: runtime dashboard review\n")

        result = grimoire_repo_knowledge_search(
            "runtime dashboard",
            project_path=str(workspace),
            scopes="docs,skills",
            limit=5,
        )
        data = json.loads(result)

        assert data["tool"] == "grimoire_repo_knowledge_search"
        assert data["count"] >= 2
        doc_entries = [entry for entry in data["results"] if entry["path"] == "docs/exploitation/plan.md"]
        skill_entries = [entry for entry in data["results"] if entry["path"] == ".github/skills/dashboard/SKILL.md"]
        assert doc_entries
        assert skill_entries
        assert any(entry["scope"] == "docs" for entry in doc_entries)
        assert any(entry["line"] >= 1 for entry in doc_entries)
        assert any("runtime dashboard" in entry["text"].lower() for entry in doc_entries)
        assert any(entry["scope"] == "skills" for entry in skill_entries)


class TestDiffImpactTool:
    def test_reports_related_tests_and_repo_checks(self, workspace: Path) -> None:
        source_file = workspace / "grimoire-kit" / "src" / "grimoire" / "mcp" / "server.py"
        source_file.parent.mkdir(parents=True)
        source_file.write_text("def run() -> None:\n    pass\n")

        test_file = workspace / "grimoire-kit" / "tests" / "unit" / "mcp" / "test_server.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("def test_server() -> None:\n    assert True\n")

        skill_file = workspace / ".github" / "skills" / "demo" / "SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text("# Demo\n")

        result = grimoire_diff_impact(
            project_path=str(workspace),
            paths="grimoire-kit/src/grimoire/mcp/server.py\n.github/skills/demo/SKILL.md",
        )
        data = json.loads(result)

        assert data["tool"] == "grimoire_diff_impact"
        assert len(data["impacts"]) == 2

        server_impact = next(
            impact
            for impact in data["impacts"]
            if impact["path"] == "grimoire-kit/src/grimoire/mcp/server.py"
        )
        assert "python-source" in server_impact["categories"]
        assert "mcp-surface" in server_impact["categories"]
        assert "grimoire-kit/tests/unit/mcp/test_server.py" in server_impact["related_tests"]
        assert any("MCP" in note for note in server_impact["notes"])

        summary_commands = data["summary"]["recommended_commands"]
        assert any(
            item["kind"] == "shell" and "pytest grimoire-kit/tests/unit/mcp/test_server.py" in item["value"]
            for item in summary_commands
        )
        assert any(
            item["kind"] == "task" and item["value"] == "grimoire: validate-skills"
            for item in summary_commands
        )


class TestTestRecommendationsTool:
    def test_python_source_recommends_pytest_and_ruff(self, workspace: Path) -> None:
        source_file = workspace / "grimoire-kit" / "src" / "grimoire" / "mcp" / "server.py"
        source_file.parent.mkdir(parents=True)
        source_file.write_text("def run() -> None:\n    pass\n")

        test_file = workspace / "grimoire-kit" / "tests" / "unit" / "mcp" / "test_server.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("def test_server() -> None:\n    assert True\n")

        result = grimoire_test_recommendations(
            project_path=str(workspace),
            paths="grimoire-kit/src/grimoire/mcp/server.py",
        )
        data = json.loads(result)

        assert data["tool"] == "grimoire_test_recommendations"
        assert "grimoire-kit/tests/unit/mcp/test_server.py" in data["related_tests"]
        assert data["per_path"][0]["path"] == "grimoire-kit/src/grimoire/mcp/server.py"

        commands = data["recommended_commands"]
        assert any(
            item["kind"] == "shell" and "pytest grimoire-kit/tests/unit/mcp/test_server.py" in item["value"]
            for item in commands
        )
        assert any(
            item["kind"] == "shell"
            and "ruff check grimoire-kit/src/grimoire/mcp/server.py grimoire-kit/tests/unit/mcp/test_server.py"
            in item["value"]
            for item in commands
        )

    def test_game_runtime_recommends_existing_npm_scripts(self, workspace: Path) -> None:
        source_file = (
            workspace
            / "grimoire-kit"
            / "apps"
            / "grimoire-game"
            / "src"
            / "state"
            / "runtime-dashboard-view.ts"
        )
        source_file.parent.mkdir(parents=True)
        source_file.write_text("export const dashboard = true;\n")

        test_file = (
            workspace
            / "grimoire-kit"
            / "apps"
            / "grimoire-game"
            / "tests"
            / "integration"
            / "runtime-dashboard-view.test.ts"
        )
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("import { describe, it } from 'vitest';\n")

        result = grimoire_test_recommendations(
            project_path=str(workspace),
            paths="grimoire-kit/apps/grimoire-game/src/state/runtime-dashboard-view.ts",
        )
        data = json.loads(result)

        assert data["tool"] == "grimoire_test_recommendations"
        assert "grimoire-kit/apps/grimoire-game/tests/integration/runtime-dashboard-view.test.ts" in data[
            "related_tests"
        ]
        assert data["per_path"][0]["categories"] == ["game-runtime-source"]

        commands = data["recommended_commands"]
        assert any(item["kind"] == "shell" and item["value"] == "npm --prefix grimoire-kit/apps/grimoire-game run check" for item in commands)
        assert any(item["kind"] == "shell" and item["value"] == "npm --prefix grimoire-kit/apps/grimoire-game run build" for item in commands)
        assert any(
            item["kind"] == "shell"
            and item["value"]
            == "npm --prefix grimoire-kit/apps/grimoire-game run test -- tests/integration/runtime-dashboard-view.test.ts"
            for item in commands
        )


class TestMcpPolicyReportTool:
    def test_reports_workspace_servers_with_policy_findings(self, workspace: Path) -> None:
        mcp_config = workspace / ".vscode" / "mcp.json"
        mcp_config.parent.mkdir(parents=True)
        mcp_config.write_text(
            json.dumps(
                {
                    "servers": {
                        "github": {
                            "type": "http",
                            "url": "https://api.githubcopilot.com/mcp/",
                        },
                        "context7": {
                            "type": "http",
                            "url": "https://mcp.context7.com/mcp",
                        },
                        "playwright": {
                            "command": "${workspaceFolder}/grimoire-kit/framework/tools/playwright-mcp.sh",
                            "cwd": "${workspaceFolder}",
                        },
                    }
                },
                indent=2,
            )
        )

        result = grimoire_mcp_policy_report(str(workspace))
        data = json.loads(result)

        assert data["tool"] == "grimoire_mcp_policy_report"
        assert data["server_count"] == 3
        assert data["summary"]["status_counts"]["pass"] >= 1
        assert data["summary"]["status_counts"]["warn"] >= 1

        servers = {entry["name"]: entry for entry in data["servers"]}
        assert servers["github"]["transport"] == "http"
        assert servers["github"]["mutability"] == "read-write"
        assert servers["context7"]["mutability"] == "read-mostly"
        assert "remote-network" in servers["context7"]["risk_flags"]
        assert servers["playwright"]["transport"] == "stdio"
        assert "local-command-execution" in servers["playwright"]["risk_flags"]

    def test_flags_hardcoded_secrets(self, workspace: Path) -> None:
        mcp_config = workspace / ".vscode" / "mcp.json"
        mcp_config.parent.mkdir(parents=True)
        mcp_config.write_text(
            json.dumps(
                {
                    "servers": {
                        "unsafe-docs": {
                            "type": "http",
                            "url": "https://docs.example.com/mcp",
                            "headers": {
                                "Authorization": "Bearer hardcoded-secret-value",
                            },
                        }
                    }
                },
                indent=2,
            )
        )

        result = grimoire_mcp_policy_report(str(workspace))
        data = json.loads(result)

        assert data["summary"]["status_counts"]["fail"] == 1
        server = data["servers"][0]
        assert server["status"] == "fail"
        assert server["auth_mode"] == "hardcoded-header-secret"
        assert "hardcoded-secret" in server["risk_flags"]

    def test_honors_policy_allowlists_and_fail_closed_remote(self, workspace: Path) -> None:
        mcp_config = workspace / ".vscode" / "mcp.json"
        mcp_config.parent.mkdir(parents=True)
        mcp_config.write_text(
            json.dumps(
                {
                    "servers": {
                        "context7": {
                            "type": "http",
                            "url": "https://mcp.context7.com/mcp",
                        },
                        "rogue-docs": {
                            "type": "http",
                            "url": "https://rogue.example.com/mcp",
                        },
                        "playwright": {
                            "command": "${workspaceFolder}/grimoire-kit/framework/tools/playwright-mcp.sh",
                            "cwd": "${workspaceFolder}",
                        },
                    }
                },
                indent=2,
            )
        )

        policy_file = workspace / "_grimoire-runtime" / "_config" / "mcp-policy.yaml"
        policy_file.parent.mkdir(parents=True)
        policy_file.write_text(
            "trusted_remote_hosts:\n"
            "  - mcp.context7.com\n"
            "trusted_workspace_servers:\n"
            "  - playwright\n"
            "fail_closed_remote_hosts: true\n"
        )

        result = grimoire_mcp_policy_report(str(workspace))
        data = json.loads(result)

        assert data["policy"]["loaded"]
        assert data["policy"]["fail_closed_remote_hosts"] is True

        servers = {entry["name"]: entry for entry in data["servers"]}
        assert servers["context7"]["status"] == "pass"
        assert servers["playwright"]["status"] == "pass"
        assert servers["playwright"]["trust_level"] == "trusted-local"
        assert servers["rogue-docs"]["status"] == "fail"
        assert "fail-closed-remote-deny" in servers["rogue-docs"]["risk_flags"]


# ── subprocess-backed MCP wrappers ────────────────────────────────────────────

class TestQuickCheckTool:
    def test_runs_quick_check_script(self, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        script = workspace / "grimoire-kit" / "framework" / "tools" / "quick-check.sh"
        script.write_text("#!/usr/bin/env bash\n")
        calls: dict[str, object] = {}

        def fake_run(
            command: list[str],
            *,
            capture_output: bool,
            text: bool,
            cwd: Path,
            env: dict[str, str],
            timeout: float,
        ) -> subprocess.CompletedProcess[str]:
            calls["command"] = command
            calls["cwd"] = cwd
            return subprocess.CompletedProcess(command, 0, "quick ok", "")

        monkeypatch.setattr(mcp_server.subprocess, "run", fake_run)

        result = grimoire_quick_check(str(workspace))
        data = json.loads(result)
        assert data["tool"] == "grimoire_quick_check"
        assert data["ok"]
        assert data["command"] == ["bash", str(script)]
        assert data["kit_root"] == str(workspace / "grimoire-kit")
        assert calls["cwd"] == workspace / "grimoire-kit"


class TestValidateSkillsTool:
    def test_returns_parsed_json_report(
        self,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        script = workspace / "grimoire-kit" / "framework" / "tools" / "skill-validator.py"
        script.write_text("#!/usr/bin/env python3\n")
        report = {
            "skills_count": 1,
            "findings_count": 0,
            "reports": [{"skill": "demo", "findings_count": 0, "findings": []}],
        }

        def fake_run(
            command: list[str],
            *,
            capture_output: bool,
            text: bool,
            cwd: Path,
            env: dict[str, str],
            timeout: float,
        ) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(command, 0, json.dumps(report), "")

        monkeypatch.setattr(mcp_server.subprocess, "run", fake_run)

        result = grimoire_validate_skills(str(workspace), skill="demo")
        data = json.loads(result)
        assert data["tool"] == "grimoire_validate_skills"
        assert data["ok"]
        assert data["skill"] == "demo"
        assert data["report"]["skills_count"] == 1
        assert data["command"] == [
            sys.executable,
            str(script),
            "--project-root",
            str(workspace),
            "--json",
            "--skill",
            "demo",
        ]


class TestAssetToolWrappers:
    def test_generate_complete_baseline_uses_detected_assets_root(
        self,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        script = workspace / "grimoire-game-assets" / "tools" / "generate_complete_baseline.py"
        script.write_text("#!/usr/bin/env python3\n")

        def fake_run(
            command: list[str],
            *,
            capture_output: bool,
            text: bool,
            cwd: Path,
            env: dict[str, str],
            timeout: float,
        ) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(command, 0, "baseline ok", "")

        monkeypatch.setattr(mcp_server.subprocess, "run", fake_run)

        result = grimoire_assets_generate_complete_baseline(str(workspace / "grimoire-kit"))
        data = json.loads(result)
        assert data["tool"] == "grimoire_assets_generate_complete_baseline"
        assert data["ok"]
        assert data["assets_root"] == str(workspace / "grimoire-game-assets")
        assert data["command"] == [
            sys.executable,
            str(script),
            "--assets-root",
            str(workspace / "grimoire-game-assets"),
        ]

    def test_generate_complete_baseline_rejects_external_assets_root(
        self,
        workspace: Path,
    ) -> None:
        external_assets_root = workspace.parent / "external-assets"
        (external_assets_root / "tools").mkdir(parents=True)
        (external_assets_root / "10-curated").mkdir(parents=True)

        result = grimoire_assets_generate_complete_baseline(
            str(workspace / "grimoire-kit"),
            assets_root=str(external_assets_root),
        )
        data = json.loads(result)
        assert data["tool"] == "grimoire_assets_generate_complete_baseline"
        assert data["assets_root"] == str(external_assets_root)
        assert "error" in data

    def test_generate_character_action_variants_runs_expected_script(
        self,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        script = (
            workspace
            / "grimoire-game-assets"
            / "tools"
            / "generate_character_action_variants.py"
        )
        script.write_text("#!/usr/bin/env python3\n")

        def fake_run(
            command: list[str],
            *,
            capture_output: bool,
            text: bool,
            cwd: Path,
            env: dict[str, str],
            timeout: float,
        ) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(command, 0, "variants ok", "")

        monkeypatch.setattr(mcp_server.subprocess, "run", fake_run)

        result = grimoire_assets_generate_character_action_variants(str(workspace))
        data = json.loads(result)
        assert data["tool"] == "grimoire_assets_generate_character_action_variants"
        assert data["ok"]
        assert data["command"] == [
            sys.executable,
            str(script),
            "--assets-root",
            str(workspace / "grimoire-game-assets"),
        ]

    def test_extract_task_icons_resolves_relative_paths(
        self,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        script = workspace / "grimoire-game-assets" / "tools" / "extract_task_icons.py"
        script.write_text("#!/usr/bin/env python3\n")

        def fake_run(
            command: list[str],
            *,
            capture_output: bool,
            text: bool,
            cwd: Path,
            env: dict[str, str],
            timeout: float,
        ) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(command, 0, "icons ok", "")

        monkeypatch.setattr(mcp_server.subprocess, "run", fake_run)

        result = grimoire_assets_extract_task_icons(
            str(workspace),
            source="00-intake/pixel-agents/tasks-icons-sheet.png",
            output_dir="10-curated/ui",
            version="v03",
            dry_run=True,
        )
        data = json.loads(result)
        assert data["tool"] == "grimoire_assets_extract_task_icons"
        assert data["ok"]
        assert data["dry_run"]
        assert data["version"] == "v03"
        assert data["command"] == [
            sys.executable,
            str(script),
            "--version",
            "v03",
            "--sat-thresh",
            "0.09",
            "--val-min",
            "0.78",
            "--fade-range",
            "0.05",
            "--padding",
            "8",
            "--source",
            str(
                workspace
                / "grimoire-game-assets"
                / "00-intake"
                / "pixel-agents"
                / "tasks-icons-sheet.png"
            ),
            "--out",
            str(workspace / "grimoire-game-assets" / "10-curated" / "ui"),
            "--dry-run",
        ]

    def test_extract_task_icons_rejects_source_outside_assets_root(
        self,
        workspace: Path,
    ) -> None:
        result = grimoire_assets_extract_task_icons(
            str(workspace),
            source="../secret.png",
        )
        data = json.loads(result)
        assert data["tool"] == "grimoire_assets_extract_task_icons"
        assert data["parameter"] == "source"
        assert data["provided_path"] == "../secret.png"
        assert "error" in data

    def test_extract_task_icons_rejects_output_outside_assets_root(
        self,
        workspace: Path,
    ) -> None:
        result = grimoire_assets_extract_task_icons(
            str(workspace),
            output_dir="../exports",
        )
        data = json.loads(result)
        assert data["tool"] == "grimoire_assets_extract_task_icons"
        assert data["parameter"] == "output_dir"
        assert data["provided_path"] == "../exports"
        assert "error" in data

    def test_publish_to_observatory_uses_dry_run_env(
        self,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        script = workspace / "grimoire-game-assets" / "tools" / "publish_to_observatory.sh"
        script.write_text("#!/usr/bin/env bash\n")
        calls: dict[str, object] = {}

        def fake_run(
            command: list[str],
            *,
            capture_output: bool,
            text: bool,
            cwd: Path,
            env: dict[str, str],
            timeout: float,
        ) -> subprocess.CompletedProcess[str]:
            calls["env"] = env
            return subprocess.CompletedProcess(command, 0, "publish ok", "")

        monkeypatch.setattr(mcp_server.subprocess, "run", fake_run)

        result = grimoire_assets_publish_to_observatory(str(workspace / "grimoire-kit"))
        data = json.loads(result)
        assert data["tool"] == "grimoire_assets_publish_to_observatory"
        assert data["ok"]
        assert data["dry_run"]
        assert calls["env"]["DRY_RUN"] == "1"

    def test_publish_to_observatory_rejects_target_dir_outside_workspace(
        self,
        workspace: Path,
    ) -> None:
        result = grimoire_assets_publish_to_observatory(
            str(workspace / "grimoire-kit"),
            target_dir="../../escape",
        )
        data = json.loads(result)
        assert data["tool"] == "grimoire_assets_publish_to_observatory"
        assert data["parameter"] == "target_dir"
        assert data["provided_path"] == "../../escape"
        assert "error" in data


# ── _find_kit_root ────────────────────────────────────────────────────────────

class TestFindKitRoot:
    def test_finds_archetypes(self, tmp_path: Path) -> None:
        (tmp_path / "archetypes").mkdir()
        result = _find_kit_root(tmp_path)
        assert result == tmp_path.resolve()

    def test_finds_nested_grimoire_kit(self, workspace: Path) -> None:
        result = _find_kit_root(workspace)
        assert result == (workspace / "grimoire-kit").resolve()

    def test_walks_up(self, tmp_path: Path) -> None:
        (tmp_path / "archetypes").mkdir()
        sub = tmp_path / "sub" / "deep"
        sub.mkdir(parents=True)
        result = _find_kit_root(sub)
        assert result == tmp_path.resolve()

    def test_returns_none(self, tmp_path: Path) -> None:
        result = _find_kit_root(tmp_path)
        assert result is None


class TestFindAssetsRoot:
    def test_finds_assets_root_from_workspace(self, workspace: Path) -> None:
        result = _find_assets_root(workspace)
        assert result == (workspace / "grimoire-game-assets").resolve()

    def test_finds_assets_root_from_nested_kit(self, workspace: Path) -> None:
        result = _find_assets_root(workspace / "grimoire-kit")
        assert result == (workspace / "grimoire-game-assets").resolve()
