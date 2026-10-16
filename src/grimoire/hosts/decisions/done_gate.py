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
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.core.standard_checks.evidence_journal import (
    append_guard_event,
    evidence_log_relpath,
    read_evidence_log,
    record_guard_error,
)
from grimoire.hosts.decisions.enrolment import BLOCKING_PROFILES
from grimoire.hosts.decisions.tool_facts import command_surface, is_read_only_command

__all__ = [
    "DoneGateVerdict",
    "StopStep",
    "evaluate_done_gate",
    "record_stop_block",
    "record_stop_would_block",
    "resolve_done_gate_block",
    "resolve_done_gate_block_after_crash",
    "stop_cap",
    "stop_signature",
    "stop_step",
]

#: Same directory as ``evidence-log.jsonl`` (see the module docstring) —
#: never ``_grimoire-output/evidence`` (the versioned pack): a per-task
#: cooldown/cap counter is exactly the kind of run state ``RUNS_DIR`` exists
#: for, and it must never be committed.
_CAPS_STATE_FILENAME = "done-gate-state.json"

#: Sidecar of the "after a crash of the resolution" path (W1-06) : the sessions
#: already refused once there. Separate from :data:`_CAPS_STATE_FILENAME` on
#: purpose — that file is exactly what could not be read when this path runs.
_CRASH_BLOCKS_FILENAME = "done-gate-crash-blocks.json"

#: Sessions remembered in that sidecar (oldest dropped) — keeps the file bounded.
_CRASH_BLOCKS_MAX = 50

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
    """A mutation with no green check after it. ``False`` for a crash of the
    evaluation itself (``reason`` ``error:*``) : no mutation was judged stale
    there, but a refusal can still follow (see ``blocked``)."""
    reason: str
    """One of ``no_mutation``, ``green_check_after_mutation`` (the veto dur),
    ``mutation_after_last_check``, or ``error:<ExceptionType>`` when the
    evaluation crashed (:func:`.evidence_gate._done_gate_unevaluable`)."""
    enforce: bool
    """Whether this project/environment opted into a real ``BLOCK`` (see the
    module docstring) — independent of whether *this* call actually used it."""
    blocked: bool
    """``True`` only when :mod:`.evidence_gate` should escalate its outcome
    to :attr:`~grimoire.hosts.decisions._shared.Outcome.BLOCK` for this call:
    stale (or the evaluation crashed, ``reason`` ``error:*``), enforced, a
    blocking profile, and the caps below did not apply."""
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


#: An absolute-path-looking token: whitespace (or start of string) followed
#: by ``/``. Used only to find *identifiable* out-of-root targets in a bash
#: command line — see :func:`_bash_targets_all_outside_root`. Deliberately
#: does not match a ``/`` glued onto a preceding non-space character (a URL's
#: ``https://…``, a path fragment mid-word): the lookbehind requires an
#: actual token boundary.
_ABS_PATH_TOKEN_RE = re.compile(r"(?<!\S)(/\S+)")

#: ``<<TAG``/``<<'TAG'``/``<<-TAG`` opening a heredoc body, matched against
#: the *flattened* (whitespace-collapsed) command the journal actually
#: stores — see :func:`_strip_flattened_heredoc_bodies` for why this cannot
#: reuse :func:`grimoire.hosts.decisions.tool_facts.command_surface`'s own
#: heredoc stripping, which requires real newlines this text no longer has.
_FLATTENED_HEREDOC_RE = re.compile(r"(<<-?\s*(['\"]?)(\w+)\2\s)(.*?)\s\3\b", re.DOTALL)


def _strip_flattened_heredoc_bodies(command: str) -> str:
    """Drop a heredoc body from an already whitespace-flattened command.

    2026-09-29 calibration follow-up (Refs #644, incidents #3/#4):
    ``evidence_journal.build_bash_event`` stores every command through
    ``_truncate_command`` (``" ".join(command.split())``) *before* this
    module ever sees it — a real multi-line heredoc (``cat >> /tmp/x <<'EOF'
    \\nsome text\\nEOF``) is already a single space-joined line by the time
    it reaches :func:`_bash_targets_all_outside_root`.
    :func:`~grimoire.hosts.decisions.tool_facts.command_surface`'s own
    heredoc stripping (:func:`~grimoire.hosts.decisions.
    tool_facts._strip_heredoc_bodies`) anchors on the body starting *after a
    literal newline* and the closing tag sitting *alone on its own line* —
    both permanently gone here, so that function is a silent no-op on this
    text, and any in-project path merely *mentioned* inside the heredoc body
    (a note, a log excerpt) would otherwise still read as a second,
    in-project "target", defeating :func:`_bash_targets_all_outside_root`
    exactly when the write target itself is legitimately outside the
    project.

    This is the flattened-text equivalent: the body is whatever sits
    between the opening ``<<TAG`` and the next bare occurrence of the same
    tag as its own word. No tag anywhere in the (flattened) command -> the
    text is returned unchanged, never an error.
    """
    return _FLATTENED_HEREDOC_RE.sub(r"\1", command)


def _resolve_quiet(path: Path) -> Path | None:
    """``Path.resolve()``, or ``None`` on anything that stops it — never raises."""
    try:
        return path.resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def _is_outside_root(target: str, resolved_root: Path) -> bool:
    """Whether *target* (an absolute path string) resolves outside *resolved_root*.

    ``False`` on anything unresolvable — an unresolvable path is ambiguous,
    never confidently "outside" (see the two callers: both count an
    ambiguous target as a reason *not* to exclude a candidate mutation,
    never as a reason to).
    """
    resolved_target = _resolve_quiet(Path(target))
    if resolved_target is None:
        return False
    try:
        resolved_target.relative_to(resolved_root)
    except ValueError:
        return True
    return False


def _bash_targets_all_outside_root(command: str, project_root: Path) -> bool:
    """Issue #644 follow-up (calibration, Refs #644): the 2026-09-29 false positive.

    A mutation candidate whose *only* identifiable targets are outside the
    project — a ``bash`` command run against a worktree of another repo, for
    instance ``git -C /some/other/repo push`` or ``python3 script.py
    /some/other/repo/file.py`` — must never make an otherwise-untouched
    project read as "stale". :func:`_is_mutation_candidate` reads the
    journal's ``command`` string for every ``bash`` entry it has always
    classified as a mutation; this adds one more condition to that
    classification, never a new source of it.

    "Identifiable target" means an absolute-path-looking token in the
    command's :func:`~grimoire.hosts.decisions.tool_facts.command_surface`
    (see :data:`_ABS_PATH_TOKEN_RE`) — never the raw command line. 2026-09-29
    calibration follow-up (Refs #644, observed in real Forge sessions,
    incidents #3/#4): a heredoc body redirected to a file outside the
    project (``cat >> /tmp/x/notes.md <<'EOF' … EOF``) can freely mention an
    unrelated in-project path in its *documented text* (a note, a log
    excerpt) — reading the raw command found that mention as a second
    "target", made the mix-of-inside/outside branch below fire, and kept a
    write that only ever touched ``/tmp`` counting as an in-project
    mutation. ``command_surface`` already drops heredoc bodies and quoted
    text for exactly this reason (documented data must never read as the
    action itself) — the same guard :mod:`.tool_policy` relies on for its
    own destructive-pattern and untrusted-content checks.

    This deliberately does not attempt to resolve a bare relative argument
    (``cp file.py ../other/``): the hook's own cwd is not carried in the
    journal, so a relative path cannot be judged in or out of the project
    root with any confidence, and this function must never manufacture
    false confidence either way (see the "ambiguous -> count" branches
    below).

    Returns ``True`` only when at least one identifiable target was found
    *and* every one of them resolves outside *project_root*. No identifiable
    target at all, an unresolvable one, or a mix of inside/outside targets
    all return ``False`` — "count it" is the fail-closed default throughout
    this function, never "exclude it".
    """
    targets = _ABS_PATH_TOKEN_RE.findall(command_surface(_strip_flattened_heredoc_bodies(command)))
    if not targets:
        return False
    resolved_root = _resolve_quiet(project_root)
    if resolved_root is None:
        return False
    return all(_is_outside_root(target, resolved_root) for target in targets)


def _file_write_target_outside_root(path: str, project_root: Path) -> bool:
    """Same guarantee as :func:`_bash_targets_all_outside_root`, for a ``file_write``
    entry's own ``path``. A relative path is never excluded (ambiguous ->
    count): every real ``Edit``/``Write`` call observed so far sends an
    absolute path, but a hand-written journal or a future host might not.
    """
    if not path.startswith("/"):
        return False
    resolved_root = _resolve_quiet(project_root)
    if resolved_root is None:
        return False
    return _is_outside_root(path, resolved_root)


def _is_mutation_candidate(entry: dict[str, Any], project_root: Path) -> bool:
    """A ``file_write`` event is a mutation when its path is under *project_root*
    (see :func:`_file_write_target_outside_root` for what "ambiguous" means
    here — never excluded).

    A ``bash`` event is one when its command is not classified read-only —
    reusing :func:`grimoire.hosts.decisions.tool_facts.is_read_only_command`
    on the stored command string rather than storing a redundant flag at
    write time: the journal already carries the one thing that
    classification needs (the full command line), and a second copy of the
    verdict could drift from it if the classifier ever changes — *and* when
    it is not a command whose only identifiable targets sit outside
    *project_root* (:func:`_bash_targets_all_outside_root`, the 2026-09-29
    false positive this reads).

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
        path = str(entry.get("path") or "")
        return bool(path) and not _file_write_target_outside_root(path, project_root)
    if kind == "bash":
        command = str(entry.get("command") or "")
        if not command or is_read_only_command(command):
            return False
        return not _bash_targets_all_outside_root(command, project_root)
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


def _is_enforced(project_root: Path, task_id: str) -> bool:
    """Whether this project/environment opted into a real ``BLOCK`` — see the
    module docstring for why shadow is the default. Checked in this order:
    the environment variable (cheapest, and the one named in the issue when
    no project option exists yet), then an ``options.done_gate: enforce`` key
    hand-added to ``standard-profile.yaml``.

    An unreadable profile still answers ``False`` (shadow is the safe default
    for the opt-in), but no longer silently (W1-06) : the failure is traced as
    a ``guard.error`` (``guard_id`` ``done_gate.enforce_option``) for *task_id*,
    so ``grimoire standard verify`` shows that the option could not be read.
    "Unreadable" covers an invalid YAML, a file that is not valid UTF-8, an I/O
    error and a root that is not a mapping. The profile is parsed here rather
    than through ``standard_state._load_mapping``, which swallows those very
    errors and returns ``{}``: the failure would never reach this ``except``.
    A missing profile, or one without the option, is not a failure.
    """
    if os.environ.get(_ENFORCE_ENV, "").strip().lower() == "enforce":
        return True
    try:
        from ruamel.yaml import YAML

        from grimoire.core.standard_generation import STANDARD_PROFILE_FILE

        profile = project_root.resolve() / STANDARD_PROFILE_FILE
        if not profile.is_file():
            return False
        data = YAML(typ="safe").load(profile)
        if data is None:
            return False
        if not isinstance(data, dict):
            raise TypeError(f"{STANDARD_PROFILE_FILE} : la racine n'est pas un mapping ({type(data).__name__})")
    except Exception as exc:
        record_guard_error(project_root, task_id, "done_gate.enforce_option", type(exc).__name__, str(exc))
        return False
    options = data.get("options")
    if not isinstance(options, dict):
        return False
    return str(options.get("done_gate", "")).strip().lower() == "enforce"


def _caps_path(project_root: Path, task_id: str) -> Path:
    return project_root / evidence_log_relpath(task_id).parent / _CAPS_STATE_FILENAME


def caps_state_relpath(task_id: str) -> Path:
    """Project-relative path of the cooldown/cap state of *task_id* (named in the messages that ask to delete it)."""
    return evidence_log_relpath(task_id).parent / _CAPS_STATE_FILENAME


def _crash_blocks_path(project_root: Path, task_id: str) -> Path:
    return project_root / evidence_log_relpath(task_id).parent / _CRASH_BLOCKS_FILENAME


def _load_caps(path: Path) -> dict[str, Any]:
    """The JSON object stored at *path*, ``{}`` when missing, unreadable, not UTF-8 or not an object."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # ``UnicodeDecodeError`` and ``JSONDecodeError`` are ``ValueError``
        return {}
    return data if isinstance(data, dict) else {}


def _save_caps(path: Path, data: dict[str, Any]) -> bool:
    """Atomic write, best-effort — mirrors
    :func:`grimoire.policies.session_state.save_session_state`.

    Returns whether the state reached the disk (W2-02-S2) : the cooldown/cap
    callers ignore it, but a counter that cannot be written must never be
    mistaken for one that advanced (see :func:`stop_step`).
    """
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        return False
    return True


def _seconds_between(a: str, b: str) -> float:
    try:
        return abs((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds())
    except (ValueError, TypeError):
        # Unparseable state (or a naive timestamp against an aware one, which
        # raises ``TypeError``): never let a corrupt timestamp hold a cooldown
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
    raw_count = session_blocks.get(sid, 0)
    if isinstance(raw_count, int) and not isinstance(raw_count, bool) and raw_count >= 0:
        count = raw_count
    else:
        # État de plafond corrompu (W1-06) : une valeur non entière ne doit ni planter la
        # résolution du refus ni la faire taire. Comptée 0 (le refus reste possible, le
        # fichier est réécrit sain), et la corruption est tracée.
        record_guard_error(
            project_root, task_id, "done_gate.caps_state", "ValueError", f"session_blocks[{sid!r}]={raw_count!r}"
        )
        count = 0
    if count >= _SESSION_CAP:
        return True
    session_blocks[sid] = count + 1
    state["last_block_ts"] = now_iso
    state["session_blocks"] = session_blocks
    _save_caps(path, state)
    return False


def resolve_done_gate_block(
    project_root: Path, task_id: str, session_id: str, profile: str, now_iso: str
) -> tuple[bool, bool, bool]:
    """``(enforce, blocked, capped)`` : la résolution unique du refus du gate « fini ».

    Partagée par le verdict ``stale`` et par l'échec d'évaluation
    (:mod:`.evidence_gate`) : un refus n'est possible que dans un profil
    bloquant ET avec l'opt-in ``enforce``, et passe alors par le
    refroidissement et le plafond de session. Consomme un tour du plafond
    uniquement quand un refus est dû.
    """
    enforce = _is_enforced(project_root, task_id)
    if profile in BLOCKING_PROFILES and enforce:
        capped = _check_and_record_cap(project_root, task_id, session_id, now_iso)
        return enforce, not capped, capped
    return enforce, False, False


def resolve_done_gate_block_after_crash(
    project_root: Path, task_id: str, profile: str, session_id: str = ""
) -> tuple[bool, bool, bool]:
    """``(enforce, blocked, capped)`` quand :func:`resolve_done_gate_block` a lui-même planté (W1-06).

    Fail-closed là où le profil le promet : profil bloquant ET opt-in
    ``enforce`` → ``blocked``. Mais le refroidissement et le plafond de session
    ne peuvent plus s'appuyer sur leur état (c'est justement lui qui n'a pas pu
    être lu) : un sidecar minimal (``done-gate-crash-blocks.json``) retient les
    sessions déjà refusées une fois ici, et une session ne l'est qu'une fois —
    la règle « prévenue une fois, pas bouclée » s'applique aussi à ce chemin.
    Un sidecar illisible compte pour vide (refus fail-closed, réécrit sain) ; un
    sidecar impossible à écrire ne peut rien retenir (l'hôte laisse de toute
    façon passer le second ``Stop``, ``stop_active``). Ailleurs, jamais de refus.
    ``enforce`` est relu séparément (``_is_enforced`` trace sa propre panne) au
    lieu d'être réécrit à ``False``.
    """
    try:
        enforce = _is_enforced(project_root, task_id)
    except Exception as exc:
        record_guard_error(project_root, task_id, "done_gate.enforce_option", type(exc).__name__, str(exc))
        enforce = False
    if not (enforce and profile in BLOCKING_PROFILES):
        return enforce, False, False
    path = _crash_blocks_path(project_root, task_id)
    raw = _load_caps(path).get("sessions")
    sessions = [s for s in raw if isinstance(s, str)] if isinstance(raw, list) else []
    sid = session_id or "unknown"
    if sid in sessions:
        return enforce, False, True
    _save_caps(path, {"sessions": [*sessions, sid][-_CRASH_BLOCKS_MAX:]})
    return enforce, True, False


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
    candidate_indices = [
        index for index, entry in enumerate(entries) if _is_mutation_candidate(entry, hook.project_root)
    ]
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

    command_hint = resolved_command or _last_check_command(check_entries) or "ta commande de test"
    enforce, blocked, capped = resolve_done_gate_block(
        hook.project_root, task_id, hook.session_id, profile, now_iso
    )
    return DoneGateVerdict(
        stale=True,
        reason="mutation_after_last_check",
        enforce=enforce,
        blocked=blocked,
        capped=capped,
        command_hint=command_hint,
        last_mutation_ts=last_mutation_ts,
    )


# --- W2-02 : signature de progrès du Stop ---------------------------------
#
# Le compteur ci-dessus (``done-gate-state.json``) est étendu, pas doublé : la
# clé ``stop`` y porte la chaîne de refus des gates de preuve rouges. Avant, un
# ``Stop`` continué (``stop_hook_active``) passait sans rien regarder : un seul
# refus, que la session ait avancé ou non. Désormais le refus est maintenu tant
# que la signature change (la session avance), et libère dès qu'une signature se
# répète (elle tourne en rond) ou que le plafond du profil est atteint.

#: Plafond de refus par chaîne de ``Stop``, par profil. Jamais une variable
#: d'environnement : un plafond réglable par l'agent serait un plafond désarmé.
_STOP_CAPS: dict[str, int] = {"governed": 4, "production": 8}
_STOP_CAP_DEFAULT = 2

#: Événement tracé à chaque relâchement (journal de garde de la tâche).
STOP_RELEASED_EVENT = "stop.released"

#: Événement tracé en shadow (W2-02-S3) : le refus maintenu aurait été émis, il ne l'est pas.
STOP_WOULD_BLOCK_EVENT = "stop.would_block"

_STOP_KEY = "stop"
_FINGERPRINT_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class StopStep:
    """Décision d'un ``Stop`` continué : ``release`` (la session sort) ou refus maintenu."""

    release: bool
    reason: str = ""
    count: int = 0
    cap: int = 0


def stop_cap(profile: str) -> int:
    """Plafond de refus consécutifs du profil *profile* (2 par défaut, 4 governed, 8 production)."""
    return _STOP_CAPS.get(profile, _STOP_CAP_DEFAULT)


def _fingerprint_excluded_prefixes() -> tuple[str, ...]:
    """Préfixes (POSIX, avec ``/`` final) des dossiers écrits par le hook ``Stop`` lui-même."""
    from grimoire.core.agentic_standard import EVENT_DIR
    from grimoire.core.standard_generation import RUNS_DIR, TRACES_DIR

    return tuple(f"{folder.as_posix()}/" for folder in (RUNS_DIR, EVENT_DIR, TRACES_DIR))


def _tree_fingerprint(project_root: Path, task_id: str) -> str:
    """Empreinte de l'arbre de travail : fichiers modifiés ou non suivis, taille et mtime.

    Hors de tout ce que le hook ``Stop`` écrit lui-même (W2-02-S1) :
    ``RUNS_DIR`` (le compteur y vit), ``EVENT_DIR`` (le journal d'événements
    que ``_gate_summary`` alimente à chaque évaluation) et ``TRACES_DIR`` (la
    trace de ``run_hook``). Ces dossiers ne sont pas ignorés par git : sans
    cette exclusion, chaque ``Stop`` changerait l'empreinte et la signature ne
    serait jamais identique. Sans git, repli sur le journal d'actions de la
    tâche (taille). Jamais d'exception.
    """
    excluded = _fingerprint_excluded_prefixes()
    try:
        listing = subprocess.run(
            ["git", "-C", str(project_root), "ls-files", "-m", "-o", "--exclude-standard", "-z"],
            capture_output=True,
            timeout=_FINGERPRINT_TIMEOUT_SECONDS,
            check=True,
        ).stdout
        digest = hashlib.sha256()
        for raw in sorted(listing.split(b"\0")):
            rel = raw.decode("utf-8", errors="replace")
            if not rel or rel.startswith(excluded):
                continue
            try:
                st = (project_root / rel).stat()
                digest.update(f"{rel}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
            except OSError:
                digest.update(f"{rel}\0gone\n".encode())
        return "git:" + digest.hexdigest()[:16]
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        size = (project_root / evidence_log_relpath(task_id)).stat().st_size
    except OSError:
        size = 0
    return f"journal:{size}"


def stop_signature(
    project_root: Path, task_id: str, summary: str, missing: list[str], *, flow_node: str = ""
) -> str:
    """Signature de progrès : ``(tâche, digest des gates rouges, empreinte d'arbre, node de flow)``.

    ``flow_node`` n'est pas lu dans un hook (le moteur de flows n'est pas
    accessible à bas coût depuis ``Stop``) : le paramètre fixe l'emplacement du
    composant, vide tant que rien ne l'alimente.
    """
    gates = hashlib.sha256((summary + "\0" + "\0".join(sorted(missing))).encode("utf-8", "replace")).hexdigest()[:16]
    return "|".join((task_id, gates, _tree_fingerprint(project_root, task_id), flow_node))


def _load_stop_chain(state: dict[str, Any], project_root: Path, task_id: str) -> tuple[str, int]:
    """``(dernière signature, refus émis)`` ; un état corrompu revient à ``("", 0)``, tracé."""
    raw = state.get(_STOP_KEY)
    if raw is None:
        return "", 0
    count = raw.get("count") if isinstance(raw, dict) else None
    signature = raw.get("signature") if isinstance(raw, dict) else None
    if isinstance(count, int) and not isinstance(count, bool) and count >= 0 and isinstance(signature, str):
        return signature, count
    record_guard_error(project_root, task_id, "done_gate.stop_state", "ValueError", f"stop={raw!r}")
    return "", 0


def record_stop_block(project_root: Path, task_id: str, signature: str) -> bool:
    """Un ``Stop`` initial vient de refuser : ouvre la chaîne (compte 1). Best-effort.

    Rend ``False`` quand la chaîne n'a pas pu être écrite (W2-02-S2) : le
    prochain ``Stop`` continué ne la trouvera pas et sortira par ``no_chain``.
    """
    path = _caps_path(project_root, task_id)
    state = _load_caps(path)
    state[_STOP_KEY] = {"signature": signature, "count": 1}
    return _save_caps(path, state)


def stop_step(project_root: Path, task_id: str, profile: str, signature: str, red_gates: str, now_iso: str) -> StopStep:
    """Un ``Stop`` continué avec des gates rouges : maintenir le refus ou relâcher.

    Relâche (et trace ``stop.released``) quand la signature est identique à celle
    du dernier refus (la session ne progresse pas), quand le plafond du profil
    est atteint (le (cap+1)-ième ``Stop`` passe toujours), ou quand aucune chaîne
    n'est connue (état absent ou corrompu : rien ne prouve un progrès, la session
    sort comme avant). Sinon le refus est maintenu et le compteur avance.

    Fail-open (W2-02-S2) : si le compteur ne peut pas être écrit, il n'avancerait
    jamais et le refus se répéterait sans fin ; la session sort (``state_unwritable``),
    tracé en ``stop.released`` et en ``guard.error``.
    """
    path = _caps_path(project_root, task_id)
    state = _load_caps(path)
    last_signature, count = _load_stop_chain(state, project_root, task_id)
    cap = stop_cap(profile)
    if count <= 0:
        return StopStep(release=True, reason="no_chain", count=0, cap=cap)
    why = "same_signature" if signature == last_signature else "cap_reached" if count >= cap else ""
    if why:
        append_guard_event(
            project_root,
            task_id,
            {
                "ts": now_iso,
                "type": STOP_RELEASED_EVENT,
                "guard_id": "done_gate.stop_signature",
                "reason": why,
                "profile": profile,
                "blocks": count,
                "cap": cap,
                "signature": signature,
                "red_gates": red_gates,
            },
        )
        state[_STOP_KEY] = {"signature": signature, "count": 0}
        _save_caps(path, state)
        return StopStep(release=True, reason=why, count=count, cap=cap)
    state[_STOP_KEY] = {"signature": signature, "count": count + 1}
    if not _save_caps(path, state):
        record_guard_error(
            project_root,
            task_id,
            "done_gate.stop_state",
            "OSError",
            f"compteur du Stop non inscriptible ({path.name}) : le refus maintenu serait sans fin, la session sort",
        )
        append_guard_event(
            project_root,
            task_id,
            {
                "ts": now_iso,
                "type": STOP_RELEASED_EVENT,
                "guard_id": "done_gate.stop_signature",
                "reason": "state_unwritable",
                "profile": profile,
                "blocks": count,
                "cap": cap,
                "signature": signature,
                "red_gates": red_gates,
            },
        )
        return StopStep(release=True, reason="state_unwritable", count=count, cap=cap)
    return StopStep(release=False, reason="signature_changed", count=count + 1, cap=cap)


def record_stop_would_block(
    project_root: Path, task_id: str, profile: str, step: StopStep, signature: str, red_gates: str, now_iso: str
) -> None:
    """Shadow (W2-02-S3) : trace le refus maintenu qui aurait été émis (``stop.would_block``). Best-effort."""
    append_guard_event(
        project_root,
        task_id,
        {
            "ts": now_iso,
            "type": STOP_WOULD_BLOCK_EVENT,
            "guard_id": "done_gate.stop_signature",
            "profile": profile,
            "blocks": step.count,
            "cap": step.cap,
            "signature": signature,
            "red_gates": red_gates,
        },
    )
