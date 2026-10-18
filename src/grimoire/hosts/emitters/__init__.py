"""Emitter registry: one host id, one renderer of the neutral surface."""

from __future__ import annotations

from grimoire.bridges.schemas import HostId
from grimoire.hosts.emitters.base import (
    Degradation,
    EmitPlan,
    EmitResult,
    EmittedFile,
    Emitter,
    JsonMerge,
    apply_plan,
    apply_plans,
    owned_managed_paths,
)
from grimoire.hosts.emitters.claude_code import ClaudeCodeEmitter
from grimoire.hosts.emitters.copilot import CopilotEmitter
from grimoire.hosts.emitters.generic import GenericEmitter
from grimoire.hosts.emitters.opencode import OpenCodeEmitter

_EMITTERS: dict[HostId, Emitter] = {
    HostId.CLAUDE_CODE_CLI: ClaudeCodeEmitter(),
    HostId.GITHUB_COPILOT: CopilotEmitter(),
    HostId.CODEX: GenericEmitter(HostId.CODEX),
    HostId.CURSOR: GenericEmitter(HostId.CURSOR),
    HostId.GEMINI_CLI: GenericEmitter(HostId.GEMINI_CLI),
    HostId.OPENCODE: OpenCodeEmitter(),
}


def emitter_for(host_id: HostId) -> Emitter | None:
    """Renderer for *host_id*, or ``None`` when the kit has none."""
    return _EMITTERS.get(host_id)


def supported_hosts() -> tuple[HostId, ...]:
    return tuple(_EMITTERS)


__all__ = [
    "ClaudeCodeEmitter",
    "CopilotEmitter",
    "Degradation",
    "EmitPlan",
    "EmitResult",
    "EmittedFile",
    "Emitter",
    "GenericEmitter",
    "JsonMerge",
    "OpenCodeEmitter",
    "apply_plan",
    "apply_plans",
    "emitter_for",
    "owned_managed_paths",
    "supported_hosts",
]
