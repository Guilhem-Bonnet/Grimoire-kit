"""Tests for the Host Bridge module."""

from __future__ import annotations

from grimoire.bridges.host import HostBridge
from grimoire.bridges.schemas import (
    CLAUDE_CODE_CLI_MANIFEST,
    CODEX_MANIFEST,
    GITHUB_COPILOT_MANIFEST,
    HostId,
)


def test_detect_with_override():
    bridge = HostBridge(override_host_id=HostId.CLAUDE_CODE_CLI)
    manifest = bridge.detect()
    assert manifest.host_id == HostId.CLAUDE_CODE_CLI
    assert manifest.hooks.pre_tool_use is True


def test_codex_manifest_hooks():
    assert CODEX_MANIFEST.hooks.subagent_start is True
    assert CODEX_MANIFEST.hooks.user_prompt_submit is False
    assert CODEX_MANIFEST.hooks.pre_tool_use is False


def test_github_copilot_manifest_full_hooks():
    assert GITHUB_COPILOT_MANIFEST.hooks.user_prompt_submit is True
    assert GITHUB_COPILOT_MANIFEST.hooks.pre_tool_use is True
    assert GITHUB_COPILOT_MANIFEST.hooks.session_start is True


def test_claude_code_cli_manifest():
    assert CLAUDE_CODE_CLI_MANIFEST.hooks.pre_tool_use is True
    assert CLAUDE_CODE_CLI_MANIFEST.hooks.user_prompt_submit is False


def test_detect_via_env_var(monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOST_ID", "host-codex")
    bridge = HostBridge()
    manifest = bridge.detect()
    assert manifest.host_id == HostId.CODEX


def test_detect_unknown_env_var_returns_unknown(monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOST_ID", "host-totally-unknown")
    # Clear heuristic vars
    monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CODEX_ENV", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("COPILOT_AGENT", raising=False)
    bridge = HostBridge()
    manifest = bridge.detect()
    assert manifest.host_id == HostId.UNKNOWN


def test_supports_hook():
    bridge = HostBridge(override_host_id=HostId.GITHUB_COPILOT)
    assert bridge.supports_hook("user_prompt_submit", HostId.GITHUB_COPILOT) is True
    assert bridge.supports_hook("user_prompt_submit", HostId.CODEX) is False


def test_all_manifests_returns_three():
    bridge = HostBridge()
    manifests = bridge.all_manifests()
    assert len(manifests) == 3


def test_manifest_serialization():
    data = CODEX_MANIFEST.to_dict()
    from grimoire.bridges.schemas import HostCapabilityManifest
    recovered = HostCapabilityManifest.from_dict(data)
    assert recovered.host_id == CODEX_MANIFEST.host_id
    assert recovered.hooks.subagent_start == CODEX_MANIFEST.hooks.subagent_start
    assert recovered.fallback.mode == CODEX_MANIFEST.fallback.mode
