"""Host capability manifest schemas."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class HostId(str, Enum):
    CLAUDE_CODE_CLI = "host-claude-code-cli"
    GITHUB_COPILOT = "host-github-copilot"
    CODEX = "host-codex"
    UNKNOWN = "host-unknown"


class FallbackMode(str, Enum):
    CLI_GUARDED = "cli_guarded"
    PREVIEW_ONLY = "preview_only"
    FULL = "full"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class HostHooks:
    session_start: bool = False
    user_prompt_submit: bool = False
    pre_tool_use: bool = False
    post_tool_use: bool = False
    subagent_start: bool = False
    subagent_stop: bool = False
    pre_compact: bool = False
    stop: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_start": self.session_start,
            "user_prompt_submit": self.user_prompt_submit,
            "pre_tool_use": self.pre_tool_use,
            "post_tool_use": self.post_tool_use,
            "subagent_start": self.subagent_start,
            "subagent_stop": self.subagent_stop,
            "pre_compact": self.pre_compact,
            "stop": self.stop,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> HostHooks:
        return cls(
            session_start=d.get("session_start", False),
            user_prompt_submit=d.get("user_prompt_submit", False),
            pre_tool_use=d.get("pre_tool_use", False),
            post_tool_use=d.get("post_tool_use", False),
            subagent_start=d.get("subagent_start", False),
            subagent_stop=d.get("subagent_stop", False),
            pre_compact=d.get("pre_compact", False),
            stop=d.get("stop", False),
        )


@dataclass(frozen=True, slots=True)
class HostFallback:
    mode: FallbackMode
    required_controls: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode.value, "required_controls": list(self.required_controls)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> HostFallback:
        return cls(
            mode=FallbackMode(d.get("mode", "none")),
            required_controls=tuple(d.get("required_controls", [])),
        )


@dataclass(frozen=True, slots=True)
class HostCapabilityManifest:
    host_id: HostId
    display_name: str
    hooks: HostHooks
    fallback: HostFallback
    schema_version: str = "grimoire.host_capability.v1"
    supports_mcp: bool = False
    supports_streaming: bool = False
    supports_workspace_mutation: bool = False
    tool_policy_native: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "host_id": self.host_id.value,
            "display_name": self.display_name,
            "supports": {
                "hooks": self.hooks.to_dict(),
                "mcp": self.supports_mcp,
                "streaming": self.supports_streaming,
                "workspace_mutation": self.supports_workspace_mutation,
                "tool_policy_native": self.tool_policy_native,
            },
            "fallback": self.fallback.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> HostCapabilityManifest:
        supports = d.get("supports", {})
        return cls(
            host_id=HostId(d["host_id"]),
            display_name=d.get("display_name", ""),
            hooks=HostHooks.from_dict(supports.get("hooks", {})),
            fallback=HostFallback.from_dict(d.get("fallback", {})),
            schema_version=d.get("schema_version", "grimoire.host_capability.v1"),
            supports_mcp=supports.get("mcp", False),
            supports_streaming=supports.get("streaming", False),
            supports_workspace_mutation=supports.get("workspace_mutation", False),
            tool_policy_native=supports.get("tool_policy_native", False),
        )


# Canonical manifests per known host
CLAUDE_CODE_CLI_MANIFEST = HostCapabilityManifest(
    host_id=HostId.CLAUDE_CODE_CLI,
    display_name="Claude Code CLI",
    hooks=HostHooks(
        session_start=True,
        user_prompt_submit=False,
        pre_tool_use=True,
        post_tool_use=True,
        subagent_start=True,
        subagent_stop=True,
        pre_compact=True,
        stop=True,
    ),
    fallback=HostFallback(mode=FallbackMode.CLI_GUARDED, required_controls=("preview_before_write",)),
    supports_mcp=True,
    supports_streaming=True,
    supports_workspace_mutation=True,
    tool_policy_native=False,
)

GITHUB_COPILOT_MANIFEST = HostCapabilityManifest(
    host_id=HostId.GITHUB_COPILOT,
    display_name="GitHub Copilot",
    hooks=HostHooks(
        session_start=True,
        user_prompt_submit=True,
        pre_tool_use=True,
        post_tool_use=True,
        subagent_start=True,
        subagent_stop=True,
        pre_compact=True,
        stop=True,
    ),
    fallback=HostFallback(mode=FallbackMode.FULL, required_controls=()),
    supports_mcp=True,
    supports_streaming=True,
    supports_workspace_mutation=True,
    tool_policy_native=False,
)

CODEX_MANIFEST = HostCapabilityManifest(
    host_id=HostId.CODEX,
    display_name="Codex",
    hooks=HostHooks(
        session_start=False,
        user_prompt_submit=False,
        pre_tool_use=False,
        post_tool_use=False,
        subagent_start=True,
        subagent_stop=True,
        pre_compact=False,
        stop=False,
    ),
    fallback=HostFallback(
        mode=FallbackMode.CLI_GUARDED,
        required_controls=(
            "preview_before_write",
            "validation_before_durable_write",
            "explicit_proof_for_risky_changes",
        ),
    ),
    supports_mcp=True,
    supports_streaming=True,
    supports_workspace_mutation=True,
    tool_policy_native=False,
)

_HOST_REGISTRY: dict[HostId, HostCapabilityManifest] = {
    HostId.CLAUDE_CODE_CLI: CLAUDE_CODE_CLI_MANIFEST,
    HostId.GITHUB_COPILOT: GITHUB_COPILOT_MANIFEST,
    HostId.CODEX: CODEX_MANIFEST,
}
