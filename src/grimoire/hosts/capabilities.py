"""What each host can actually execute, and what it can only be told.

:mod:`grimoire.bridges.schemas` already records *hook* availability per host.
This module extends that with the surfaces an emitter needs to decide what to
write: does the host load sub-agent files, skill folders, slash commands, a
permission table? And — the field that keeps everyone honest — can its hooks
*block*, or only advise?

A capability is claimed here only when the host documents it. When a host
lacks one, the emitter degrades explicitly (see :class:`Degradation` in
:mod:`grimoire.hosts.emitters.base`) rather than writing a file the host will
never read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from grimoire.bridges.schemas import (
    _HOST_REGISTRY,
    HostCapabilityManifest,
    HostId,
)
from grimoire.hosts.surface import HookEvent

#: Hook events mapped onto the ``HostHooks`` field names used by the bridge
#: manifests, so availability has exactly one source of truth.
_HOOK_FIELDS: dict[HookEvent, str] = {
    HookEvent.SESSION_START: "session_start",
    HookEvent.USER_PROMPT_SUBMIT: "user_prompt_submit",
    HookEvent.PRE_TOOL_USE: "pre_tool_use",
    HookEvent.POST_TOOL_USE: "post_tool_use",
    HookEvent.SUBAGENT_STOP: "subagent_stop",
    HookEvent.PRE_COMPACT: "pre_compact",
    HookEvent.STOP: "stop",
}


@dataclass(frozen=True, slots=True)
class HostProfile:
    """Executable surfaces offered by one host."""

    host_id: HostId
    display_name: str
    #: Loads per-agent definition files that get their own context window.
    subagents_native: bool = False
    #: Loads on-demand skill folders (progressive disclosure).
    skills_native: bool = False
    #: Loads user-invocable command files (slash commands).
    commands_native: bool = False
    #: Hooks can refuse a tool call or a turn closure, not merely inject text.
    blocking_hooks: bool = False
    #: Has a declarative allow/deny/ask permission table.
    permissions_native: bool = False
    #: Reads an MCP client configuration.
    mcp_native: bool = False
    #: Can install a packaged bundle without mutating the target repository.
    plugin_packaging: bool = False
    #: Entry-point file the host reads first, when it has one.
    instructions_entrypoint: str = ""
    notes: str = ""

    @property
    def manifest(self) -> HostCapabilityManifest | None:
        return _HOST_REGISTRY.get(self.host_id)

    def supports_event(self, event: HookEvent) -> bool:
        manifest = self.manifest
        if manifest is None:
            return False
        return bool(getattr(manifest.hooks, _HOOK_FIELDS[event], False))

    def supported_events(self) -> tuple[HookEvent, ...]:
        return tuple(e for e in HookEvent if self.supports_event(e))

    def to_dict(self) -> dict[str, Any]:
        return {
            "host_id": self.host_id.value,
            "display_name": self.display_name,
            "surfaces": {
                "subagents": self.subagents_native,
                "skills": self.skills_native,
                "commands": self.commands_native,
                "blocking_hooks": self.blocking_hooks,
                "permissions": self.permissions_native,
                "mcp": self.mcp_native,
                "plugin_packaging": self.plugin_packaging,
            },
            "hook_events": [e.value for e in self.supported_events()],
            "instructions_entrypoint": self.instructions_entrypoint,
            "notes": self.notes,
        }


CLAUDE_CODE_PROFILE = HostProfile(
    host_id=HostId.CLAUDE_CODE_CLI,
    display_name="Claude Code",
    subagents_native=True,
    skills_native=True,
    commands_native=True,
    blocking_hooks=True,
    permissions_native=True,
    mcp_native=True,
    plugin_packaging=True,
    instructions_entrypoint="CLAUDE.md",
    notes="Every Grimoire surface has a native counterpart; nothing degrades.",
)

COPILOT_PROFILE = HostProfile(
    host_id=HostId.GITHUB_COPILOT,
    display_name="GitHub Copilot (VS Code)",
    subagents_native=True,
    skills_native=True,
    commands_native=True,
    blocking_hooks=True,
    permissions_native=False,
    mcp_native=True,
    plugin_packaging=False,
    instructions_entrypoint=".github/copilot-instructions.md",
    notes="No declarative permission table: permissions are enforced by the pre_tool_use hook instead.",
)

CODEX_PROFILE = HostProfile(
    host_id=HostId.CODEX,
    display_name="Codex",
    subagents_native=False,
    skills_native=False,
    commands_native=False,
    blocking_hooks=False,
    permissions_native=False,
    mcp_native=True,
    plugin_packaging=False,
    instructions_entrypoint="AGENTS.md",
    notes="Prose entrypoint plus MCP: agents, skills and commands degrade to a described catalog.",
)

CURSOR_PROFILE = HostProfile(
    host_id=HostId.CURSOR,
    display_name="Cursor",
    subagents_native=False,
    skills_native=False,
    commands_native=False,
    blocking_hooks=False,
    permissions_native=False,
    mcp_native=True,
    plugin_packaging=False,
    instructions_entrypoint=".cursor/rules/grimoire.mdc",
    notes="Rule files plus MCP; no lifecycle hooks, so governance is advisory only.",
)

GEMINI_PROFILE = HostProfile(
    host_id=HostId.GEMINI_CLI,
    display_name="Gemini CLI",
    subagents_native=False,
    skills_native=False,
    commands_native=False,
    blocking_hooks=False,
    permissions_native=False,
    mcp_native=True,
    plugin_packaging=False,
    instructions_entrypoint="GEMINI.md",
    notes="Prose entrypoint plus MCP; no lifecycle hooks, so governance is advisory only.",
)

_PROFILES: dict[HostId, HostProfile] = {
    p.host_id: p for p in (CLAUDE_CODE_PROFILE, COPILOT_PROFILE, CODEX_PROFILE, CURSOR_PROFILE, GEMINI_PROFILE)
}

#: Short ids accepted on the command line, mapped to their host.
HOST_ALIASES: dict[str, HostId] = {
    "claude": HostId.CLAUDE_CODE_CLI,
    "claude-code": HostId.CLAUDE_CODE_CLI,
    "copilot": HostId.GITHUB_COPILOT,
    "github-copilot": HostId.GITHUB_COPILOT,
    "codex": HostId.CODEX,
    "cursor": HostId.CURSOR,
    "gemini": HostId.GEMINI_CLI,
    "gemini-cli": HostId.GEMINI_CLI,
}


def resolve_host(name: str) -> HostId | None:
    """Resolve a CLI alias or a full host id, case-insensitively."""
    key = name.strip().lower()
    if key in HOST_ALIASES:
        return HOST_ALIASES[key]
    try:
        return HostId(key)
    except ValueError:
        return None


def profile_for(host_id: HostId) -> HostProfile:
    """Profile for *host_id*; unknown hosts get a prose-only profile."""
    return _PROFILES.get(
        host_id,
        HostProfile(host_id=HostId.UNKNOWN, display_name="Unknown host", notes="No known executable surface."),
    )


def all_profiles() -> tuple[HostProfile, ...]:
    return tuple(_PROFILES.values())


@dataclass(frozen=True, slots=True)
class CapabilityGap:
    """One surface a host cannot execute, and what stands in for it."""

    surface: str
    fallback: str
    events: tuple[str, ...] = field(default_factory=tuple)


def gaps_for(profile: HostProfile) -> tuple[CapabilityGap, ...]:
    """Surfaces *profile* cannot execute natively, with their substitute."""
    gaps: list[CapabilityGap] = []
    if not profile.subagents_native:
        gaps.append(CapabilityGap("subagents", "agents described in the instructions entrypoint"))
    if not profile.skills_native:
        gaps.append(CapabilityGap("skills", "skills inlined as a catalog in the instructions entrypoint"))
    if not profile.commands_native:
        gaps.append(CapabilityGap("commands", "commands listed as CLI invocations the agent must run"))
    if not profile.blocking_hooks:
        missing = tuple(e.value for e in HookEvent if not profile.supports_event(e))
        gaps.append(CapabilityGap("blocking_hooks", "governance stated as instructions, enforced by CI only", missing))
    elif not profile.permissions_native:
        gaps.append(CapabilityGap("permissions", "permission rules enforced through the pre_tool_use hook"))
    return tuple(gaps)
