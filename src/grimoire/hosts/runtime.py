"""The wire layer: one hook process, every host's JSON dialect.

:mod:`grimoire.hosts.decisions` decides; this module translates. Hosts agree
more than they look: all of them hand a hook a JSON payload on stdin and read a
JSON verdict on stdout. They disagree on key casing (``tool_name`` vs
``toolName``), on event spelling, on which field carries a refusal, and on what
a non-zero exit code means.

Keeping that translation in one place is what makes the promise checkable: the
same rule, the same refusal, the same wording, under Claude Code and under
Copilot — and a new host costs a table entry, not a second implementation of
the governance.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.bridges.schemas import HostId
from grimoire.core.standard_state import active_task_id, suspended_state_writes
from grimoire.hosts import copilot_wire, gemini_wire
from grimoire.hosts.capabilities import profile_for
from grimoire.hosts.decisions import (
    ADVISORY_PRE_TOOL_DECISIONS,
    DEFAULT_DECISION_BY_EVENT,
    Decision,
    HookInput,
    Outcome,
    run_decision,
)

# From the leaf module (issue #419), not ``grimoire.hosts.surface``: this is
# the hook entry point, invoked on every tool call — pulling the full
# agent/model IR for one enum was the tax the split removes.
from grimoire.hosts.events import NOT_EMITTED_EVENTS, HookEvent

#: Neutral event -> the name a host writes in its configuration and payloads.
#: Claude Code and VS Code Copilot happen to share the spelling; the table is
#: still per-host so the next one that does not can be added without a branch.
_WIRE_EVENT_NAMES: dict[HookEvent, str] = {
    HookEvent.SESSION_START: "SessionStart",
    HookEvent.USER_PROMPT_SUBMIT: "UserPromptSubmit",
    HookEvent.PRE_TOOL_USE: "PreToolUse",
    HookEvent.POST_TOOL_USE: "PostToolUse",
    HookEvent.POST_TOOL_USE_FAILURE: "PostToolUseFailure",
    HookEvent.SUBAGENT_START: "SubagentStart",
    HookEvent.SUBAGENT_STOP: "SubagentStop",
    HookEvent.PRE_COMPACT: "PreCompact",
    HookEvent.STOP: "Stop",
    HookEvent.TASK_CREATED: "TaskCreated",
    HookEvent.TASK_COMPLETED: "TaskCompleted",
    HookEvent.INSTRUCTIONS_LOADED: "InstructionsLoaded",
}

_EVENT_BY_WIRE_NAME: dict[str, HookEvent] = {v.lower(): k for k, v in _WIRE_EVENT_NAMES.items()}


def wire_event_name(event: HookEvent, host_id: HostId | None = None) -> str:
    """Name *event* the way *host_id* spells it in configuration files."""
    del host_id  # single spelling today; parameter keeps call sites future-proof
    return _WIRE_EVENT_NAMES[event]


def parse_event(name: str, host_id: HostId | None = None) -> HookEvent | None:
    """Accept ``PreToolUse``, ``pre_tool_use`` or ``pre-tool-use`` alike.

    A host whose event names are not the Claude Code spelling is asked first:
    Gemini CLI writes ``BeforeTool``, ``BeforeAgent``, ``PreCompress`` and so
    on (see :mod:`grimoire.hosts.gemini_wire`).
    """
    if host_id is HostId.GEMINI_CLI:
        gemini = gemini_wire.event_from_gemini(name)
        if gemini is not None:
            return gemini
    key = name.strip().replace("-", "_").replace(" ", "").lower()
    if key in _EVENT_BY_WIRE_NAME:
        return _EVENT_BY_WIRE_NAME[key]
    compact = key.replace("_", "")
    for wire, event in _EVENT_BY_WIRE_NAME.items():
        if wire.replace("_", "") == compact:
            return event
    # Copilot écrit ``userPromptSubmitted`` et ``agentStop`` (dialecte camelCase).
    copilot = copilot_wire.event_from_alias(compact)
    if copilot is not None:
        return copilot
    try:
        return HookEvent(key)
    except ValueError:
        return None


def _pick(payload: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return default


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def payload_event_name(payload: dict[str, Any]) -> str:
    """The event name a payload carries, in either key spelling."""
    return str(_pick(payload, "hook_event_name", "hookEventName", "event", default="") or "")


def decode_hook_payload(raw: str) -> tuple[dict[str, Any], str]:
    """Decode stdin into ``(payload, problem)``.

    ``problem`` names why the input cannot be judged — empty stdin, invalid
    JSON, or a JSON value that is not an object — and is empty when the payload
    is usable. The entry points decide what a problem means for their host: a
    non-blocking Gemini event keeps the legacy ``{}``, a blocking one fails
    closed.
    """
    if not raw.strip():
        return {}, "stdin vide"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, f"JSON invalide ({exc.msg})"
    if not isinstance(parsed, dict):
        return {}, f"charge utile de type {type(parsed).__name__}, pas un objet"
    return parsed, ""


def gemini_fail_closed_reason(
    host_id: HostId, event: HookEvent | None, payload_problem: str
) -> str:
    """Why a Gemini hook must exit 2 before deciding, or ``""`` to proceed.

    Two cases have no verdict to render: an event the host never named (no
    fallback to ``SessionStart``), and an unreadable payload on an event that
    can block. Both are refusals, not silent passes.
    """
    if host_id is not HostId.GEMINI_CLI:
        return ""
    if event is None:
        return "événement inconnu ou absent : aucun verdict ne peut être rendu"
    if payload_problem and event in gemini_wire.BLOCKING_EVENTS:
        return f"entrée injugeable ({payload_problem}) sur un événement bloquant"
    return ""


#: Le marqueur d'un projet Grimoire : le dossier qu'on trouve à sa racine.
PROJECT_MARKER = "_grimoire"
_ROOT_PAYLOAD_KEYS = ("cwd", "workspaceFolder", "workspace_folder", "project_root", "projectRoot")
#: ``COPILOT_WORKSPACE_FOLDER`` : heuristique, aucune source documentée (les références
#: GitHub et VS Code des hooks ne la citent pas) ; le candidat reste borné par le
#: marqueur ``_grimoire/``.
_ROOT_ENV_VARS = ("GRIMOIRE_PROJECT_ROOT", "COPILOT_WORKSPACE_FOLDER")


def _nearest_project_root(start: Path) -> Path | None:
    """Le plus proche ancêtre de *start* (lui compris) qui porte ``_grimoire/``."""
    for candidate in (start, *start.parents):
        if (candidate / PROJECT_MARKER).is_dir():
            return candidate
    return None


def locate_project_root(payload: dict[str, Any] | None = None, explicit: Path | None = None) -> tuple[Path, bool]:
    """``(racine, reconnue)`` pour un run de hook.

    Ordre : ``--project-root`` ; ``CLAUDE_PROJECT_DIR`` (Claude Code l'exporte
    et il reste la racine du projet) ; puis le ``cwd`` du payload ou les
    variables des autres hôtes. Un ``cwd`` n'énonce pas le projet : sur Claude
    Code c'est le répertoire courant du shell, qui suit chaque ``cd`` de
    l'agent (issue #717). On remonte donc jusqu'au premier dossier qui porte
    ``_grimoire/`` ; sans marqueur, le candidat est gardé tel quel mais la
    racine n'est pas « reconnue » (aucun hook n'y écrit d'état).
    """
    if explicit is not None:
        return explicit.resolve(), True
    stated = os.environ.get("CLAUDE_PROJECT_DIR", "").strip()
    if stated:
        return Path(stated).resolve(), True
    data = payload or {}
    candidates: list[str] = []
    picked = _pick(data, *_ROOT_PAYLOAD_KEYS)
    if isinstance(picked, str) and picked.strip():
        candidates.append(picked)
    candidates.extend(v for v in (os.environ.get(var, "").strip() for var in _ROOT_ENV_VARS) if v)
    first: Path | None = None
    for raw in candidates:
        path = Path(raw).resolve()
        first = first or path
        found = _nearest_project_root(path)
        if found is not None:
            return found, True
    start = first or Path.cwd().resolve()
    found = _nearest_project_root(start) if first is None else None
    return (found, True) if found is not None else (start, False)


def resolve_project_root(payload: dict[str, Any] | None = None, explicit: Path | None = None) -> Path:
    """Project root for a hook run — see :func:`locate_project_root`."""
    return locate_project_root(payload, explicit)[0]


def normalize_input(
    payload: dict[str, Any],
    *,
    event: HookEvent | None = None,
    project_root: Path | None = None,
    host_id: HostId | None = None,
) -> HookInput:
    """Flatten a host payload into the neutral :class:`HookInput`."""
    wire_event = payload_event_name(payload)
    resolved = event or (parse_event(wire_event, host_id=host_id) if wire_event else None)
    if resolved is None:
        if host_id is HostId.GEMINI_CLI:
            # Pas de repli sur le début de session : un événement que l'hôte ne
            # nomme pas n'est pas un `SessionStart`, c'est une entrée injugeable.
            raise gemini_wire.UnjudgeableInputError(
                f"événement inconnu ou absent ({wire_event or 'aucun nom dans la charge utile'})"
            )
        resolved = HookEvent.SESSION_START
    stop_active = bool(_pick(payload, "stop_hook_active", "stopHookActive", default=False))
    tool_name = str(_pick(payload, "tool_name", "toolName", "tool", default="") or "")
    tool_input = _as_dict(_pick(payload, "tool_input", "toolInput", "input", "arguments"))
    if not tool_name:
        # Dialecte camelCase de Copilot : ``toolCalls`` (un seul appel) ou
        # ``toolName`` déjà lu ci-dessus ; un lot de plusieurs appels reste sans nom.
        call = copilot_wire.single_tool_call(payload)
        if call is not None:
            tool_name, tool_input = call
    elif not tool_input and "toolArgs" in payload:
        # ``toolArgs`` : objet ou chaîne JSON selon l'événement (journal de l'hôte).
        tool_input = copilot_wire.parse_tool_args(payload["toolArgs"])
    if host_id is HostId.GEMINI_CLI:
        # Gemini names its tools `write_file`/`replace`/`run_shell_command`; the
        # policy engine classifies on the kit's classes. On a pending tool call,
        # an unknown tool is routed by its payload (path -> write, command ->
        # shell); empty or field-less input fails closed (see `gemini_wire`).
        if resolved is HookEvent.PRE_TOOL_USE:
            tool_name, tool_input = gemini_wire.route_tool_call(tool_name, tool_input)
        else:
            tool_name = gemini_wire.canonical_tool_name(tool_name)
    return HookInput(
        event=resolved,
        project_root=resolve_project_root(payload, project_root),
        tool_name=tool_name,
        tool_input=tool_input,
        tool_response=_as_dict(_pick(payload, "tool_response", "toolResponse", "toolResult", "result")),
        prompt=str(_pick(payload, "prompt", "userPrompt", "user_prompt", default="") or ""),
        # Issue #716 : ``agent_type`` (session ouverte par ``claude --agent``,
        # ou événement d'un sous-agent) est l'identité que l'hôte fournit
        # réellement ; ``agent_name`` la reprend quand rien de plus explicite
        # n'est envoyé, pour que la trace nomme l'agent en jeu.
        agent_name=str(
            _pick(payload, "agent_name", "agentName", "subagent", "agent_type", "agentType", default="") or ""
        ),
        agent_type=str(_pick(payload, "agent_type", "agentType", default="") or ""),
        agent_id=str(_pick(payload, "agent_id", "agentId", default="") or ""),
        session_id=str(_pick(payload, "session_id", "sessionId", default="") or ""),
        stop_active=stop_active,
        host=host_id.value if host_id is not None and host_id is not HostId.UNKNOWN else "",
        tool_use_id=str(_pick(payload, "tool_use_id", "toolUseId", "tool_call_id", "toolCallId", default="") or ""),
        raw=dict(payload),
    )


def _persist_capsule(hook: HookInput, decision: Decision) -> tuple[Path | None, str]:
    """Write the pre-compaction capsule where the next window can find it.

    No host injects context *into* a compaction, so a capsule that only exists
    in the hook's stdout dies with the old window. On disk it outlives it, and
    the session-start decision can read it back.

    Returns ``(path, "")`` when written, ``(None, cause)`` when the disk
    refused — the caller says so instead of announcing a capsule that is not
    there.
    """
    if hook.event is not HookEvent.PRE_COMPACT or not decision.context:
        return None, ""
    task_id = str(decision.detail.get("task_id") or active_task_id(hook.project_root, session_id=hook.session_id, agent_id=hook.agent_id))
    from grimoire.hosts.decisions.resume_capsule import capsule_path, write_capsule

    dest = capsule_path(hook.project_root, task_id)
    try:
        write_capsule(hook.project_root, task_id, decision.context, session_id=hook.session_id)
    except OSError as exc:
        return None, f"{dest}: {type(exc).__name__}: {exc}"
    return dest, ""


def render(decision: Decision, hook: HookInput, host_id: HostId) -> dict[str, Any]:
    """Render *decision* into the JSON *host_id* understands for this event."""
    if decision.abstain:
        # Pas d'avis : rien à rendre, l'hôte poursuit comme sans hook.
        return {}
    if host_id is HostId.GEMINI_CLI:
        # Gemini's dialect is a top-level `decision`/`reason` and
        # `additionalContext`, not Claude Code's `hookSpecificOutput`.
        return gemini_wire.render(decision, hook)
    event_name = wire_event_name(hook.event, host_id)
    payload: dict[str, Any] = {}
    specific: dict[str, Any] = {"hookEventName": event_name}

    if hook.event is HookEvent.PRE_TOOL_USE:
        if profile_for(host_id).blocking_hooks:
            mapping = {Outcome.DENY: "deny", Outcome.ASK: "ask", Outcome.ALLOW: "allow"}
            specific["permissionDecision"] = mapping.get(decision.outcome, "allow")
            if decision.reason:
                specific["permissionDecisionReason"] = decision.reason
            if decision.updated_input is not None and decision.outcome is Outcome.ALLOW:
                # L'entrée complète : Claude Code remplace ``tool_input``, il ne fusionne pas.
                specific["updatedInput"] = dict(decision.updated_input)
        elif decision.reason:
            # A host that cannot refuse or ask is at least told why it should.
            specific["additionalContext"] = decision.reason
        if decision.context and decision.outcome is Outcome.ALLOW:
            # Issue #724 : un avertissement non bloquant accompagne un `allow`,
            # dans `additionalContext` pour l'agent. L'avertissement de code
            # interprété n'est plus recopié en `systemMessage` : l'agent le
            # reçoit déjà, le doublon ne faisait que l'afficher deux fois.
            specific["additionalContext"] = decision.context
            if not decision.detail.get("interpreted_code"):
                payload["systemMessage"] = decision.context
        payload["hookSpecificOutput"] = specific
        return payload

    if hook.event in {HookEvent.STOP, HookEvent.SUBAGENT_STOP}:
        if decision.outcome is Outcome.BLOCK and profile_for(host_id).blocking_hooks:
            payload["decision"] = "block"
            payload["reason"] = decision.reason
            return payload
        if decision.context:
            specific["additionalContext"] = decision.context
            payload["hookSpecificOutput"] = specific
            # No host reads additionalContext on a stop event; a non-blocking
            # verdict that lived only there was a verdict nobody saw.
            payload["systemMessage"] = decision.context
        return payload

    if decision.context:
        specific["additionalContext"] = decision.context
        payload["hookSpecificOutput"] = specific
    if hook.event is HookEvent.PRE_COMPACT and decision.context:
        # Nothing consumes additionalContext during a compaction; say plainly
        # where the capsule went — or that it went nowhere — instead of
        # pretending it was injected.
        capsule_error = str(decision.detail.get("capsule_error") or "")
        payload["systemMessage"] = (
            f"[Grimoire] capsule de gouvernance non écrite avant compaction ({capsule_error}) : "
            "le contexte de tâche ne survivra pas à la compaction."
            if capsule_error
            else "[Grimoire] capsule de gouvernance écrite avant compaction."
        )
    return payload


#: Neutral outcome -> the verdict word the ledger's ``policy_block_rate`` counts.
#: ``deny`` is written as ``block`` on purpose: the metric predates this layer
#: and counts ``"block"``, and renaming the value would silently zero it.
_LEDGER_VERDICT = {
    Outcome.DENY: "block",
    Outcome.BLOCK: "block",
    Outcome.ASK: "ask",
    Outcome.ALLOW: "allow",
}

#: Events worth a ledger line. Read-only tool calls are deliberately absent:
#: they return before the policy engine runs, so recording them would add file
#: I/O to the one path this layer just spent a chantier making cheap.
_RECORDED_EVENTS = {
    HookEvent.PRE_TOOL_USE,
    HookEvent.STOP,
    HookEvent.SUBAGENT_STOP,
    *NOT_EMITTED_EVENTS,
}


#: Attente maximale (s) du verrou du journal de traces depuis un hook. La trace
#: est best-effort, la réponse à l'hôte ne l'est pas : avec le délai par défaut
#: de 30 s, des hooks concurrents s'empilaient derrière le verrou et la garde
#: ``PreToolUse`` en échec fermé dépassait son ``timeout`` (refus de l'hôte).
_LEDGER_LOCK_TIMEOUT_S = 2.0


def _record_decision(
    hook: HookInput,
    decision: Decision,
    host_id: HostId,
    latency_ms: float,
    started_at: str | None = None,
    decision_id: str = "",
) -> None:
    """Append what the decision did to the project's trace ledger.

    The ledger and this layer were built for each other and never connected:
    ``ToolCallTrace`` carries a ``policy_verdict_id``, ``TraceRecord`` carries
    evidence refs, and ``policy_block_rate()`` documents itself as "fraction of
    tool calls that were blocked" — a number that could only ever read zero
    while the hooks wrote nothing. Governance that leaves no trace cannot be
    measured, and an unmeasured guardrail is a claim.

    Best-effort by construction: a ledger that cannot be written must never
    fail a session. The import is deferred because it costs 29 ms, which the
    read-only path has no reason to pay.
    """
    if hook.event not in _RECORDED_EVENTS or not decision.detail:
        return
    try:
        from grimoire.core.standard_generation import TRACES_DIR
        from grimoire.hosts.decisions.done_gate import done_gate_mode_tags
        from grimoire.traces.ledger import TraceLedger
        from grimoire.traces.schemas import TraceOutcome

        detail = decision.detail
        # Le seul flux qui ignorait la tâche : la décision de policy ne la porte
        # pas dans son détail, donc chaque refus d'outil était journalisé sous
        # un task_id vide et `grimoire task trace` ne pouvait pas le retrouver.
        # Le gateway est l'unique écrivain de ce ledger ; c'est lui qui résout.
        task_id = str(detail.get("task_id") or active_task_id(hook.project_root, session_id=hook.session_id, agent_id=hook.agent_id))
        verdict = _LEDGER_VERDICT.get(decision.outcome, "allow")
        record_started_at = started_at or _now_iso()
        tool_calls = []
        if hook.event is HookEvent.PRE_TOOL_USE:
            tool_calls.append(
                {
                    "tool": hook.tool_name or "unknown",
                    "verdict": verdict,
                    "args_hash": _args_hash(hook.tool_input),
                    "latency_ms": latency_ms,
                    # Real wall-clock instant the tool call was decided, not
                    # the record-time timestamp: the OTel export needs the
                    # call's own timing, and falling back to the trace's
                    # ``started_at`` would silently reuse a later moment.
                    "timestamp": record_started_at,
                }
            )
        TraceLedger(hook.project_root / TRACES_DIR).record(
            run_id=hook.session_id or "session-unknown",
            workflow_instance_id="",
            mission_id="",
            task_id=task_id,
            recipe_id=decision_id or DEFAULT_DECISION_BY_EVENT.get(hook.event, hook.event.value),
            outcome=TraceOutcome.FAILURE if decision.is_refusal else TraceOutcome.SUCCESS,
            started_at=record_started_at,
            # Quel agent était en jeu, pour que le cockpit puisse répondre à
            # « qui a été choisi et quand » (#374) — `SubagentStop` est le seul
            # événement qui porte un nom de sous-agent utile ; ailleurs
            # `hook.agent_name` est vide et `TraceRecord.agent_id` garde son
            # défaut, comme avant ce correctif.
            agent_id=hook.agent_name,
            host_id=host_id.value,
            tool_calls=tool_calls,
            evidence_refs=[f"_grimoire-output/evidence/{task_id}"] if task_id else [],
            latency_ms=latency_ms,
            tags=[
                hook.event.value,
                verdict,
                *_refusal_tags(decision),
                *done_gate_mode_tags(detail.get("done_gate")),
                *_dispatch_model_tags(detail),
                *([f"batch_outcome:{detail['batch_outcome']}"] if detail.get("batch_outcome") else []),
            ],
            lock_timeout=_LEDGER_LOCK_TIMEOUT_S,
        )
    except Exception:
        return


def _dispatch_model_tags(detail: dict[str, Any]) -> list[str]:
    """``dispatch_model:<modèle>`` et ``dispatch_source:<class|heuristic>`` : le modèle imposé à un sous-agent, relisible au ledger."""
    model = detail.get("imposed_model")
    if not model:
        return []
    return [f"dispatch_model:{model}", f"dispatch_source:{detail.get('dispatch_source') or 'unknown'}"]


def _refusal_tags(decision: Decision) -> list[str]:
    """``refusal:<motif>`` sur un refus : pourquoi la clôture ou l'outil a été refusé, relisible au ledger (D-12).

    Sans lui, une trace ``stop``/``block`` ne disait pas si la tâche était tenue sans être rendue
    (``task_unparked``), sans tâche (``no_active_task``), aux gates rouges (``gates_red``) ou si la
    garde elle-même était en panne (``guard_error``) : l'e2e live devait le déduire du texte de l'agent.
    """
    if not decision.is_refusal:
        return []
    detail = decision.detail
    if detail.get("blocked_on"):
        kind = str(detail["blocked_on"])
    elif detail.get("guard_error"):
        kind = "guard_error"
    elif detail.get("ok") is False:
        kind = "gates_red"
    else:
        kind = "other"
    return [f"refusal:{kind}"]


def _args_hash(tool_input: dict[str, Any]) -> str:
    """Short digest of a call's arguments — enough to correlate, not to leak.

    Tool arguments carry file contents, commands and occasionally credentials.
    The ledger is written to disk and exported to OTel, so it gets a digest.
    """
    try:
        payload = json.dumps(tool_input, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        payload = repr(tool_input)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _decide(hook: HookInput, decision_id: str, recognized: bool) -> Decision:
    """Une décision sur *hook* ; hors racine reconnue, la garde décide sans écrire d'état (#717)."""
    if not recognized and hook.event is not HookEvent.PRE_TOOL_USE:
        return Decision()
    if not decision_id:
        return Decision()
    if not recognized:
        with suspended_state_writes():
            return run_decision(decision_id, hook)
    return run_decision(decision_id, hook)


def _unreadable_call_decision(problem: str) -> Decision:
    """Le refus d'un appel d'outil que la garde ne sait pas lire."""
    return Decision(
        outcome=Outcome.DENY,
        reason=(
            f"[Grimoire policy] appel d'outil refusé — {problem} : "
            "un appel que la garde ne sait pas lire n'est pas un appel autorisé."
        ),
        detail={"tool": "", "unreadable_tool_call": problem, "blocked_on": "unreadable_tool_call"},
    )


def _combine_batch(judged: list[tuple[HookInput, Decision]]) -> Decision:
    """Le verdict d'un lot d'appels : refus > demande > autorisation, jamais le premier seul."""
    size = len(judged)
    if size == 1:
        return judged[0][1]
    tools = [sub.tool_name for sub, _ in judged]
    for refusal_wanted in (True, False):
        for index, (sub, decision) in enumerate(judged, start=1):
            hit = decision.is_refusal if refusal_wanted else decision.outcome is Outcome.ASK
            if hit:
                return Decision(
                    outcome=Outcome.DENY if refusal_wanted else Outcome.ASK,
                    reason=f"{decision.reason} (appel {index}/{size} d'un lot : {sub.tool_name})",
                    detail={**decision.detail, "batch_size": size, "batch_index": index, "batch_tools": tools},
                )
    for index, (sub, decision) in enumerate(judged, start=1):
        if decision.updated_input is not None:
            # Une réécriture vise un appel ; l'hôte ne sait pas l'appliquer à un seul appel d'un lot.
            return Decision(
                outcome=Outcome.ASK,
                reason=(
                    f"[Grimoire policy] {sub.tool_name} (appel {index}/{size} d'un lot) : la politique "
                    "réécrirait cet appel, ce qu'un lot ne permet pas — à toi de décider."
                ),
                detail={**decision.detail, "batch_size": size, "batch_index": index, "batch_tools": tools},
            )
    contexts = list(dict.fromkeys(d.context for _, d in judged if d.context))
    return Decision(
        outcome=Outcome.ALLOW,
        context="\n".join(contexts),
        detail={"batch_size": size, "batch_tools": tools},
    )


def run_hook(
    payload: dict[str, Any],
    *,
    host_id: HostId,
    event: HookEvent | None = None,
    project_root: Path | None = None,
    decision_id: str | None = None,
) -> tuple[dict[str, Any], Decision, HookInput]:
    """Full path: normalise, decide, persist side effects, render."""
    hook = normalize_input(payload, event=event, project_root=project_root, host_id=host_id)
    resolved_decision = decision_id or DEFAULT_DECISION_BY_EVENT.get(hook.event, "")
    started_at = _now_iso()
    started = time.perf_counter()
    # Hors d'une racine reconnue (aucun `_grimoire/` au-dessus du cwd) il n'y a pas
    # de projet auquel rattacher un état. Les décisions qui écrivent (`_grimoire-output/`)
    # sont inertes ; PreToolUse, le garde-fou, garde sa décision mais en lecture
    # seule : ni cache d'état, ni hold, ni trace, ni capsule (#717).
    recognized = locate_project_root(payload, project_root)[1]
    # La garde : la politique d'outils sur un appel en attente. Une décision consultative
    # (modèle imposé) ou une autre décision nommée par ``--decision`` n'est pas concernée.
    guard = (
        hook.event is HookEvent.PRE_TOOL_USE and resolved_decision == DEFAULT_DECISION_BY_EVENT[HookEvent.PRE_TOOL_USE]
    )
    batch = copilot_wire.inspect_tool_calls(payload) if guard else None
    judged: list[tuple[HookInput, Decision]]
    if batch is not None and batch[1]:
        # Échec fermé : une charge qui annonce des appels sans qu'on puisse tous les lire.
        decision = _unreadable_call_decision(batch[1])
        judged = [(hook, decision)]
    elif batch is not None:
        # Forme camelCase (Copilot) : chaque appel inspecté est jugé, même seul, et
        # le plus restrictif l'emporte. Le hook normalisé n'est jamais jugé à sa place.
        judged = [
            (sub, _decide(sub, resolved_decision, recognized))
            for sub in (
                replace(hook, tool_name=call.name, tool_input=call.args, tool_use_id=call.call_id or hook.tool_use_id)
                for call in batch[0]
            )
        ]
        decision = _combine_batch(judged)
        if len(judged) > 1:
            # La trace de chaque appel dit ce qu'il est advenu du lot (un `allow` d'un lot refusé n'a pas été exécuté).
            judged = [
                (
                    sub,
                    replace(d, detail={**d.detail, "batch_outcome": decision.outcome.value, "batch_size": len(judged)}),
                )
                for sub, d in judged
            ]
    elif guard and not hook.tool_name:
        # Sans nom d'outil, la politique n'a rien à juger : ce n'est pas une autorisation.
        decision = _unreadable_call_decision("aucun nom d'outil exploitable dans la charge")
        judged = [(hook, decision)]
    else:
        decision = _decide(hook, resolved_decision, recognized)
        judged = [(hook, decision)]
    latency_ms = (time.perf_counter() - started) * 1000
    if recognized:
        for judged_hook, judged_decision in judged:
            _record_decision(judged_hook, judged_decision, host_id, latency_ms, started_at, resolved_decision)
        capsule, capsule_error = _persist_capsule(hook, decision)
    else:
        capsule, capsule_error = None, ""
    if capsule is not None or capsule_error:
        stamp = {"capsule": str(capsule)} if capsule is not None else {"capsule_error": capsule_error}
        decision = Decision(
            outcome=decision.outcome,
            reason=decision.reason,
            context=decision.context,
            detail={**decision.detail, **stamp},
        )
    return render(decision, hook, host_id), decision, hook


def guard_failure_verdict(cause: str) -> dict[str, Any]:
    """Le refus ``PreToolUse`` rendu quand la garde elle-même est en panne (voir :mod:`.hook_entry`)."""
    from grimoire.hosts.hook_entry import guard_failure_payload

    return guard_failure_payload(cause)


def main(argv: list[str] | None = None) -> int:
    """Entry point used by generated hook configurations.

    Reads the host payload on stdin and writes the host verdict on stdout.
    Exits 0 for every host that reads its verdict from the JSON — a non-zero
    exit would turn a policy decision into an unexplained host error.

    Gemini CLI is the exception, and only where it must be: its hook protocol
    treats a non-zero exit as "let the action through" unless the event can
    block, where exit 2 plus a reason on stderr is the documented refusal. So
    an unjudgeable input (empty or malformed payload, unknown event, empty tool
    name, unknown tool with no command or path) and an internal error on
    ``BeforeTool``/``BeforeAgent`` exit 2 (fail closed) instead of printing a
    verdict nobody asked for; on the advisory events an internal error warns
    and stays out of the way.
    """
    from grimoire.core.console_encoding import enable_utf8_output

    enable_utf8_output()
    args = list(sys.argv[1:] if argv is None else argv)
    host_name = ""
    event_name = ""
    root: Path | None = None
    decision_id: str | None = None
    it = iter(range(len(args)))
    for i in it:
        arg = args[i]
        nxt = args[i + 1] if i + 1 < len(args) else ""
        if arg == "--host":
            host_name = nxt
        elif arg == "--event":
            event_name = nxt
        elif arg == "--project-root":
            root = Path(nxt)
        elif arg == "--decision" and decision_id is None:
            # Première occurrence, comme ``hook_entry._option`` : les deux lectures
            # doivent désigner la même décision (consultative ou garde).
            decision_id = nxt

    from grimoire.hosts.capabilities import resolve_host

    host_id = resolve_host(host_name) or HostId.UNKNOWN
    event = parse_event(event_name, host_id=host_id) if event_name else None
    try:
        raw = sys.stdin.read()
    except (OSError, ValueError):
        raw = ""
    payload, payload_problem = decode_hook_payload(raw)

    wire_event = payload_event_name(payload)
    if event is None and wire_event:
        # Fail-closed must hold even when the host's configuration forgets
        # ``--event``: the payload still names it.
        event = parse_event(wire_event, host_id=host_id)
    label = event_name or wire_event or "événement inconnu"

    if refusal := gemini_fail_closed_reason(host_id, event, payload_problem):
        # No decision can be rendered: exit 2 is Gemini's documented refusal,
        # and the reason says why. Never a silent pass on an unjudgeable input.
        sys.stderr.write(f"[Grimoire] hook Gemini en échec sur {label} ({refusal}) : action refusée par défaut.\n")
        return 2

    try:
        result, _decision, _hook = run_hook(
            payload, host_id=host_id, event=event, project_root=root, decision_id=decision_id
        )
        rendered = json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        cause = f"{type(exc).__name__}: {exc}"
        if decision_id in ADVISORY_PRE_TOOL_DECISIONS:
            # Décision consultative (imposer un modèle à un sous-agent) : sa panne
            # laisse passer l'appel tel quel, jamais un refus de garde.
            sys.stdout.write("{}")
            return 0
        if host_id is not HostId.GEMINI_CLI:
            if event is not HookEvent.PRE_TOOL_USE:
                # Événements consultatifs : la panne remonte comme avant (erreur
                # non bloquante de l'hôte), jamais déguisée en verdict.
                raise
            # La garde est émise en échec fermé (``onFailure: block``) : un
            # plantage refuserait l'outil avec un message de l'hôte qui ne dit
            # pas quoi faire. Le refus explicite dit d'où vient la panne.
            sys.stdout.write(json.dumps(guard_failure_verdict(cause), ensure_ascii=False))
            return 0
        blocked_event = event if event in gemini_wire.BLOCKING_EVENTS else None
        sys.stderr.write(
            f"[Grimoire] hook Gemini en échec sur {label} ({cause}) : "
            f"{'action refusée par défaut' if blocked_event else 'action laissée passer (événement non bloquant)'}.\n"
        )
        if blocked_event is None:
            rendered = "{}"
        else:
            return 2
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
