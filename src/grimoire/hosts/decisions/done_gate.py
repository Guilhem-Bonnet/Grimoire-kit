"""``Stop``: a deterministic "done" gate — mutation after the last green check.

Issue #644, itself a deterministic-only read of jev-belay (``belay.mjs``) and
pi-warden (``src/done.ts``): a closure whose evidence gates are green can
still hide a session that edited a file *after* its last green test run and
never re-ran anything. :mod:`.evidence_gate` never sees that — it only knows
whether the required artifacts exist, not what order the actions inside them
happened in. This module reads the one thing that already has that order:
the ``PostToolUse`` journal (:mod:`grimoire.core.standard_checks.
evidence_journal`), append-only and per task, already written for the
evidence pack's "Inventaire observé" section (issue #582 lot G2). No second
journal, no model, no network — the mutation/check split it needs already
matches what :mod:`.tool_facts` (``is_read_only_command``) and
``evidence-log.jsonl``'s own ``"test_run"`` marker (extended by this issue to
cover ``vitest``/``jest``/``tsc``/``eslint``/``ruff``) can answer from disk.

Veto dur: a green check logged *after* the last mutation always wins,
whatever else is true — see :func:`evaluate_done_gate`, the ``green_after``
short-circuit. Nothing in this module can turn that back into a stale
verdict.

Shadow by default
------------------
:func:`evaluate_done_gate` computes a verdict on every eligible ``Stop``, but
only *reports* it (:mod:`.evidence_gate` writes it to
``Decision.detail["done_gate"]``, plus a warning context in a blocking
profile) — it does not, by itself, make :mod:`.evidence_gate` refuse a
closure. Refusing costs a whole turn on a false positive, and this gate has
had zero hours of production journal to be wrong against: the classification
it depends on (:func:`grimoire.hosts.decisions.tool_facts.
is_read_only_command`) is already conservative in the *policy* direction
(unrecognised → mutation), which is the wrong bias for a gate that blocks —
here an unrecognised verb should not manufacture a stale verdict either. The
kit's own directive (:mod:`.evidence_gate`'s docstring) already promotes a
*prompt-level* suggestion to a hard rule once it has been read from a
project's journals; this module earns that promotion the same way — after a
project has logged real ``done_gate`` verdicts and someone has read whether
they tracked reality. Until then, opting a project in early is one line:
``GRIMOIRE_DONE_GATE=enforce`` in the environment, or ``options: {done_gate:
enforce}`` in ``_grimoire/standard/standard-profile.yaml`` (read the same way
:mod:`grimoire.core.standard_checks.gate_test_run` reads that file's
``profile`` key — see :func:`_is_enforced`). Even enforced, a blocking
profile (:data:`grimoire.hosts.decisions.enrolment.BLOCKING_PROFILES`) is the
only one this can ever refuse in; every other profile only ever gets the
warning context, exactly like a red evidence gate today.

Caps
----
Enforced and stale, this path still refuses at most once per task per 60
seconds and three times per session — the same shape jev-belay/pi-warden use
(refroidissement, plafond de session) so a session that keeps hitting the
same stale state is told once, not looped. State lives beside the journal it
reads (``evidence-log.jsonl``'s own directory,
``_grimoire-output/.runs/evidence/<task_id>/``), a JSON file this module owns
alone — see :func:`_caps_path`.
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.core.standard_checks.evidence_journal import evidence_log_relpath, read_evidence_log
from grimoire.hosts.decisions.enrolment import BLOCKING_PROFILES
from grimoire.hosts.decisions.tool_facts import is_read_only_command

__all__ = ["DoneGateVerdict", "evaluate_done_gate"]

#: Same directory as ``evidence-log.jsonl`` (see the module docstring) —
#: never ``_grimoire-output/evidence`` (the versioned pack): a per-task
#: cooldown/cap counter is exactly the kind of run state ``RUNS_DIR`` exists
#: for, and it must never be committed.
_CAPS_STATE_FILENAME = "done-gate-state.json"

#: One refusal per task per this many seconds (jev-belay/pi-warden: "60 s").
_COOLDOWN_SECONDS = 60.0

#: At most this many refusals per session, however many tasks it touches
#: (jev-belay/pi-warden: "trois par session").
_SESSION_CAP = 3

#: ``GRIMOIRE_DONE_GATE=enforce`` — the escape hatch named in the issue when
#: no project/profile option exists yet (see the module docstring).
_ENFORCE_ENV = "GRIMOIRE_DONE_GATE"


@dataclass(frozen=True, slots=True)
class DoneGateVerdict:
    """What :func:`evaluate_done_gate` decided, and why — always attached to
    ``Decision.detail["done_gate"]`` by :mod:`.evidence_gate`, never only
    inferred from the outcome."""

    stale: bool
    """A mutation with no green check after it — the only condition that
    can ever lead to a refusal via this path."""
    reason: str
    """One of ``no_mutation``, ``green_check_after_mutation`` (the veto dur)
    or ``mutation_after_last_check``."""
    enforce: bool
    """Whether this project/environment opted into a real ``BLOCK`` (see the
    module docstring) — independent of whether *this* call actually used it."""
    blocked: bool
    """``True`` only when :mod:`.evidence_gate` should escalate its outcome
    to :attr:`~grimoire.hosts.decisions._shared.Outcome.BLOCK` for this call:
    stale, enforced, a blocking profile, and the caps below did not apply."""
    capped: bool
    """A refusal was due here but a cooldown or session cap suppressed it."""
    command_hint: str = ""
    """The command to name in a message — the project's resolved test
    command when one exists, else the last recognised check command seen in
    the journal, else a generic hint."""
    last_mutation_ts: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "stale": self.stale,
            "reason": self.reason,
            "enforce": self.enforce,
            "blocked": self.blocked,
            "capped": self.capped,
            "command_hint": self.command_hint,
            "last_mutation_ts": self.last_mutation_ts,
        }


def _entry_ts(entry: dict[str, Any]) -> str:
    return str(entry.get("ts") or "")


def _is_mutation_candidate(entry: dict[str, Any]) -> bool:
    """A ``file_write`` event is always a mutation (Edit/Write never read).

    A ``bash`` event is one when its command is not classified read-only —
    reusing :func:`grimoire.hosts.decisions.tool_facts.is_read_only_command`
    on the stored command string rather than storing a redundant flag at
    write time: the journal already carries the one thing that
    classification needs (the full command line), and a second copy of the
    verdict could drift from it if the classifier ever changes.

    A ``test_run`` event is never a mutation candidate, deliberately: running
    a recognised test/lint command is not read-only under
    :func:`is_read_only_command` either (``pytest`` is not in its read-only
    verb whitelist — it is an *execution*, not a query), so without this
    exclusion the test run that should discharge a mutation would itself
    count as one, pushing "the last mutation" past the very check meant to
    answer it. Checks and mutations are mutually exclusive categories here —
    see :func:`_is_check_entry`, the other half of that split, and
    :func:`evaluate_done_gate`'s second pass for the one case (a ``bash``
    event that turns out to *be* the project's resolved test command) this
    cheap, resolve-free pass cannot rule out on its own.
    """
    kind = entry.get("type")
    if kind == "file_write":
        return True
    if kind == "bash":
        command = str(entry.get("command") or "")
        return bool(command) and not is_read_only_command(command)
    return False


def _is_check_entry(entry: dict[str, Any], resolved_command: str) -> bool:
    """A recognised check: the journal's own ``"test_run"`` marker, or a
    ``bash`` entry whose command is (a prefix of) the project's resolved test
    command — a project whose test command does not match
    :data:`grimoire.core.standard_checks.evidence_journal._TEST_COMMAND_PATTERN`
    (``tox``, ``make test``…) still counts once resolved."""
    if entry.get("type") == "test_run":
        return True
    if entry.get("type") != "bash" or not resolved_command:
        return False
    command = str(entry.get("command") or "").strip()
    resolved = resolved_command.strip()
    return bool(command) and (command == resolved or command.startswith(resolved))


def _check_is_green(entry: dict[str, Any]) -> bool:
    exit_code = entry.get("exit_code")
    return isinstance(exit_code, int) and not isinstance(exit_code, bool) and exit_code == 0


def _last_check_command(check_entries: list[dict[str, Any]]) -> str:
    if not check_entries:
        return ""
    latest = max(check_entries, key=_entry_ts)
    return str(latest.get("command") or "")


def _resolved_test_command(project_root: Path) -> str:
    """Best-effort, never raises: an unreadable ``project-context.yaml`` or an
    unresolvable need degrades to "no hint", never to a broken ``Stop``.

    Deferred import, like :mod:`grimoire.core.standard_checks.gate_test_run`
    does for the same call: only reached once a mutation was actually found
    (see :func:`evaluate_done_gate`'s early return), so a read-only ``Stop``
    never pays for it.
    """
    try:
        from grimoire.core.execution_needs import resolve_need

        need = resolve_need("test-runner", project_root.resolve())
    except Exception:
        return ""
    return str(need.command) if need.resolved and need.command else ""


def _is_enforced(project_root: Path) -> bool:
    """Whether this project/environment opted into a real ``BLOCK`` — see the
    module docstring for why shadow is the default. Checked in this order:
    the environment variable (cheapest, and the one named in the issue when
    no project option exists yet), then an ``options.done_gate: enforce`` key
    hand-added to ``standard-profile.yaml`` — the same file and the same
    ``_load_mapping`` helper :func:`grimoire.core.standard_checks.
    gate_test_run.board_state_of_task` already reads privately across module
    boundaries for a project-declared value, so this is not a new pattern.
    """
    if os.environ.get(_ENFORCE_ENV, "").strip().lower() == "enforce":
        return True
    try:
        from grimoire.core.standard_generation import STANDARD_PROFILE_FILE
        from grimoire.core.standard_state import _load_mapping

        options = _load_mapping(project_root.resolve() / STANDARD_PROFILE_FILE).get("options")
    except Exception:
        return False
    if not isinstance(options, dict):
        return False
    return str(options.get("done_gate", "")).strip().lower() == "enforce"


def _caps_path(project_root: Path, task_id: str) -> Path:
    return project_root / evidence_log_relpath(task_id).parent / _CAPS_STATE_FILENAME


def _load_caps(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _save_caps(path: Path, data: dict[str, Any]) -> None:
    """Atomic write, best-effort — mirrors
    :func:`grimoire.policies.session_state.save_session_state`."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


def _seconds_between(a: str, b: str) -> float:
    try:
        return abs((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds())
    except ValueError:
        # Unparseable state: never let a corrupt timestamp hold a cooldown
        # open forever — treat it as long enough ago to have expired.
        return _COOLDOWN_SECONDS + 1.0


def _check_and_record_cap(project_root: Path, task_id: str, session_id: str, now_iso: str) -> bool:
    """``True`` when a refusal is due here but a cap suppresses it — and, in
    that case only, records this call as having used one (cooldown *and*
    session-count are consumed together: the session cap exists precisely to
    survive a host that ignores the cooldown and retries immediately)."""
    path = _caps_path(project_root, task_id)
    state = _load_caps(path)
    last_block_ts = str(state.get("last_block_ts") or "")
    if last_block_ts and _seconds_between(last_block_ts, now_iso) < _COOLDOWN_SECONDS:
        return True
    session_blocks_raw = state.get("session_blocks")
    session_blocks: dict[str, int] = session_blocks_raw if isinstance(session_blocks_raw, dict) else {}
    sid = session_id or "unknown"
    count = int(session_blocks.get(sid, 0) or 0)
    if count >= _SESSION_CAP:
        return True
    session_blocks[sid] = count + 1
    state["last_block_ts"] = now_iso
    state["session_blocks"] = session_blocks
    _save_caps(path, state)
    return False


def evaluate_done_gate(hook: Any, task_id: str, profile: str, *, now_iso: str | None = None) -> DoneGateVerdict:
    """The verdict for *task_id* — never raises, never touches the network or
    a model. ``hook`` is a :class:`grimoire.hosts.decisions._shared.HookInput`
    (typed loosely here to avoid an import cycle with :mod:`.evidence_gate`,
    the only caller). ``now_iso`` is only ever overridden by tests: real
    callers get the actual clock.
    """
    now_iso = now_iso or datetime.now(UTC).isoformat()
    entries = read_evidence_log(hook.project_root, task_id)
    # Pass 1, cheap and resolve-free: candidates only (see
    # :func:`_is_mutation_candidate` for why ``test_run`` is excluded
    # outright but a plain ``bash`` mutation is not yet final — it might
    # still turn out to be the project's own resolved test command, which
    # pass 2 alone can tell).
    candidate_indices = [index for index, entry in enumerate(entries) if _is_mutation_candidate(entry)]
    if not candidate_indices:
        return DoneGateVerdict(stale=False, reason="no_mutation", enforce=False, blocked=False, capped=False)

    resolved_command = _resolved_test_command(hook.project_root)
    mutation_indices = [index for index in candidate_indices if not _is_check_entry(entries[index], resolved_command)]
    if not mutation_indices:
        # Every candidate mutation was actually the resolved test command
        # itself (a ``bash`` entry the cheap pass could not rule out).
        return DoneGateVerdict(stale=False, reason="no_mutation", enforce=False, blocked=False, capped=False)
    # Ordering by *list position*, not by the ``ts`` string: the journal is
    # append-only, so position already is the true sequence, and two events
    # appended microseconds apart can carry the identical ISO timestamp
    # (``datetime.isoformat()`` truncates a zero microsecond field, and even
    # a nonzero one is coarse next to how fast two appends can happen).
    # Comparing timestamps would make that tie ambiguous in either direction;
    # position never is.
    last_mutation_idx = max(mutation_indices)
    last_mutation_ts = _entry_ts(entries[last_mutation_idx])

    check_entries = [entry for entry in entries if _is_check_entry(entry, resolved_command)]
    checks_after = [
        entry for entry in entries[last_mutation_idx + 1 :] if _is_check_entry(entry, resolved_command)
    ]
    green_after = [entry for entry in checks_after if _check_is_green(entry)]
    if green_after:
        # Veto dur — see the module docstring. Nothing below this line can
        # ever turn a fresh green check back into a stale verdict.
        return DoneGateVerdict(
            stale=False,
            reason="green_check_after_mutation",
            enforce=False,
            blocked=False,
            capped=False,
            last_mutation_ts=last_mutation_ts,
        )

    enforce = _is_enforced(hook.project_root)
    command_hint = resolved_command or _last_check_command(check_entries) or "ta commande de test"
    capped = False
    blocked = False
    if profile in BLOCKING_PROFILES and enforce:
        capped = _check_and_record_cap(hook.project_root, task_id, hook.session_id, now_iso)
        blocked = not capped
    return DoneGateVerdict(
        stale=True,
        reason="mutation_after_last_check",
        enforce=enforce,
        blocked=blocked,
        capped=capped,
        command_hint=command_hint,
        last_mutation_ts=last_mutation_ts,
    )
