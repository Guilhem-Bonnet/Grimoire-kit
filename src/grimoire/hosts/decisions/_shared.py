"""The vocabulary every decision shares: ``Outcome``, ``HookInput``, ``Decision``.

Split out of the former monolithic ``grimoire.hosts.decisions`` module (issue
#419) so that the two things every decision needs — regardless of which one
actually runs — cost only what they are: an enum and two small frozen
dataclasses, none of them pulling anything beyond the standard library and
:mod:`grimoire.hosts.events`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from grimoire.hosts.events import HookEvent


class Outcome(StrEnum):
    """What the host should do with the pending action or turn."""

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"
    BLOCK = "block"
    """Refuse to end the turn: the agent must keep working."""


@dataclass(frozen=True, slots=True)
class HookInput:
    """A host hook payload, normalised.

    Hosts disagree on field names (``tool_name`` vs ``toolName``) and on tool
    vocabulary (``Bash`` vs ``run_in_terminal``); :mod:`grimoire.hosts.runtime`
    flattens both before a decision ever sees them.
    """

    event: HookEvent
    project_root: Path
    tool_name: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    tool_response: dict[str, Any] = field(default_factory=dict)
    prompt: str = ""
    agent_name: str = ""
    session_id: str = ""
    stop_active: bool = False
    """True when the host is already re-running the agent because of a previous
    block. Blocking again here is how a session gets stuck in a loop."""
    host: str = ""
    """The host id (:class:`grimoire.bridges.schemas.HostId` value) the hook was
    invoked for — ``grimoire-hook --host claude`` — or ``""`` when a decision is
    run without a host (tests, direct calls). Read by the session ↔ task link
    (issue #638): a resume command exists only for a host known to have one."""
    tool_use_id: str = ""
    """The host's identifier of this tool call, shared by its ``PreToolUse`` and
    ``PostToolUse`` payloads, or ``""`` when the host sends none. The temporal
    layer keys a pending approval on it: a hook may rewrite the tool input
    between the two events (W1-09)."""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Decision:
    """A host-neutral verdict plus whatever context the agent should receive."""

    outcome: Outcome = Outcome.ALLOW
    reason: str = ""
    context: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def is_refusal(self) -> bool:
        return self.outcome in {Outcome.DENY, Outcome.BLOCK}

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "reason": self.reason,
            "context": self.context,
            "detail": dict(self.detail),
        }
