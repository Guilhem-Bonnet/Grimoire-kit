"""Calibrating hook verdicts against what happens next (Refs #644).

Idea B of the party-mode governance debate (``_scratch/party-oss/
gouvernance.md``): a hook that refuses or asks (:mod:`.done_gate`'s ``stale``
verdict, :mod:`.tool_policy`'s ``ask``/``deny``) is a *hold* — and until now
nothing recorded what happened next. Two tags on the existing
:class:`grimoire.traces.ledger.TraceLedger` close that loop, the same
pattern :mod:`.record` already uses for ``agent.dispatch``/``agent.miss``:
no new journal, no model, no network.

Never "regret"
---------------
Every label this module writes is structural, never a judgment:
:data:`~grimoire.traces.ledger.HOLD_LABEL_RESPECTED`,
:data:`~grimoire.traces.ledger.HOLD_LABEL_RETRIED_SAME`,
:data:`~grimoire.traces.ledger.HOLD_LABEL_RETRIED_VARIANT` and
:data:`~grimoire.traces.ledger.HOLD_LABEL_ABANDONED` describe what the agent
did next, not whether the hold was right to fire. Calling a bare recurrence
"regret" would assert a second source of truth (a red test discovered
afterward, a revert) that does not exist yet — see the governance doc's
unresolved disagreement, arbitrated here in favour of the narrower
vocabulary.

Content never travels
----------------------
:func:`action_fingerprint` is one-way (a hash): a hold can exist *because*
the pending command carried a destructive pattern or a secret target
(``tool_policy.py``'s own ``what = facts.destructive_reason or
facts.secret_target or facts.kind.value``), so nothing this module persists
may let that content be reconstructed from the journal. :func:`target_key`
is coarser still — a bare verb or directory name, never a full path or
command line — used only to detect "the same kind of action retried",
never rendered as evidence of anything.

The hook path never reads ``traces.jsonl``
--------------------------------------------
First measured (A/B, median of 7, real ``grimoire-hook --event
post_tool_use`` subprocess): against a 200-line ``traces.jsonl``, the very
first version of :func:`record_hold_followup` — which looked up "the still
open hold" via ``TraceLedger.open_policy_hold``, itself a full parse of
``traces.jsonl`` — cost +10 ms per call, above the noise this feature was
supposed to stay under, and the cost only grows with the project's age
(every trace type ever written, not only holds, sits in that one file).
``PostToolUse`` runs on *every* tool call of *every* session; a hook budget
cannot afford a read that scales with how long the project has existed.

The fix: "what hold is still open for this session" now lives in its own
small, bounded, per-session state file (:func:`_hold_state_path`, same
shape as :mod:`.session_memory`'s own ``memory-<session>.json`` — atomic
write, best-effort, capped at :data:`_MAX_OPEN_HOLDS` entries so it can
never grow with the ledger). :func:`record_policy_hold` and
:func:`record_hold_followup` read and write *only* this file on the hook
path; the ``TraceLedger`` only ever receives an append (``.record(...)``
with an explicit ``trace_id``, so it never falls back to counting existing
entries either — see :meth:`~grimoire.traces.ledger.TraceLedger.record`).
The full ledger — the only place ``policy.hold``/``policy.hold_followup``
are actually read back — is read exclusively by ``grimoire hooks
calibrate`` (:meth:`~grimoire.traces.ledger.TraceLedger.
policy_hold_calibration`), never by a hook.

Re-measured after the fix, same protocol, against a 2 000-line
``traces.jsonl``: +0.7 ms median — back in the noise (an empty-journal run
the same session measured *negative* 10.9 ms, i.e. entirely machine
variance between separate subprocess runs, not a real speed-up).
``tests/unit/test_calibration.py::TestHookPathNeverReadsTheLedger`` guards
this with a call-counting monkeypatch on ``TraceLedger._load_all`` — not a
raised exception, since both functions' own best-effort ``except
Exception: pass`` would silently swallow that and let the regression back
in unnoticed.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = ["action_fingerprint", "record_hold_followup", "record_policy_hold", "target_key"]

#: Same directory as :mod:`.session_memory`'s own state file — a per-session
#: run artefact, never versioned (see ``grimoire.core.standard_generation.
#: RUNS_DIR``, imported lazily below to keep this module's own import cost
#: at "hashlib/json/uuid", nothing heavier, on the hot path).
_STATE_SCHEMA_VERSION = 1
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

#: Bound on how many still-open holds one session's state file may carry —
#: see the module docstring. In practice a session answers a hold on its
#: very next ``PostToolUse``, so this almost never grows past 1; the cap
#: exists so a pathological session (many holds, no followup ever observed)
#: cannot make this file itself grow without bound the way ``traces.jsonl``
#: did.
_MAX_OPEN_HOLDS = 20


def action_fingerprint(tool_name: str, detail: str) -> str:
    """A one-way, content-free identifier for a pending or executed action.

    Never the raw command/target — only its hash, truncated to 16 hex
    characters (128 bits, ample for "did the next call match this one",
    never meant to be looked up the other way). See the module docstring
    for why this may never be the literal command or path.
    """
    raw = f"{tool_name}\x00{detail}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def target_key(tool_name: str, detail: str) -> str:
    """A coarser key than :func:`action_fingerprint`: same tool, similar target.

    For a shell command, the leading verb (its first whitespace-separated
    token) — ``git``, ``rm``, ``python3`` — never the rest of the line. For
    a file path, its parent directory's own name — never the file name or
    its content. Two calls sharing this key are "the same kind of thing",
    never "the same thing" (that is what :func:`action_fingerprint` answers
    instead) — used only to tell :data:`~grimoire.traces.ledger.
    HOLD_LABEL_RETRIED_VARIANT` apart from :data:`~grimoire.traces.ledger.
    HOLD_LABEL_RESPECTED`.
    """
    token = detail.strip().split()[0] if detail.strip() else ""
    if "/" in token:
        parent = token.rstrip("/").rsplit("/", 1)[0]
        token = parent.rsplit("/", 1)[-1] if parent else token
    return f"{tool_name}:{token}".lower()


def _safe_session_id(session_id: str) -> str:
    """Copy of :func:`grimoire.hosts.decisions.session_memory._safe_session_id`.

    Duplicated, not imported — same call session_memory.py itself makes for
    its private helpers borrowed from :mod:`.tool_facts`: a two-line
    filename-safety concern does not warrant a cross-module dependency.
    """
    session_id = session_id.strip() or "unknown"
    if _SAFE_ID_RE.match(session_id):
        return session_id
    return "h-" + hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:32]


def _hold_state_path(project_root: Path, session_id: str) -> Path:
    from grimoire.core.standard_generation import RUNS_DIR

    return project_root / RUNS_DIR / f"policy-holds-{_safe_session_id(session_id)}.json"


def _load_open_holds(path: Path) -> list[dict[str, Any]]:
    """The still-open holds of one session, ``[]`` on anything unreadable.

    Same closed-guard contract as :func:`~grimoire.hosts.decisions.
    session_memory.load_session_memory`: an absent, corrupt or
    wrong-version file degrades to "no hold open", never to an exception a
    hook could break on.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(data, dict) or data.get("schema_version") != _STATE_SCHEMA_VERSION:
        return []
    holds = data.get("holds")
    return [entry for entry in holds if isinstance(entry, dict)] if isinstance(holds, list) else []


def _save_open_holds(path: Path, session_id: str, holds: list[dict[str, Any]]) -> None:
    """Atomic write (temp file + ``os.replace``), best-effort — same shape as
    :func:`~grimoire.hosts.decisions.session_memory.save_session_memory`.
    """
    payload = {
        "schema_version": _STATE_SCHEMA_VERSION,
        "session_id": session_id,
        "holds": holds[-_MAX_OPEN_HOLDS:],
    }
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


def record_policy_hold(
    project_root: Path,
    *,
    session_id: str,
    task_id: str,
    hook_id: str,
    reason: str,
    fingerprint: str,
    target: str,
) -> None:
    """Journal one non-``allow`` hook verdict — the ``policy.hold`` half.

    Two writes, both append-only / bounded, never a read of ``traces.jsonl``
    (see the module docstring): the ``TraceLedger`` receives one
    ``.record(...)`` call with an explicit ``trace_id`` (so it never falls
    back to :meth:`~grimoire.traces.ledger.TraceLedger._next_id`, itself a
    full-journal read), and the session's own bounded state file
    (:func:`_hold_state_path`) gets this hold appended, trimmed to
    :data:`_MAX_OPEN_HOLDS`.

    Best-effort, same contract as :func:`grimoire.hosts.decisions.record.
    record_agent_miss`: a journal that cannot be written must never turn a
    hook decision into a broken hook. A no-op without a *session_id* — there
    is nothing to correlate the eventual followup against.
    """
    if not session_id:
        return
    hold_id = uuid.uuid4().hex[:12]
    try:
        from grimoire.core.standard_generation import TRACES_DIR
        from grimoire.traces.ledger import POLICY_HOLD_TAG, TraceLedger
        from grimoire.traces.schemas import TraceOutcome

        TraceLedger(project_root / TRACES_DIR).record(
            run_id=f"hold-{hold_id}",
            workflow_instance_id="",
            mission_id="",
            task_id=task_id,
            recipe_id="grimoire.policy-hold",
            outcome=TraceOutcome.SUCCESS,
            started_at=datetime.now(UTC).isoformat(),
            tags=[
                POLICY_HOLD_TAG,
                f"session:{session_id}",
                f"hook:{hook_id}",
                f"reason:{reason}",
                f"fingerprint:{fingerprint}",
                f"target:{target}",
                f"hold_id:{hold_id}",
            ],
            trace_id=f"TRC-policy-hold-{hold_id}",
        )
    except Exception:  # noqa: S110 — observabilité : jamais au prix du hook lui-même
        pass
    try:
        path = _hold_state_path(project_root, session_id)
        holds = _load_open_holds(path)
        holds.append({
            "hold_id": hold_id,
            "hook": hook_id,
            "reason": reason,
            "fingerprint": fingerprint,
            "target": target,
            "task_id": task_id,
        })
        _save_open_holds(path, session_id, holds)
    except Exception:  # noqa: S110 — observabilité : jamais au prix du hook lui-même
        pass


def record_hold_followup(
    project_root: Path,
    *,
    session_id: str,
    fingerprint: str,
    target: str,
) -> None:
    """Label the still-open hold of *session_id*, if any — the ``policy.hold_followup`` half.

    Called from the next ``PostToolUse`` of the same session after a hold.
    *fingerprint*/*target* describe the action this ``PostToolUse`` call
    just observed, built the same way (:func:`action_fingerprint` /
    :func:`target_key`) as the ones the open hold itself carries:

    - an exact *fingerprint* match -> ``retried_same`` (the very same action,
      replayed);
    - else the same *target* -> ``retried_variant`` (same kind of action,
      not the exact one);
    - else -> ``respected`` (something else entirely happened next).

    Reads and writes only the session's own bounded state file to find "the
    still open hold" — never ``traces.jsonl`` (see the module docstring for
    the measured cost that made this necessary). The matched hold is
    removed from that state file once labelled, so the next call never
    re-answers it. Writing the ``policy.hold_followup`` record itself is a
    single ``TraceLedger.record(...)`` call with an explicit ``trace_id`` —
    an append, never a read.

    Best-effort, same contract as :func:`record_policy_hold`. A no-op
    without a *session_id*, or when there is no open hold to answer.
    """
    if not session_id:
        return
    try:
        path = _hold_state_path(project_root, session_id)
        holds = _load_open_holds(path)
        if not holds:
            return
        hold = holds.pop()  # the most recently opened still-open hold
        _save_open_holds(path, session_id, holds)
    except Exception:
        return
    try:
        from grimoire.core.standard_generation import TRACES_DIR
        from grimoire.traces.ledger import (
            HOLD_FOLLOWUP_TAG,
            HOLD_LABEL_RESPECTED,
            HOLD_LABEL_RETRIED_SAME,
            HOLD_LABEL_RETRIED_VARIANT,
            TraceLedger,
        )
        from grimoire.traces.schemas import TraceOutcome

        held_fingerprint = str(hold.get("fingerprint") or "")
        held_target = str(hold.get("target") or "")
        if fingerprint and fingerprint == held_fingerprint:
            label = HOLD_LABEL_RETRIED_SAME
        elif target and target == held_target:
            label = HOLD_LABEL_RETRIED_VARIANT
        else:
            label = HOLD_LABEL_RESPECTED
        hold_id = str(hold.get("hold_id") or "")
        TraceLedger(project_root / TRACES_DIR).record(
            run_id=f"followup-{hold_id}",
            workflow_instance_id="",
            mission_id="",
            task_id=str(hold.get("task_id") or ""),
            recipe_id="grimoire.policy-hold-followup",
            outcome=TraceOutcome.SUCCESS,
            started_at=datetime.now(UTC).isoformat(),
            tags=[
                HOLD_FOLLOWUP_TAG,
                f"hold_id:{hold_id}",
                f"label:{label}",
                f"hook:{hold.get('hook') or ''}",
                f"reason:{hold.get('reason') or ''}",
            ],
            trace_id=f"TRC-policy-hold-followup-{hold_id}",
        )
    except Exception:  # noqa: S110 — observabilité : jamais au prix du hook lui-même
        pass
