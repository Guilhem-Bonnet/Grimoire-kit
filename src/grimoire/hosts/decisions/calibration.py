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
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path

__all__ = ["action_fingerprint", "record_hold_followup", "record_policy_hold", "target_key"]


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

    Best-effort, same contract as :func:`grimoire.hosts.decisions.record.
    record_agent_miss`: a journal that cannot be written must never turn a
    hook decision into a broken hook. A no-op without a *session_id* — there
    is nothing to correlate the eventual followup against.
    """
    if not session_id:
        return
    try:
        from grimoire.core.standard_generation import TRACES_DIR
        from grimoire.traces.ledger import POLICY_HOLD_TAG, TraceLedger
        from grimoire.traces.schemas import TraceOutcome

        hold_id = uuid.uuid4().hex[:12]
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

    Best-effort, same contract as :func:`record_policy_hold`. A no-op
    without a *session_id*, or when there is no open hold to answer.

    Cost, measured (A/B, median of 7, real ``grimoire-hook`` ``PostToolUse``
    subprocess): in the noise (<1 ms) against an empty traces journal, but
    :meth:`~grimoire.traces.ledger.TraceLedger.open_policy_hold` parses the
    *entire* ``traces.jsonl`` on every call with a *session_id* — with 200
    prior lines already in the journal (every trace type, not only holds)
    the measured delta was already ~10 ms, above the "noise" this feature
    was designed under (party-mode idea B, risk (b): "coût par tour du
    ``TraceLedger.record`` supplémentaire non mesuré"). This is a real,
    per-session-growing cost this function does not yet address — reported
    here rather than silently accepted; narrowing the read (an index, a
    tail-only scan) is out of scope for this change.
    """
    if not session_id:
        return
    try:
        from grimoire.core.standard_generation import TRACES_DIR
        from grimoire.traces.ledger import (
            HOLD_LABEL_RESPECTED,
            HOLD_LABEL_RETRIED_SAME,
            HOLD_LABEL_RETRIED_VARIANT,
            TraceLedger,
        )

        ledger = TraceLedger(project_root / TRACES_DIR)
        hold = ledger.open_policy_hold(session_id)
        if hold is None:
            return
        held_fingerprint = _last_tag(hold.tags, "fingerprint:")
        held_target = _last_tag(hold.tags, "target:")
        if fingerprint and fingerprint == held_fingerprint:
            label = HOLD_LABEL_RETRIED_SAME
        elif target and target == held_target:
            label = HOLD_LABEL_RETRIED_VARIANT
        else:
            label = HOLD_LABEL_RESPECTED
        ledger.record_hold_followup(hold=hold, label=label)
    except Exception:  # noqa: S110 — observabilité : jamais au prix du hook lui-même
        pass


def _last_tag(tags: tuple[str, ...], prefix: str) -> str:
    """The value of the last *prefix*-prefixed tag in *tags*, ``""`` if none.

    Mirrors :func:`grimoire.traces.ledger._last_tag_value` (private there) —
    duplicated rather than imported: this reads a record's own tags, a
    two-line concern that does not warrant widening that module's exports.
    """
    value = ""
    for tag in tags:
        if tag.startswith(prefix):
            value = tag[len(prefix) :]
    return value
