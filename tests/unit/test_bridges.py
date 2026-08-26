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
    # Claude Code does expose UserPromptSubmit. The manifest claimed otherwise,
    # which excluded the one hook that can enrich a prompt before the model
    # reads it; the assertion encoded the mistake and is corrected with it.
    assert CLAUDE_CODE_CLI_MANIFEST.hooks.user_prompt_submit is True


def test_detect_via_env_var(monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOST_ID", "host-codex")
    bridge = HostBridge()
    manifest = bridge.detect()
    assert manifest.host_id == HostId.CODEX


def test_detect_unknown_env_var_returns_unknown(monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOST_ID", "host-totally-unknown")
    # Clear every heuristic marker, otherwise the test passes or fails
    # depending on which host happens to run it.
    for var in (
        "CLAUDECODE",
        "CLAUDE_CODE_ENTRYPOINT",
        "CODEX_ENV",
        "CODEX_SANDBOX",
        "COPILOT_AGENT",
        "GITHUB_COPILOT_AGENT",
        "CURSOR_AGENT",
        "CURSOR_TRACE_ID",
        "GEMINI_CLI",
        "GEMINI_CLI_VERSION",
    ):
        monkeypatch.delenv(var, raising=False)
    bridge = HostBridge()
    manifest = bridge.detect()
    assert manifest.host_id == HostId.UNKNOWN


def test_supports_hook():
    bridge = HostBridge(override_host_id=HostId.GITHUB_COPILOT)
    assert bridge.supports_hook("user_prompt_submit", HostId.GITHUB_COPILOT) is True
    assert bridge.supports_hook("user_prompt_submit", HostId.CODEX) is False


def test_all_manifests_cover_the_registry():
    bridge = HostBridge()
    manifests = {m.host_id for m in bridge.all_manifests()}
    assert manifests == {
        HostId.CLAUDE_CODE_CLI,
        HostId.GITHUB_COPILOT,
        HostId.CODEX,
        HostId.CURSOR,
        HostId.GEMINI_CLI,
    }


def test_api_key_alone_does_not_identify_a_host(monkeypatch):
    """A vendor key says who pays for the tokens, not which host is running."""
    monkeypatch.delenv("GRIMOIRE_HOST_ID", raising=False)
    for var in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CODEX_ENV", "CODEX_SANDBOX",
                "COPILOT_AGENT", "GITHUB_COPILOT_AGENT", "CURSOR_AGENT", "CURSOR_TRACE_ID",
                "GEMINI_CLI", "GEMINI_CLI_VERSION"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert HostBridge().detect().host_id == HostId.UNKNOWN


def test_manifest_serialization():
    data = CODEX_MANIFEST.to_dict()
    from grimoire.bridges.schemas import HostCapabilityManifest
    recovered = HostCapabilityManifest.from_dict(data)
    assert recovered.host_id == CODEX_MANIFEST.host_id
    assert recovered.hooks.subagent_start == CODEX_MANIFEST.hooks.subagent_start
    assert recovered.fallback.mode == CODEX_MANIFEST.fallback.mode
