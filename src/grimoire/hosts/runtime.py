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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.bridges.schemas import HostId
from grimoire.core.standard_generation import CONTEXT_DIR
from grimoire.core.standard_state import active_task_id
from grimoire.hosts.capabilities import profile_for
from grimoire.hosts.decisions import (
    DEFAULT_DECISION_BY_EVENT,
    Decision,
    HookInput,
    Outcome,
    run_decision,
)
from grimoire.hosts.surface import HookEvent

#: Neutral event -> the name a host writes in its configuration and payloads.
#: Claude Code and VS Code Copilot happen to share the spelling; the table is
#: still per-host so the next one that does not can be added without a branch.
_WIRE_EVENT_NAMES: dict[HookEvent, str] = {
    HookEvent.SESSION_START: "SessionStart",
    HookEvent.USER_PROMPT_SUBMIT: "UserPromptSubmit",
    HookEvent.PRE_TOOL_USE: "PreToolUse",
    HookEvent.POST_TOOL_USE: "PostToolUse",
    HookEvent.SUBAGENT_STOP: "SubagentStop",
    HookEvent.PRE_COMPACT: "PreCompact",
    HookEvent.STOP: "Stop",
}

_EVENT_BY_WIRE_NAME: dict[str, HookEvent] = {v.lower(): k for k, v in _WIRE_EVENT_NAMES.items()}


def wire_event_name(event: HookEvent, host_id: HostId | None = None) -> str:
    """Name *event* the way *host_id* spells it in configuration files."""
    del host_id  # single spelling today; parameter keeps call sites future-proof
    return _WIRE_EVENT_NAMES[event]


def parse_event(name: str) -> HookEvent | None:
    """Accept ``PreToolUse``, ``pre_tool_use`` or ``pre-tool-use`` alike."""
    key = name.strip().replace("-", "_").replace(" ", "").lower()
    if key in _EVENT_BY_WIRE_NAME:
        return _EVENT_BY_WIRE_NAME[key]
    compact = key.replace("_", "")
    for wire, event in _EVENT_BY_WIRE_NAME.items():
        if wire.replace("_", "") == compact:
            return event
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


def resolve_project_root(payload: dict[str, Any] | None = None, explicit: Path | None = None) -> Path:
    """Project root for a hook run.

    Order: an explicit ``--project-root``, then the host's own signal (its
    payload ``cwd`` or the project-dir variable it exports), then the process
    working directory. A hook that guesses this wrong evaluates the gates of
    the wrong project, so every candidate is a value the host actually stated.
    """
    if explicit is not None:
        return explicit.resolve()
    data = payload or {}
    candidate = _pick(data, "cwd", "workspaceFolder", "workspace_folder", "project_root", "projectRoot")
    if isinstance(candidate, str) and candidate.strip():
        return Path(candidate).resolve()
    for var in ("CLAUDE_PROJECT_DIR", "GRIMOIRE_PROJECT_ROOT", "COPILOT_WORKSPACE_FOLDER"):
        value = os.environ.get(var, "").strip()
        if value:
            return Path(value).resolve()
    return Path.cwd().resolve()


def normalize_input(
    payload: dict[str, Any],
    *,
    event: HookEvent | None = None,
    project_root: Path | None = None,
) -> HookInput:
    """Flatten a host payload into the neutral :class:`HookInput`."""
    wire_event = _pick(payload, "hook_event_name", "hookEventName", "event", default="")
    resolved = event or (parse_event(str(wire_event)) if wire_event else None) or HookEvent.SESSION_START
    stop_active = bool(_pick(payload, "stop_hook_active", "stopHookActive", default=False))
    return HookInput(
        event=resolved,
        project_root=resolve_project_root(payload, project_root),
        tool_name=str(_pick(payload, "tool_name", "toolName", "tool", default="") or ""),
        tool_input=_as_dict(_pick(payload, "tool_input", "toolInput", "input", "arguments")),
        tool_response=_as_dict(_pick(payload, "tool_response", "toolResponse", "result")),
        prompt=str(_pick(payload, "prompt", "userPrompt", "user_prompt", default="") or ""),
        agent_name=str(_pick(payload, "agent_name", "agentName", "subagent", default="") or ""),
        session_id=str(_pick(payload, "session_id", "sessionId", default="") or ""),
        stop_active=stop_active,
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
    task_id = str(decision.detail.get("task_id") or active_task_id(hook.project_root))
    dest = hook.project_root / CONTEXT_DIR / task_id / "compaction-capsule.md"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(decision.context + "\n", encoding="utf-8")
    except OSError as exc:
        return None, f"{dest}: {type(exc).__name__}: {exc}"
    return dest, ""


def render(decision: Decision, hook: HookInput, host_id: HostId) -> dict[str, Any]:
    """Render *decision* into the JSON *host_id* understands for this event."""
    event_name = wire_event_name(hook.event, host_id)
    payload: dict[str, Any] = {}
    specific: dict[str, Any] = {"hookEventName": event_name}

    if hook.event is HookEvent.PRE_TOOL_USE:
        if profile_for(host_id).blocking_hooks:
            mapping = {Outcome.DENY: "deny", Outcome.ASK: "ask", Outcome.ALLOW: "allow"}
            specific["permissionDecision"] = mapping.get(decision.outcome, "allow")
            if decision.reason:
                specific["permissionDecisionReason"] = decision.reason
        elif decision.reason:
            # A host that cannot refuse or ask is at least told why it should.
            specific["additionalContext"] = decision.reason
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
_RECORDED_EVENTS = {HookEvent.PRE_TOOL_USE, HookEvent.STOP, HookEvent.SUBAGENT_STOP}


def _record_decision(hook: HookInput, decision: Decision, host_id: HostId, latency_ms: float) -> None:
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
        from grimoire.traces.ledger import TraceLedger
        from grimoire.traces.schemas import TraceOutcome

        detail = decision.detail
        task_id = str(detail.get("task_id") or "")
        verdict = _LEDGER_VERDICT.get(decision.outcome, "allow")
        tool_calls = []
        if hook.event is HookEvent.PRE_TOOL_USE:
            tool_calls.append(
                {
                    "tool": hook.tool_name or "unknown",
                    "verdict": verdict,
                    "args_hash": _args_hash(hook.tool_input),
                    "latency_ms": latency_ms,
                }
            )
        TraceLedger(hook.project_root / TRACES_DIR).record(
            run_id=hook.session_id or "session-unknown",
            workflow_instance_id="",
            mission_id="",
            task_id=task_id,
            recipe_id=DEFAULT_DECISION_BY_EVENT.get(hook.event, hook.event.value),
            outcome=TraceOutcome.FAILURE if decision.is_refusal else TraceOutcome.SUCCESS,
            started_at=_now_iso(),
            host_id=host_id.value,
            tool_calls=tool_calls,
            evidence_refs=[f"_grimoire-output/evidence/{task_id}"] if task_id else [],
            latency_ms=latency_ms,
            tags=[hook.event.value, verdict],
        )
    except Exception:
        return


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


def run_hook(
    payload: dict[str, Any],
    *,
    host_id: HostId,
    event: HookEvent | None = None,
    project_root: Path | None = None,
    decision_id: str | None = None,
) -> tuple[dict[str, Any], Decision, HookInput]:
    """Full path: normalise, decide, persist side effects, render."""
    hook = normalize_input(payload, event=event, project_root=project_root)
    resolved_decision = decision_id or DEFAULT_DECISION_BY_EVENT.get(hook.event, "")
    started = time.perf_counter()
    decision = run_decision(resolved_decision, hook) if resolved_decision else Decision()
    latency_ms = (time.perf_counter() - started) * 1000
    _record_decision(hook, decision, host_id, latency_ms)
    capsule, capsule_error = _persist_capsule(hook, decision)
    if capsule is not None or capsule_error:
        stamp = {"capsule": str(capsule)} if capsule is not None else {"capsule_error": capsule_error}
        decision = Decision(
            outcome=decision.outcome,
            reason=decision.reason,
            context=decision.context,
            detail={**decision.detail, **stamp},
        )
    return render(decision, hook, host_id), decision, hook


def main(argv: list[str] | None = None) -> int:
    """Entry point used by generated hook configurations.

    Reads the host payload on stdin, writes the host verdict on stdout, and
    always exits 0: the verdict lives in the JSON, and a non-zero exit is how a
    hook turns a policy decision into an unexplained host error.
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
        elif arg == "--decision":
            decision_id = nxt

    from grimoire.hosts.capabilities import resolve_host

    host_id = resolve_host(host_name) or HostId.UNKNOWN
    event = parse_event(event_name) if event_name else None
    try:
        raw = sys.stdin.read()
    except (OSError, ValueError):
        raw = ""
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    result, _decision, _hook = run_hook(
        payload, host_id=host_id, event=event, project_root=root, decision_id=decision_id
    )
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
