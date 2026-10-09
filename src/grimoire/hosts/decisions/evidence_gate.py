"""``Stop``: refuse to end a governed task whose evidence gates are red."""

from __future__ import annotations

from dataclasses import replace

from grimoire.core.standard_state import active_profile_id, is_standard_enrolled, resolve_active_task
from grimoire.hosts.decisions._shared import Decision, HookInput, Outcome
from grimoire.hosts.decisions.enrolment import (
    BLOCKING_PROFILES,
    NO_ACTIVE_TASK,
    ambiguity_text,
    no_task_stop_reason,
    session_mutation_count,
)
from grimoire.hosts.decisions.gate_summary import _gate_summary

#: Board states that owe no evidence artifact yet. A task parked here passes
#: every gate by construction — see ``check_evidence_gates``.
_STATES_WITHOUT_EVIDENCE = {"proposed", "", None}

#: Profiles whose ``Stop`` hook refuses a closure — red gates, unevaluable
#: gates and work outside any task alike. Defined once in
#: :mod:`grimoire.hosts.decisions.enrolment` so the three paths cannot drift.
_BLOCKING_PROFILES = BLOCKING_PROFILES


def decide_evidence_gate(hook: HookInput) -> Decision:
    """Stop: refuse to end a governed task whose evidence gates are red.

    The kit's own directive says a closure without green gates is an unfinished
    task. Said in a prompt, that is a suggestion; said here, it is the rule —
    this is the only decision in the package that can make a host refuse.
    """
    if hook.stop_active:
        # The host is already re-running us after a previous block. Blocking a
        # second time is how a session becomes unexitable.
        return Decision(detail={"skipped": "stop_hook_already_active"})
    if not is_standard_enrolled(hook.project_root):
        return Decision(detail={"skipped": "project_not_enrolled"})

    active = resolve_active_task(hook.project_root, session_id=hook.session_id)
    task_id = active.task_id
    profile = active_profile_id(hook.project_root)
    if active.is_fallback:
        no_task = _no_task_closure(hook, profile, active.candidates)
        if no_task is not None:
            return no_task
        if active.source == "ambiguous":
            # Issue #692 : rien n'a été écrit, la session n'est pas bloquée —
            # mais juger la tâche fantôme `bootstrap` (« encore en état
            # proposed ») dirait faux. Même résolution et même message que
            # UserPromptSubmit : les claims concurrents et `task attach`.
            return Decision(
                context=f"[Grimoire] {ambiguity_text(active.candidates)}",
                detail={"task_id": task_id, "task_source": active.source, "candidates": list(active.candidates)},
            )
    try:
        ok, summary, detail = _gate_summary(hook.project_root, task_id)
    except Exception as exc:
        return _unevaluable_gate(task_id, profile, exc)

    if ok:
        if detail.get("state") == "archived":
            # Issue #638 lot B : une tâche annulée depuis le cockpit (ou
            # archivée) n'a plus de gate à réclamer — dit en clair plutôt
            # qu'un vert muet qui ressemblerait à une preuve acceptée.
            return Decision(
                context=f"[Grimoire] Tâche {task_id} annulée ou archivée : aucun gate de preuve n'est réclamé.",
                detail=detail,
            )
        if detail.get("state") in _STATES_WITHOUT_EVIDENCE:
            # Green because nothing is owed yet, not because the work is proven.
            # Saying so is the difference between a guardrail and a placebo.
            return Decision(
                context=(
                    f"[Grimoire] Tâche {task_id} encore en état « {detail.get('state') or 'non défini'} » : "
                    "aucun artefact de preuve n'est exigé à ce stade, donc le gate ne protège rien. "
                    "Passe la tâche à `in_progress` dans _grimoire/standard/task-board.yaml pour "
                    "que la preuve devienne opposable."
                ),
                detail=detail,
            )
        return _with_done_gate(Decision(detail=detail), hook, task_id, profile)
    if profile not in _BLOCKING_PROFILES:
        return _with_done_gate(
            Decision(
                context=(
                    f"[Grimoire] Gates de preuve rouges pour {task_id} (profil {profile}, non bloquant) :\n{summary}"
                ),
                detail=detail,
            ),
            hook,
            task_id,
            profile,
        )
    reason = (
        f"[Grimoire] Tâche {task_id} non terminée : les gates de preuve sont rouges (profil {profile}).\n"
        f"{summary}\n"
        f"Complète _grimoire-output/evidence/{task_id}/ puis relance "
        f"`grimoire standard gate check --task-id {task_id} --strict`. "
        "Si la tâche doit rester ouverte, dis-le explicitement à l'utilisateur au lieu de conclure."
    )
    return _with_done_gate(Decision(outcome=Outcome.BLOCK, reason=reason, detail=detail), hook, task_id, profile)


def _with_done_gate(decision: Decision, hook: HookInput, task_id: str, profile: str) -> Decision:
    """Attach the issue #644 "done" gate verdict, and escalate to ``BLOCK`` only if enforced.

    Called only on the two paths above where the task's board state owes
    evidence at all (:data:`_STATES_WITHOUT_EVIDENCE` and ``archived`` return
    before reaching here) — the same condition the issue asks this path to
    share with the existing gate. Never called on the ``stop_active``,
    unenrolled, unevaluable-gate or no-task-closure early returns: those
    already decide the turn on their own, and a project whose board could not
    even be read has nothing reliable to say about mutation order either.

    Best-effort in the same sense as the rest of this package: a crash inside
    :func:`grimoire.hosts.decisions.done_gate.evaluate_done_gate` must not
    turn an otherwise-fine ``Stop`` into a broken hook, so it is caught here
    and reported as an unevaluated (never a stale) verdict — never as an
    invented ``BLOCK``.
    """
    from grimoire.hosts.decisions.done_gate import DoneGateVerdict, evaluate_done_gate

    try:
        verdict = evaluate_done_gate(hook, task_id, profile)
    except Exception as exc:
        # W1-06: Record the error and fail-closed (stale=True, blocked=True)
        _record_guard_error(hook, task_id, "done_gate", str(exc), type(exc).__name__)
        verdict = DoneGateVerdict(
            stale=True,
            reason=f"error:{type(exc).__name__}",
            enforce=True,  # Force evaluation as if enforced to block
            blocked=True,  # Block if in a blocking profile
            capped=False,
        )
    detail = {**decision.detail, "done_gate": verdict.to_dict()}
    if not verdict.stale:
        return replace(decision, detail=detail)
    _record_done_gate_hold(hook, task_id)
    if verdict.blocked and decision.outcome is not Outcome.BLOCK:
        reason = (
            f"[Grimoire] Tâche {task_id} : une mutation est postérieure au dernier check vert (profil {profile}).\n"
            f"Relance `{verdict.command_hint}` ; si c'est vert, conclus. Sinon, dis-le explicitement à "
            "l'utilisateur au lieu de conclure."
        )
        return replace(decision, outcome=Outcome.BLOCK, reason=reason, detail=detail)
    if profile in _BLOCKING_PROFILES and decision.outcome is not Outcome.BLOCK:
        warning = (
            f"[Grimoire] Avertissement (gate « fini », shadow) : une mutation pour {task_id} est postérieure "
            f"au dernier check vert. Relance `{verdict.command_hint}` avant de conclure."
        )
        context = f"{decision.context}\n{warning}" if decision.context else warning
        return replace(decision, context=context, detail=detail)
    return replace(decision, detail=detail)


def _record_guard_error(
    hook: HookInput, task_id: str, guard_id: str, error_message: str, error_type: str
) -> None:
    """W1-06: Record a guard error event for ``verify`` to report.

    Writes to the evidence log so the error is traceable and visible in
    ``grimoire verify``. Best-effort: if the write fails, do not break the hook.
    """
    import json
    from datetime import UTC, datetime

    from grimoire.core.standard_checks.evidence_journal import evidence_log_relpath

    try:
        log_dir = hook.project_root / evidence_log_relpath(task_id).parent
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "evidence-log.jsonl"
        event = {
            "type": "guard_error",
            "guard_id": guard_id,
            "error_type": error_type,
            "error_message": error_message,
            "ts": datetime.now(UTC).isoformat(),
        }
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
    except Exception:  # noqa: S110 — best-effort: do not break the hook
        pass


def _record_done_gate_hold(hook: HookInput, task_id: str) -> None:
    """Calibration (Refs #644): journal a stale ``done_gate`` verdict as a ``policy.hold``.

    Written whenever :func:`~grimoire.hosts.decisions.done_gate.
    evaluate_done_gate` reports ``stale`` — shadow (unenforced) sessions
    included: that is exactly the data the party-mode design needs before
    deciding whether ``GRIMOIRE_DONE_GATE=enforce`` is safe to widen. The
    fingerprint/target key are keyed on the task, not on a command or path
    — the "action" a done-gate hold is about is "close this task", not any
    one tool call. Best-effort, same contract as
    :mod:`.tool_policy`'s own ``_record_hold``.
    """
    try:
        from grimoire.hosts.decisions.calibration import action_fingerprint, record_policy_hold, target_key

        record_policy_hold(
            hook.project_root,
            session_id=hook.session_id,
            task_id=task_id,
            hook_id="grimoire.evidence-gate",
            reason="done_gate:stale",
            fingerprint=action_fingerprint("done_gate", task_id),
            target=target_key("done_gate", task_id),
        )
    except Exception:  # noqa: S110 — observabilité : jamais au prix de la décision elle-même
        pass


def _unevaluable_gate(task_id: str, profile: str, exc: Exception) -> Decision:
    """A gate that cannot be evaluated is not a green gate.

    It used to render ``ALLOW`` with an explanatory context — and on ``Stop``
    no host reads that context, so an unreadable task board closed a governed
    task in silence, exactly where the profile promises a refusal. The rule is
    now the same as for red gates: the profiles that block, block, with the
    cause in the reason; the others are told. ``stop_active`` still guarantees
    the second ``Stop`` goes through, so a broken board costs one turn, never
    the session.
    """
    cause = f"{type(exc).__name__}: {exc}"
    detail = {"task_id": task_id, "profile": profile, "error": cause, "ok": False}
    remedy = (
        f"Répare _grimoire/standard/task-board.yaml (ou l'artefact nommé ci-dessus) puis relance "
        f"`grimoire standard gate check --task-id {task_id} --strict`."
    )
    if profile in _BLOCKING_PROFILES:
        return Decision(
            outcome=Outcome.BLOCK,
            reason=(
                f"[Grimoire] Tâche {task_id} : gates de preuve non évaluables (profil {profile}) — {cause}\n"
                f"{remedy} Si la tâche doit rester ouverte, dis-le explicitement à l'utilisateur au lieu de conclure."
            ),
            detail=detail,
        )
    return Decision(
        context=f"[Grimoire] Gates de preuve non évaluables pour {task_id} (profil {profile}, non bloquant) : {cause}\n{remedy}",
        detail=detail,
    )


def _no_task_closure(hook: HookInput, profile: str, candidates: tuple[str, ...] = ()) -> Decision | None:
    """Issue #638 lot A: a session that wrote without a task is not a finished task.

    ``bootstrap`` is a fallback, not a task: nothing in the Mission Ledger
    will ever show this work. The session journal (``session-<id>.json``,
    written by ``PostToolUse``) says whether the session wrote anything at
    all — a session that only read or answered owes nothing and is never
    blocked, which is also what happens when the host sends no ``session_id``
    (no journal, count ``0``). The profiles that block, block, with the
    remedy in the reason; the others are told, in the one field a host shows
    on ``Stop`` (``systemMessage``, see :func:`grimoire.hosts.runtime.render`).
    ``stop_active`` is checked by the caller, so the second ``Stop`` always
    goes through. Returns ``None`` when there is nothing to say.
    """
    mutations = session_mutation_count(hook)
    if mutations <= 0:
        return None
    detail = {"task_id": "bootstrap", "profile": profile, "blocked_on": NO_ACTIVE_TASK, "mutations": mutations}
    reason = no_task_stop_reason(profile, mutations, candidates)
    if profile in _BLOCKING_PROFILES:
        return Decision(outcome=Outcome.BLOCK, reason=reason, detail=detail)
    return Decision(context=reason, detail=detail)
