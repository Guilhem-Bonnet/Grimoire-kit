"""Détection du harness Copilot et contrôles doctor Copilot (#1280, lot P1-5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grimoire.bridges.host import HostBridge
from grimoire.bridges.schemas import HostId
from grimoire.cli.doctor_copilot import check_copilot_hooks
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts.collect import build_surface
from grimoire.hosts.emitters import emitter_for
from grimoire.hosts.emitters.base import apply_plan

pytestmark = pytest.mark.covers("host:github-copilot", level="integration")

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "hosts" / "copilot"
_MARKERS = (
    "GRIMOIRE_HOST_ID",
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CODEX_ENV",
    "CODEX_SANDBOX",
    "COPILOT_AGENT",
    "GITHUB_COPILOT_AGENT",
    "COPILOT_AGENT_PROMPT",
    "CURSOR_AGENT",
    "CURSOR_TRACE_ID",
    "GEMINI_CLI",
    "GEMINI_CLI_VERSION",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _MARKERS:
        monkeypatch.delenv(var, raising=False)


def _payloads(name: str) -> list[dict]:
    data = json.loads((FIXTURES / "copilot-harness" / name).read_text(encoding="utf-8"))
    return [case["input"] for case in data["payloads"]]


class TestDetection:
    @pytest.mark.parametrize("name", ["pretooluse-single.json", "pretooluse-batch.json", "session-start.json"])
    def test_real_copilot_harness_payloads_identify_copilot(self, name: str) -> None:
        for payload in _payloads(name):
            assert HostBridge().detect(payload).host_id == HostId.GITHUB_COPILOT

    def test_snake_case_payload_identifies_nothing(self) -> None:
        # Forme du harness Local ET de Claude Code : la forme seule ne tranche pas.
        payload = {"session_id": "s", "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {}}
        assert HostBridge().detect(payload).host_id == HostId.UNKNOWN

    def test_payload_shape_does_not_override_the_explicit_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GRIMOIRE_HOST_ID", HostId.CODEX.value)
        assert HostBridge().detect({"sessionId": "s", "toolName": "bash"}).host_id == HostId.CODEX

    def test_payload_shape_beats_environment_heuristics(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLAUDECODE", "1")
        assert HostBridge().detect({"sessionId": "s"}).host_id == HostId.GITHUB_COPILOT
        assert HostBridge().detect().host_id == HostId.CLAUDE_CODE_CLI

    def test_undocumented_variables_no_longer_identify_copilot(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("COPILOT_AGENT", "1")
        monkeypatch.setenv("GITHUB_COPILOT_AGENT", "1")
        assert HostBridge().detect().host_id == HostId.UNKNOWN

    def test_cloud_agent_variable_documented_by_github(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("COPILOT_AGENT_PROMPT", "do it")
        assert HostBridge().detect().host_id == HostId.GITHUB_COPILOT


# ── doctor ──────────────────────────────────────────────────────────────────


def _emit_copilot(project: Path) -> None:
    setup_standard_profile(project, profile_id="governed", task_id="bootstrap")
    apply_plan(emitter_for(HostId.GITHUB_COPILOT).plan(build_surface(project), project), project)


def _by_name(project: Path) -> dict[str, object]:
    return {c.name: c for c in check_copilot_hooks(project)}


def _write_claude(project: Path, host: str = "claude-code") -> None:
    settings = project / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    cmd = f"grimoire-hook --host {host} --event PreToolUse"
    entry = {"matcher": "Bash", "hooks": [{"type": "command", "command": cmd, "onFailure": "block"}]}
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [entry]}}), encoding="utf-8")


def test_conforming_files_pass_and_report_the_harness_limits(tmp_path: Path) -> None:
    _emit_copilot(tmp_path)
    checks = _by_name(tmp_path)
    assert checks["copilot_hook_format"].passed
    assert checks["copilot_hook_format"].level == "ok"
    assert "copilot_hooks_doubled" not in checks
    assert "PascalCase" in checks["copilot_harness"].detail


def test_old_format_file_is_flagged_for_regeneration(tmp_path: Path) -> None:
    hooks = tmp_path / ".github" / "hooks"
    hooks.mkdir(parents=True)
    old = {
        "hooks": {
            "PreToolUse": [
                {
                    "type": "command",
                    "command": "grimoire-hook --host copilot --event PreToolUse",
                    "windows": "grimoire-hook.exe --host copilot",
                    "timeout": 10,
                }
            ]
        }
    }
    (hooks / "grimoire-pre-tool-use.json").write_text(json.dumps(old), encoding="utf-8")
    check = _by_name(tmp_path)["copilot_hook_format"]
    assert check.level == "warn"
    assert check.passed  # un avertissement ne fait pas échouer doctor
    assert check.level == "warn"
    assert "no `version: 1`" in check.detail
    assert "no `matcher`" in check.detail
    assert "`timeout` instead of `timeoutSec`" in check.detail
    assert "grimoire up" in check.remedy


def test_guard_without_matcher_is_flagged_even_at_version_1(tmp_path: Path) -> None:
    _emit_copilot(tmp_path)
    path = tmp_path / ".github" / "hooks" / "grimoire-pre-tool-use.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for entry in data["hooks"]["PreToolUse"]:
        entry.pop("matcher", None)
    path.write_text(json.dumps(data), encoding="utf-8")
    check = _by_name(tmp_path)["copilot_hook_format"]
    assert check.level == "warn"
    assert check.passed  # un avertissement ne fait pas échouer doctor
    assert "no `matcher`" in check.detail


def test_foreign_hook_files_are_ignored(tmp_path: Path) -> None:
    hooks = tmp_path / ".github" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "grimoire-maison.json").write_text(
        json.dumps({"hooks": {"PreToolUse": [{"type": "command", "bash": "./maison.sh"}]}}), encoding="utf-8"
    )
    assert check_copilot_hooks(tmp_path) == []


def test_claude_and_copilot_both_wired_is_a_hedged_warning(tmp_path: Path) -> None:
    _emit_copilot(tmp_path)
    _write_claude(tmp_path)
    check = _by_name(tmp_path)["copilot_hooks_doubled"]
    assert check.level == "warn"
    assert check.passed  # un avertissement ne fait pas échouer doctor
    assert check.level == "warn"
    assert "does not say whether identical entries are deduplicated" in check.detail
    assert "--host claude-code" in check.detail


def test_claude_alone_has_no_copilot_finding(tmp_path: Path) -> None:
    _write_claude(tmp_path)
    assert check_copilot_hooks(tmp_path) == []
