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
    TextMerge,
    apply_plan,
    apply_plans,
    conflict_exits,
    conflict_path,
    host_conflict_detail,
    owned_managed_paths,
)
from grimoire.hosts.emitters.claude_code import ClaudeCodeEmitter
from grimoire.hosts.emitters.codex import CodexEmitter
from grimoire.hosts.emitters.copilot import CopilotEmitter
from grimoire.hosts.emitters.cursor import CursorEmitter
from grimoire.hosts.emitters.gemini import GeminiEmitter
from grimoire.hosts.emitters.generic import GenericEmitter
from grimoire.hosts.emitters.kimi import KimiCodeEmitter
from grimoire.hosts.emitters.opencode import OpenCodeEmitter
from grimoire.hosts.emitters.zed import ZedEmitter

_EMITTERS: dict[HostId, Emitter] = {
    HostId.CLAUDE_CODE_CLI: ClaudeCodeEmitter(),
    HostId.GITHUB_COPILOT: CopilotEmitter(),
    HostId.CODEX: CodexEmitter(),
    HostId.CURSOR: CursorEmitter(),
    HostId.GEMINI_CLI: GeminiEmitter(),
    HostId.OPENCODE: OpenCodeEmitter(),
    HostId.KIMI_CODE: KimiCodeEmitter(),
    HostId.ZED: ZedEmitter(),
}


def emitter_for(host_id: HostId) -> Emitter | None:
    """Renderer for *host_id*, or ``None`` when the kit has none."""
    return _EMITTERS.get(host_id)


def supported_hosts() -> tuple[HostId, ...]:
    return tuple(_EMITTERS)


__all__ = [
    "ClaudeCodeEmitter",
    "CodexEmitter",
    "CopilotEmitter",
    "CursorEmitter",
    "Degradation",
    "EmitPlan",
    "EmitResult",
    "EmittedFile",
    "Emitter",
    "GeminiEmitter",
    "GenericEmitter",
    "JsonMerge",
    "KimiCodeEmitter",
    "OpenCodeEmitter",
    "TextMerge",
    "ZedEmitter",
    "apply_plan",
    "apply_plans",
    "conflict_exits",
    "conflict_path",
    "emitter_for",
    "host_conflict_detail",
    "owned_managed_paths",
    "supported_hosts",
]
