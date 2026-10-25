"""Host-neutral lifecycle vocabulary: hook events, tool verbs, enforcement.

Split out of :mod:`grimoire.hosts.surface` (issue #419): the hook entry point
(:mod:`grimoire.hosts.runtime`) and :mod:`grimoire.hosts.capabilities` need
only these three enums on *every* hook invocation, never the agent/model IR
(``AgentSpec``, ``ModelAffinity``, the Rust-optional fingerprint machinery)
that used to live in the same module — and so was imported, and paid for, on
every ``grimoire-hook`` call regardless of which decision actually ran.

:mod:`grimoire.hosts.surface` re-exports all three for full backward
compatibility; every existing ``from grimoire.hosts.surface import
HookEvent`` (or ``ToolVerb``/``Enforcement``) keeps working, unchanged, and
gets the exact same objects. Only the hook-path modules import from here
directly, to avoid paying for the rest of ``surface.py``.
"""

from __future__ import annotations

from enum import StrEnum


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
    #: Événements que le kit sait décider (au minimum tracer) mais qu'aucun
    #: émetteur n'écrit : voir :data:`NOT_EMITTED_EVENTS` (R31, veille
    #: ``hooks-echec-ferme-2026-10``).
    TASK_CREATED = "task_created"
    TASK_COMPLETED = "task_completed"
    INSTRUCTIONS_LOADED = "instructions_loaded"


#: Événements déclarés avec une décision par défaut (``grimoire.event-trace`` :
#: une ligne au TraceLedger) mais jamais émis dans la configuration d'un hôte.
#: ``TaskCreated``/``TaskCompleted`` portent sur les tâches de l'hôte (outil
#: ``TaskCreate``), pas sur le registre du kit ; ``InstructionsLoaded`` est une
#: sonde de mesure, pas un gate. Un hôte ne reçoit un événement que si son
#: manifeste le déclare (:meth:`HostProfile.supports_event`) : aucun ne le
#: déclare, donc aucun émetteur ne l'écrit. Le point d'entrée
#: ``grimoire host hook --event <nom>`` reste appelable à la main.
NOT_EMITTED_EVENTS: frozenset[HookEvent] = frozenset(
    {HookEvent.TASK_CREATED, HookEvent.TASK_COMPLETED, HookEvent.INSTRUCTIONS_LOADED}
)


class Enforcement(StrEnum):
    """How strongly a hook binds the agent.

    ``BLOCKING`` is the only level that turns a rule into a constraint: the
    host refuses the action or the closure. ``ADVISORY`` injects context and
    hopes. The distinction is the whole point of :mod:`grimoire.hosts.surface`
    — the kit's governance was advisory everywhere before it existed.
    """

    BLOCKING = "blocking"
    ADVISORY = "advisory"
