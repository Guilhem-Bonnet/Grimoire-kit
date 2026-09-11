"""Host-neutral hook decisions: the rules, decided once.

A hook is two things glued together: a *decision* ("may this command run?",
"is this task finished?") and a *wire format* (the JSON a given host reads and
writes). Only the second is host-specific. Keeping the first here means the
governed standard behaves identically whether it runs under Claude Code,
Copilot, or a host added next year — and it means the rules are testable
without simulating any host at all.

Every decision is defensive by construction:

- it never raises into the host — a crashed hook must not brick a session, so
  failures degrade to :data:`Outcome.ALLOW` with the error carried as context;
- it fails **open** when the project is not enrolled in the standard: gates
  that do not exist cannot be red, and blocking on their absence would make
  the hook a trap rather than a guardrail;
- it fails **closed** only where the standard says so: a governed task whose
  evidence gates are red is not a finished task.

Package, not module (issue #419)
---------------------------------
Before this, every decision lived in one 918-line module, imported in full by
:mod:`grimoire.hosts.runtime` on *every* ``grimoire-hook`` call — a
``PreToolUse`` call paid for the policy engine (:mod:`grimoire.policies.engine`)
*and* the activation/evidence-gate machinery it never runs, exactly the
anti-pattern issue #405 fixed for the CLI's Typer tree
(:mod:`grimoire.cli._lazy`). Each decision now lives in its own submodule,
resolved by :data:`DECISIONS` / :func:`run_decision` only when its id is
actually requested. ``Outcome``, ``HookInput`` and ``Decision`` (in
``_shared``) are the one exception: every decision constructs them, so they
are imported eagerly here — they cost an enum and two frozen dataclasses,
nothing a decision would ever want to defer.

Every name this module used to export at the top level (``classify_tool``,
``decide_activation``, ``record_agent_miss``, even the private
``_gate_summary`` a regression test pins) still resolves via
``from grimoire.hosts.decisions import <name>`` — through module
``__getattr__`` (:pep:`562`), which imports only the one submodule that
defines it.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

from grimoire.hosts.decisions._shared import Decision, HookInput, Outcome
from grimoire.hosts.events import HookEvent

__all__ = [
    "DECISIONS",
    "DEFAULT_DECISION_BY_EVENT",
    "Decision",
    "HookInput",
    "Outcome",
    "classify_tool",
    "command_surface",
    "decide_activation",
    "decide_context_capsule",
    "decide_evidence_gate",
    "decide_evidence_trace",
    "decide_subagent_gate",
    "decide_task_context",
    "decide_tool_policy",
    "entry_persona_context",
    "record_agent_miss",
    "run_decision",
]

#: Decision id -> (submodule, attribute). The one table that says which file
#: owns which rule; a host emitter never embeds a rule, only a reference to
#: one (``HookSpec.decision``).
_DECISION_FUNCS: dict[str, tuple[str, str]] = {
    "grimoire.activation": ("activation", "decide_activation"),
    "grimoire.task-context": ("task_context", "decide_task_context"),
    "grimoire.tool-policy": ("tool_policy", "decide_tool_policy"),
    "grimoire.evidence-trace": ("evidence_trace", "decide_evidence_trace"),
    "grimoire.evidence-gate": ("evidence_gate", "decide_evidence_gate"),
    "grimoire.subagent-gate": ("subagent_gate", "decide_subagent_gate"),
    "grimoire.context-capsule": ("context_capsule", "decide_context_capsule"),
}

#: Which decision answers which event when the caller only names an event.
DEFAULT_DECISION_BY_EVENT: dict[HookEvent, str] = {
    HookEvent.SESSION_START: "grimoire.activation",
    HookEvent.USER_PROMPT_SUBMIT: "grimoire.task-context",
    HookEvent.PRE_TOOL_USE: "grimoire.tool-policy",
    HookEvent.POST_TOOL_USE: "grimoire.evidence-trace",
    HookEvent.SUBAGENT_STOP: "grimoire.subagent-gate",
    HookEvent.PRE_COMPACT: "grimoire.context-capsule",
    HookEvent.STOP: "grimoire.evidence-gate",
}


_DecisionFunc = Callable[[HookInput], Decision]


def _resolve(decision_id: str) -> _DecisionFunc | None:
    spec = _DECISION_FUNCS.get(decision_id)
    if spec is None:
        return None
    module_name, attr = spec
    module = importlib.import_module(f"{__name__}.{module_name}")
    func: _DecisionFunc = getattr(module, attr)
    return func


class _LazyDecisions(dict[str, _DecisionFunc]):
    """``{decision id: function}``, each value resolved (and cached) on first use.

    A plain ``dict`` populated eagerly would import every decision's module —
    including its own heavy dependencies — the moment anything touched
    :data:`DECISIONS`, which is exactly the cost this package exists to avoid.
    ``__missing__`` resolves and caches instead: after the first lookup a key
    behaves like an ordinary dict entry, forwards and backwards.

    That matters for ``tests/unit/test_hosts.py``, which does
    ``monkeypatch.setitem(DECISIONS, "grimoire.tool-policy", _crash)`` — a real
    ``dict`` is exactly what ``monkeypatch`` needs to save the previous value
    (via :meth:`get`, resolving it if necessary) and restore it afterwards.
    """

    def __missing__(self, key: str) -> _DecisionFunc:
        func = _resolve(key)
        if func is None:
            raise KeyError(key)
        self[key] = func
        return func

    def get(self, key: str, default: _DecisionFunc | None = None) -> _DecisionFunc | None:  # type: ignore[override]
        # dict.get's stub is an overloaded, positional-only, generic
        # signature; this narrower one (decision id -> decision function) is
        # the whole point of the override, not an accident to silence.
        try:
            return self[key]
        except KeyError:
            return default


#: Decision id -> implementation, resolved lazily. See :class:`_LazyDecisions`.
DECISIONS: dict[str, _DecisionFunc] = _LazyDecisions()


def run_decision(decision_id: str, hook: HookInput) -> Decision:
    """Run *decision_id* against *hook*, importing only its module, never raising into the host."""
    func = DECISIONS.get(decision_id)
    if func is None:
        return Decision(context=f"[Grimoire] décision inconnue : {decision_id}", detail={"error": "unknown-decision"})
    try:
        return func(hook)
    except Exception as exc:
        return _failed_decision(decision_id, hook, exc)


def _failed_decision(decision_id: str, hook: HookInput, exc: Exception) -> Decision:
    """What a crashed decision becomes — never an approval.

    Before this, any exception here rendered ``ALLOW``; on a blocking host
    that is ``permissionDecision: allow``, and the explanatory context is not
    a field the host reads on ``PreToolUse``. A guardrail that crashed was an
    auto-approval with no visible trace. A call the policy could not judge is
    now handed back to the user (``ask``) with the cause in the reason; every
    other event keeps the session alive and says what broke.
    """
    cause = f"{type(exc).__name__}: {exc}"
    detail = {"error": cause, "decision": decision_id}
    if hook.event is HookEvent.PRE_TOOL_USE:
        return Decision(
            outcome=Outcome.ASK,
            reason=(
                f"[Grimoire] hook {decision_id} en erreur — {cause}. "
                f"La politique n'a pas pu juger {hook.tool_name or 'cet appel'} : à toi de décider."
            ),
            detail=detail,
        )
    return Decision(
        outcome=Outcome.ALLOW,
        context=f"[Grimoire] hook {decision_id} en erreur, session non bloquée : {cause}",
        detail=detail,
    )


#: Public (and the one pinned private) name -> submodule that defines it, for
#: ``__getattr__`` below. Every name this package's callers import by name
#: from the top level, other than ``Decision``/``HookInput``/``Outcome``
#: (imported eagerly above — they are cheap and every decision needs them).
_NAME_OWNER: dict[str, str] = {
    "ToolFacts": "tool_facts",
    "classify_tool": "tool_facts",
    "command_surface": "tool_facts",
    "decide_activation": "activation",
    "entry_persona_context": "activation",
    "record_agent_miss": "record",
    "decide_task_context": "task_context",
    "decide_tool_policy": "tool_policy",
    "decide_evidence_trace": "evidence_trace",
    "_gate_summary": "gate_summary",
    "decide_evidence_gate": "evidence_gate",
    "_unevaluable_gate": "evidence_gate",
    "_BLOCKING_PROFILES": "evidence_gate",
    "decide_subagent_gate": "subagent_gate",
    "decide_context_capsule": "context_capsule",
}


def __getattr__(name: str) -> Any:
    owner = _NAME_OWNER.get(name)
    if owner is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(f"{__name__}.{owner}")
    value = getattr(module, name)
    globals()[name] = value  # cache on the package module itself: one import, then a plain attribute
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__) | set(_NAME_OWNER))
