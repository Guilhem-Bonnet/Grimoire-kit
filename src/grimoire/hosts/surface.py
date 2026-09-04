"""Host-neutral description of what a Grimoire project offers an agent host.

The kit has always described its agents, workflows and governance in prose and
let each host read that prose. Prose is the lowest common denominator: it
survives everywhere and binds nowhere. A host that offers *executable*
surfaces — sub-agents with their own context window, skills with progressive
disclosure, slash commands, blocking lifecycle hooks, tool permissions — gets
none of that from a Markdown pointer.

This module is the other half of the answer: a single intermediate
representation (IR) of the project's agentic surface, built once from the
project, then rendered by one emitter per host. Adding a host means adding an
emitter, never re-describing the project.

The IR is deliberately vendor-free:

- tools are named with :class:`ToolVerb` (``read``, ``edit``, …), not with
  ``Read``/``Bash`` or ``['read', 'search']`` — each emitter maps them;
- models are named by *affinity* (reasoning, context window, speed, cost), not
  by model id, so a host picks from what it actually offers;
- hooks name a **decision**, not a command line: the decision is host-neutral
  Python (:mod:`grimoire.hosts.decisions`) and the emitter wires whatever
  invocation its host understands.

Anything a host cannot do natively is not silently dropped: emitters declare a
degradation (:class:`Degradation`) so ``grimoire host status`` can say what is
enforced, what is only advertised, and what is missing outright.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ToolVerb(StrEnum):
    """The tool capabilities an agent may be granted, host-independently."""

    READ = "read"
    SEARCH = "search"
    EDIT = "edit"
    EXECUTE = "execute"
    WEB = "web"


class HookEvent(StrEnum):
    """Agent lifecycle events, named once for every host.

    Hosts implement a subset; :class:`grimoire.hosts.capabilities.HostProfile`
    records which, and emitters skip — loudly — what their host lacks.
    """

    SESSION_START = "session_start"
    USER_PROMPT_SUBMIT = "user_prompt_submit"
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    SUBAGENT_STOP = "subagent_stop"
    PRE_COMPACT = "pre_compact"
    STOP = "stop"


class Enforcement(StrEnum):
    """How strongly a hook binds the agent.

    ``BLOCKING`` is the only level that turns a rule into a constraint: the
    host refuses the action or the closure. ``ADVISORY`` injects context and
    hopes. The distinction is the whole point of this module — the kit's
    governance was advisory everywhere before it existed.
    """

    BLOCKING = "blocking"
    ADVISORY = "advisory"


@dataclass(frozen=True, slots=True)
class ModelAffinity:
    """What an agent needs from a model, without naming one."""

    reasoning: str = "medium"
    context_window: str = "medium"
    speed: str = "medium"
    cost: str = "medium"

    @classmethod
    def from_frontmatter(cls, data: dict[str, Any] | None) -> ModelAffinity:
        if not isinstance(data, dict):
            return cls()
        return cls(
            reasoning=str(data.get("reasoning", "medium")),
            context_window=str(data.get("context_window", "medium")),
            speed=str(data.get("speed", "medium")),
            cost=str(data.get("cost", "medium")),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "reasoning": self.reasoning,
            "context_window": self.context_window,
            "speed": self.speed,
            "cost": self.cost,
        }


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """One Grimoire persona, ready to be rendered as a host sub-agent."""

    name: str
    description: str
    definition_ref: str
    tools: tuple[ToolVerb, ...] = (ToolVerb.READ, ToolVerb.SEARCH)
    affinity: ModelAffinity = field(default_factory=ModelAffinity)
    entry_point: bool = False
    tools_origin: str = "inferred"
    """``declared`` when the agent file carries ``tools:``, ``inferred`` when
    derived from its body. Surfaced by ``grimoire host status`` so a wrong
    inference is visible instead of silently shaping a tool boundary."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "definition_ref": self.definition_ref,
            "tools": [t.value for t in self.tools],
            "tools_origin": self.tools_origin,
            "affinity": self.affinity.to_dict(),
            "entry_point": self.entry_point,
        }


@dataclass(frozen=True, slots=True)
class SkillSpec:
    """A multi-step capability the host may load on demand.

    ``description`` is not decoration: on hosts with skill auto-discovery it is
    the only thing the model sees before deciding to load the body, so it must
    say *when* to use the skill, not what it is about.
    """

    slug: str
    name: str
    description: str
    body: str
    tools: tuple[ToolVerb, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "tools": [t.value for t in self.tools],
        }


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """An explicit, user-invoked action (a slash command on most hosts)."""

    slug: str
    description: str
    body: str
    argument_hint: str = ""
    tools: tuple[ToolVerb, ...] = (ToolVerb.EXECUTE,)
    source: str = "host"
    """``host`` for commands this layer owns, ``workflow`` for the kit's
    existing Copilot prompt files. The distinction decides ownership: a
    workflow prompt is already placed verbatim by the scaffolder, so an
    emitter targeting that same path would be a second writer for one file."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "description": self.description,
            "argument_hint": self.argument_hint,
            "tools": [t.value for t in self.tools],
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class HookSpec:
    """A lifecycle rule, expressed as *which decision runs when*."""

    event: HookEvent
    decision: str
    enforcement: Enforcement = Enforcement.ADVISORY
    matcher: tuple[str, ...] = ()
    """Neutral tool families the hook applies to (``write``, ``execute``, …).
    Empty means every tool. Emitters translate to their host's matcher syntax."""
    timeout: int = 30
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event.value,
            "decision": self.decision,
            "enforcement": self.enforcement.value,
            "matcher": list(self.matcher),
            "timeout": self.timeout,
            "rationale": self.rationale,
        }


@dataclass(frozen=True, slots=True)
class PermissionSpec:
    """Tool permissions in neutral terms.

    Patterns use the ``verb:target`` shape (``execute:rm -rf *``,
    ``read:.env``). Hosts with native permission config render them there;
    hosts without one fall back to a ``pre_tool_use`` hook, which is why
    :class:`grimoire.hosts.capabilities.HostProfile` carries
    ``permissions_native``.
    """

    deny: tuple[str, ...] = ()
    ask: tuple[str, ...] = ()
    allow: tuple[str, ...] = ()

    def is_empty(self) -> bool:
        return not (self.deny or self.ask or self.allow)

    def to_dict(self) -> dict[str, Any]:
        return {"deny": list(self.deny), "ask": list(self.ask), "allow": list(self.allow)}


@dataclass(frozen=True, slots=True)
class McpServerSpec:
    """An MCP server the project expects its host to connect to."""

    name: str
    command: str
    args: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "command": self.command, "args": list(self.args)}


@dataclass(frozen=True, slots=True)
class ProjectSurface:
    """Everything a host needs to know about this project, host-neutrally."""

    project_name: str
    project_root_ref: str = "."
    agents: tuple[AgentSpec, ...] = ()
    skills: tuple[SkillSpec, ...] = ()
    commands: tuple[CommandSpec, ...] = ()
    hooks: tuple[HookSpec, ...] = ()
    permissions: PermissionSpec = field(default_factory=PermissionSpec)
    mcp_servers: tuple[McpServerSpec, ...] = ()
    governed: bool = False
    """True when the project is enrolled in the agentic standard. Governance
    hooks are only emitted for enrolled projects: a blocking gate on a project
    with no gates to check would fail closed on nothing."""

    def entry_agent(self) -> AgentSpec | None:
        for agent in self.agents:
            if agent.entry_point:
                return agent
        return None

    def hooks_for(self, event: HookEvent) -> tuple[HookSpec, ...]:
        return tuple(h for h in self.hooks if h.event == event)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_name": self.project_name,
            "governed": self.governed,
            "agents": [a.to_dict() for a in self.agents],
            "skills": [s.to_dict() for s in self.skills],
            "commands": [c.to_dict() for c in self.commands],
            "hooks": [h.to_dict() for h in self.hooks],
            "permissions": self.permissions.to_dict(),
            "mcp_servers": [m.to_dict() for m in self.mcp_servers],
        }
