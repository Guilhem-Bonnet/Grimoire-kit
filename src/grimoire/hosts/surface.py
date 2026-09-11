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

Backend
-------
:meth:`AgentSpec.fingerprint` and :func:`duplicate_agent_fingerprints` — the
distinction guard (issue #372) — optionally delegate to
``grimoire_hosts_core``, a PyO3-compiled Rust port of this module and of
:mod:`grimoire.hosts.collect` (``rust/grimoire-hosts-core/``, issue #354).
Same contract as the two earlier ports (``grimoire.policies.engine``,
``grimoire.core.validator``): never required (nothing published depends on
it), the pure-Python path above is unchanged and is what runs when the
compiled module is absent. ``GRIMOIRE_HOSTS_BACKEND`` (sibling of
``GRIMOIRE_SCHEMA_BACKEND``/``GRIMOIRE_POLICIES_BACKEND``) overrides the
choice — ``"python"`` forces this reference implementation, ``"rust"``
forces the compiled module and raises
:class:`~grimoire.core.exceptions.GrimoireAgentError` if it is not
available. Defined here (not in :mod:`grimoire.hosts.collect`, which also
uses it) because :mod:`grimoire.hosts.collect` already imports from this
module and the reverse would cycle. See
``tests/unit/test_hosts_rust_parity.py``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from grimoire.core.exceptions import GrimoireAgentError

try:
    import grimoire_hosts_core as _rust_core
except ImportError:  # pragma: no cover - exercised by the dedicated Rust CI job
    _rust_core = None


def rust_backend_available() -> bool:
    """Whether the compiled ``grimoire_hosts_core`` module is importable.

    Purely informational (used by tests and diagnostics) — every call site
    below decides its own backend fresh via :func:`_use_rust_backend`.
    """
    return _rust_core is not None


def _use_rust_backend() -> bool:
    """Resolve which backend this module's Rust-optional functions should use.

    Reads ``GRIMOIRE_HOSTS_BACKEND`` fresh every time rather than once at
    import time, so tests can flip it with ``monkeypatch.setenv`` around a
    single call without reloading the module.
    """
    override = os.environ.get("GRIMOIRE_HOSTS_BACKEND", "auto").strip().lower()
    if override == "python":
        return False
    if override == "rust":
        if _rust_core is None:
            raise GrimoireAgentError(
                "GRIMOIRE_HOSTS_BACKEND=rust demande le coeur Rust, mais "
                "grimoire_hosts_core est introuvable. Construire l'extension "
                "localement (voir CONTRIBUTING.md, `maturin develop` dans "
                "rust/grimoire-hosts-core/) ou revenir a auto/python."
            )
        return True
    if override not in ("auto", ""):
        raise GrimoireAgentError(f"GRIMOIRE_HOSTS_BACKEND invalide: {override!r} (attendu auto/python/rust)")
    return _rust_core is not None


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
    POST_TOOL_USE_FAILURE = "post_tool_use_failure"
    SUBAGENT_START = "subagent_start"
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
        if _use_rust_backend():
            assert _rust_core is not None  # guarded by _use_rust_backend
            return cls(**_rust_core.model_affinity_from_frontmatter(data))
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
    max_turns: int | None = None
    """Override for the host's per-turn budget, read from the agent file's
    ``max_turns:`` frontmatter key. ``None`` lets the emitter fall back to its
    own default — most agent files never set this."""
    skills: tuple[str, ...] = ()
    """Slugs of :class:`SkillSpec` this agent owns, read from the agent
    file's ``skills:`` frontmatter key. A skill owned by an agent is emitted
    into that agent's own context rather than the project-wide skill
    directory (issue #372): the session pays its description only on the
    turns where this agent runs, not on every turn. Resolved against the
    project's collected skills at build time — an unknown slug is a build
    error, not a silently dropped reference."""
    context: tuple[str, ...] = ()
    """Project-relative paths this agent declares as its own context, read
    from the ``context:`` frontmatter key. Verified to exist on disk at
    build time. This is a declared intake for
    :mod:`grimoire.tools.context_router`, not proof that a session actually
    loads it — the router plans context adaptively and this field only says
    what the agent claims to need."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "definition_ref": self.definition_ref,
            "tools": [t.value for t in self.tools],
            "tools_origin": self.tools_origin,
            "affinity": self.affinity.to_dict(),
            "entry_point": self.entry_point,
            "max_turns": self.max_turns,
            "skills": list(self.skills),
            "context": list(self.context),
        }

    def fingerprint(self) -> tuple[str, ...]:
        """The faisceau (tools, context, skills) that must distinguish this
        agent from every other one (issue #372's distinction guard).

        Deliberately excludes ``name``, ``description`` and ``affinity``:
        those are prose, and prose is exactly what let nine phantom agents
        through in #346 — two agents that grant the same tools, read the
        same context and own the same skills are the same agent wearing two
        names, no matter how differently they are described.
        """
        if _use_rust_backend():
            assert _rust_core is not None  # guarded by _use_rust_backend
            return tuple(
                _rust_core.agent_fingerprint([t.value for t in self.tools], list(self.context), list(self.skills))
            )
        return (
            *sorted(t.value for t in self.tools),
            "|",
            *sorted(self.context),
            "|",
            *sorted(self.skills),
        )


def _partition_duplicate_pairs(
    agents: tuple[AgentSpec, ...],
    *,
    is_override: Any,
) -> tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]:
    """``(strict, duplicates)`` for *agents*, ``is_override(agent) -> bool``
    deciding which pairs count as strict. Shared implementation for
    :func:`duplicate_agent_fingerprints` (always non-strict — see below) and
    :mod:`grimoire.hosts.collect`'s ``build_surface`` (override-aware),
    delegated to :func:`grimoire_hosts_core.partition_duplicate_pairs` as a
    single call when the Rust backend is active.
    """
    if _use_rust_backend():
        assert _rust_core is not None  # guarded by _use_rust_backend
        records = [
            (
                agent.name,
                bool(is_override(agent)),
                [t.value for t in agent.tools],
                list(agent.context),
                list(agent.skills),
            )
            for agent in agents
        ]
        strict_pairs, duplicate_pairs = _rust_core.partition_duplicate_pairs(records)
        return tuple(map(tuple, strict_pairs)), tuple(map(tuple, duplicate_pairs))

    by_fingerprint: dict[tuple[str, ...], list[str]] = {}
    for agent in agents:
        by_fingerprint.setdefault(agent.fingerprint(), []).append(agent.name)
    duplicates: list[tuple[str, str]] = []
    for names in by_fingerprint.values():
        if len(names) < 2:
            continue
        ordered = sorted(names)
        duplicates.extend((ordered[i], ordered[j]) for i in range(len(ordered)) for j in range(i + 1, len(ordered)))
    by_name = {agent.name: agent for agent in agents}
    strict = [
        pair for pair in duplicates if any(is_override(by_name[n]) for n in pair if n in by_name)
    ]
    return tuple(strict), tuple(duplicates)


def duplicate_agent_fingerprints(agents: tuple[AgentSpec, ...]) -> tuple[tuple[str, str], ...]:
    """Pairs of agent names sharing an identical faisceau (issue #372).

    Exact-duplicate detection only — two agents differing by one trivial
    context path pass this guard while being functionally identical. That
    limit is assumed: this catches the phantom-agent case (#346), not the
    median case of near-duplicates.

    Never "strict" on its own (every collision here is informational): the
    override/kit distinction that decides whether a collision blocks
    (``build_surface``, ``grimoire.hosts.collect``) needs each agent's
    ``definition_ref``, which this function's signature does not carry.
    """
    _strict, duplicates = _partition_duplicate_pairs(agents, is_override=lambda _agent: False)
    return duplicates


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
    notes: tuple[str, ...] = ()
    """Constats non bloquants relevés en construisant la surface — par exemple
    deux agents livrés par le kit au faisceau identique. Une dette du kit ne
    doit pas empêcher un projet de se synchroniser ; elle doit rester visible."""

    def entry_agent(self) -> AgentSpec | None:
        for agent in self.agents:
            if agent.entry_point:
                return agent
        return None

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
