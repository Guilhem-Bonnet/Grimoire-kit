"""``Stop``: refuse to end a governed task whose evidence gates are red."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from grimoire.core.standard_checks.evidence_journal import record_guard_error
from grimoire.core.standard_state import active_profile_id, is_standard_enrolled, resolve_active_task
from grimoire.hosts.decisions._shared import Decision, HookInput, Outcome
from grimoire.hosts.decisions.enrolment import (
    BLOCKING_PROFILES,
    NO_ACTIVE_TASK,
    TASK_UNPARKED,
    ambiguity_text,
    expired_text,
    no_task_stop_reason,
    record_unattached_work,
    session_mutation_count,
    stop_scope,
    waiting_task_of_session,
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
        # The host is already re-running us after a previous block. Blocking
        # again without limit is how a session becomes unexitable : W2-02 keeps
        # the refusal only while the progress signature changes, under a cap.
        return _continued_stop(hook)
    if not is_standard_enrolled(hook.project_root):
        return Decision(detail={"skipped": "project_not_enrolled"})

    active = stop_scope(hook, resolve_active_task(hook.project_root, session_id=hook.session_id))
    task_id = active.task_id
    profile = active_profile_id(hook.project_root)
    if active.is_fallback:
        parked = _parked_waiting(hook)
        if parked is not None:
            return parked
        no_task = _no_task_closure(hook, profile, active.candidates, active.expired)
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
        if active.expired:
            # Issue #710 : même logique que #692 — rien d'écrit, pas de blocage,
            # mais dire « claim expiré » plutôt que juger la tâche fantôme.
            return Decision(
                context=f"[Grimoire] {expired_text(active.expired)}",
                detail={"task_id": task_id, "task_source": active.source, "expired": list(active.expired)},
            )
    try:
        ok, summary, detail = _gate_summary(hook.project_root, task_id)
    except Exception as exc:
        return _unevaluable_gate(task_id, profile, exc)

    if ok:
        unparked = _unparked_task(hook, active, profile, detail)
        if unparked is not None and detail.get("state") != "archived":
            return unparked
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
    _open_stop_chain(hook, task_id, summary, detail)
    return _with_done_gate(Decision(outcome=Outcome.BLOCK, reason=reason, detail=detail), hook, task_id, profile)


def _parked_waiting(hook: HookInput) -> Decision | None:
    """K6 : la tâche de la session est garée en ``waiting`` — la session peut s'arrêter, et le dit.

    Sans cela, une session qui vient de poser sa question (``task ask``) ou son attente
    (``task wait``) était refusée au ``Stop`` en profil gouverné comme un travail hors tâche.
    """
    task = waiting_task_of_session(hook)
    if task is None:
        return None
    reason = task.waiting.reason if task.waiting is not None else "?"
    return Decision(
        context=(
            f"[Grimoire] Tâche {task.id} garée en attente ({reason}) : la session peut s'arrêter ; "
            "elle reprendra avec `grimoire task resume` ou par un nouveau claim une fois l'attente levée."
        ),
        detail={"task_id": task.id, "task_state": task.status.value, "waiting_reason": reason, "parked": True},
    )


def _unparked_task(hook: HookInput, active: Any, profile: str, detail: dict[str, Any]) -> Decision | None:
    """§5.1 : une tâche rattachée à la session n'est quittée que fermée ou garée avec une raison.

    Les gates de preuve sont verts, mais le ledger tient encore la tâche (``claimed`` ou
    ``running``) : ni rendue pour vérification, ni bloquée, ni échouée, ni annulée. Un profil
    bloquant refuse la clôture en nommant les gestes qui la garent ; les autres le disent.
    « Garée » : ``needs_verification``/``blocked``/``failed``/``cancelled``, ou ``waiting`` (K6,
    :func:`_parked_waiting`). ``stop_hook_active`` passe avant ce contrôle : le second ``Stop`` sort toujours.
    """
    if active.source not in ("session_claim", "env", "ledger_claim"):
        return None
    try:
        from grimoire.core.standard_state import LEDGER_RELPATH
        from grimoire.missions.ledger import MissionLedger
        from grimoire.missions.schemas import TaskState

        events = hook.project_root.resolve() / LEDGER_RELPATH / "events.jsonl"
        task = MissionLedger(events.parent).get_task(active.task_id) if events.is_file() else None
    except Exception:
        return None
    if task is None or task.status not in (TaskState.CLAIMED, TaskState.RUNNING):
        return None
    message = (
        f"[Grimoire] Tâche {task.id} encore {task.status.value} : cette session la tient (bail epoch "
        f"{task.claim.lease_epoch if task.claim else 0}) et ne l'a ni fermée ni garée.\n"
        f"Rends-la avant de conclure : `grimoire task move {task.id} --to needs_verification` (preuve remise), "
        f"`grimoire task block {task.id} --reason ...` (bloquée), `grimoire task ask {task.id} \"...\"` "
        f"(une décision attend l'humain), `grimoire task wait {task.id} --reason ci|quota|paused` (attente nommée), "
        f"ou `grimoire task cancel {task.id} --reason ...` (abandon). "
        "Si elle doit rester en cours, dis-le explicitement à l'utilisateur au lieu de conclure."
    )
    info = {**detail, "blocked_on": TASK_UNPARKED, "task_id": task.id, "task_state": task.status.value}
    if profile in _BLOCKING_PROFILES:
        return Decision(outcome=Outcome.BLOCK, reason=message, detail=info)
    return Decision(context=message, detail=info)


def _signature_of(hook: HookInput, task_id: str, summary: str, detail: dict[str, Any]) -> str:
    from grimoire.hosts.decisions.done_gate import stop_signature

    return stop_signature(hook.project_root, task_id, summary, [str(m) for m in detail.get("missing") or []])


def _open_stop_chain(hook: HookInput, task_id: str, summary: str, detail: dict[str, Any]) -> None:
    """W2-02 : un premier refus ouvre la chaîne que les ``Stop`` continués jugeront. Best-effort."""
    from grimoire.hosts.decisions.done_gate import record_stop_block

    try:
        if not record_stop_block(hook.project_root, task_id, _signature_of(hook, task_id, summary, detail)):
            # W2-02-S2 : la chaîne n'existe pas sur disque, le Stop continué sortira par ``no_chain`` — dit, pas tu.
            record_guard_error(
                hook.project_root, task_id, "done_gate.stop_chain", "OSError", "chaîne du Stop non inscriptible"
            )
    except Exception as exc:
        record_guard_error(hook.project_root, task_id, "done_gate.stop_chain", type(exc).__name__, str(exc))


def _continued_stop(hook: HookInput) -> Decision:
    """W2-02 : ``Stop`` rejoué par l'hôte après un refus — maintenu tant que la session progresse.

    Tout ce qui n'est pas « gates rouges en profil bloquant avec une chaîne de
    refus connue » sort comme avant (``stop_hook_already_active``). Sinon la
    signature de progrès décide : changée et sous le plafond du profil, le refus
    est maintenu ; identique ou plafond atteint, la session sort et
    ``stop.released`` est tracé (journal de garde de la tâche).

    Shadow d'abord (W2-02-S3) : tant que ni ``GRIMOIRE_DONE_GATE=enforce`` ni
    ``options.done_gate: enforce`` n'est posé (le même interrupteur que le gate
    « fini »), le refus maintenu n'est pas émis : il est calculé, compté et tracé
    (``stop.would_block``), la session sort comme avant.
    """
    skipped = Decision(detail={"skipped": "stop_hook_already_active"})
    try:
        if not is_standard_enrolled(hook.project_root):
            return skipped
        active = resolve_active_task(hook.project_root, session_id=hook.session_id)
        profile = active_profile_id(hook.project_root)
        if active.is_fallback or profile not in _BLOCKING_PROFILES:
            return skipped
        task_id = active.task_id
        ok, summary, detail = _gate_summary(hook.project_root, task_id)
        if ok:
            return skipped
        from grimoire.hosts.decisions.done_gate import _is_enforced, record_stop_would_block, stop_step

        signature = _signature_of(hook, task_id, summary, detail)
        now_iso = datetime.now(UTC).isoformat()
        step = stop_step(hook.project_root, task_id, profile, signature, summary, now_iso)
        shadow = not step.release and not _is_enforced(hook.project_root, task_id)
        if shadow:
            record_stop_would_block(hook.project_root, task_id, profile, step, signature, summary, now_iso)
    except Exception as exc:
        # Un échec ici ne doit jamais rendre la session inquittable : elle sort, et c'est tracé.
        record_guard_error(hook.project_root, "bootstrap", "done_gate.stop_signature", type(exc).__name__, str(exc))
        return skipped
    detail = {**detail, "stop_signature": {"release": step.release, "reason": step.reason, "blocks": step.count, "cap": step.cap}}
    if step.release:
        return Decision(detail={**detail, "skipped": "stop_hook_already_active"})
    if shadow:
        detail["stop_signature"]["shadow"] = True
        return Decision(detail={**detail, "skipped": "stop_hook_already_active"})
    reason = (
        f"[Grimoire] Tâche {task_id} non terminée : les gates de preuve sont encore rouges (profil {profile}), "
        f"mais la session a progressé depuis le refus précédent (refus {step.count}/{step.cap}).\n"
        f"{summary}\n"
        f"Complète _grimoire-output/evidence/{task_id}/ puis relance "
        f"`grimoire standard gate check --task-id {task_id} --strict`. "
        "Si la tâche doit rester ouverte, dis-le explicitement à l'utilisateur au lieu de conclure."
    )
    return Decision(outcome=Outcome.BLOCK, reason=reason, detail=detail)


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
    and traced as a ``guard.error`` (W1-06). It is never reported as a stale
    verdict (no mutation was observed to be stale), but it is not a silent
    ``ALLOW`` either: see :func:`_done_gate_unevaluable`.
    """
    from grimoire.hosts.decisions.done_gate import evaluate_done_gate

    try:
        verdict = evaluate_done_gate(hook, task_id, profile)
    except Exception as exc:
        return _done_gate_unevaluable(decision, hook, task_id, profile, exc)
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


def _done_gate_unevaluable(decision: Decision, hook: HookInput, task_id: str, profile: str, exc: Exception) -> Decision:
    """W1-06: le gate « fini » a planté — tracé, puis fail-closed là où le profil le promet.

    Même règle que pour une mutation périmée (pas de verdict ``stale``
    inventé, pas de ``policy.hold``) : un refus seulement dans un profil
    bloquant avec l'opt-in ``enforce``, via le refroidissement et le plafond de
    session ; sinon un avertissement, jamais un ``BLOCK``.
    """
    from grimoire.hosts.decisions.done_gate import (
        DoneGateVerdict,
        caps_state_relpath,
        resolve_done_gate_block,
        resolve_done_gate_block_after_crash,
    )

    cause = f"{type(exc).__name__}: {exc}"
    record_guard_error(hook.project_root, task_id, "done_gate", type(exc).__name__, str(exc))
    now_iso = datetime.now(UTC).isoformat()
    remedy = ""
    try:
        enforce, blocked, capped = resolve_done_gate_block(
            hook.project_root, task_id, hook.session_id, profile, now_iso
        )
    except Exception as resolution_exc:
        # Pas de silence (W1-06) : la panne de la résolution est tracée, et l'opt-in
        # ``enforce`` n'est pas réécrit à False — fail-closed en profil bloquant.
        record_guard_error(
            hook.project_root, task_id, "done_gate.resolution", type(resolution_exc).__name__, str(resolution_exc)
        )
        enforce, blocked, capped = resolve_done_gate_block_after_crash(
            hook.project_root, task_id, profile, hook.session_id
        )
        remedy = (
            f"\nL'état de refroidissement et de plafond {caps_state_relpath(task_id).as_posix()} est illisible "
            f"({type(resolution_exc).__name__}) : supprime ce fichier pour le réarmer."
        )
    verdict = DoneGateVerdict(
        stale=False, reason=f"error:{type(exc).__name__}", enforce=enforce, blocked=blocked, capped=capped
    )
    detail = {**decision.detail, "done_gate": verdict.to_dict()}
    if blocked and decision.outcome is not Outcome.BLOCK:
        reason = (
            f"[Grimoire] Tâche {task_id} : gate « fini » non évaluable (profil {profile}) — {cause}{remedy}\n"
            f"Cette panne est tracée (guard.error, visible via `grimoire standard verify --task-id {task_id}`). "
            "Si la tâche doit rester ouverte, dis-le explicitement à l'utilisateur au lieu de conclure."
        )
        return replace(decision, outcome=Outcome.BLOCK, reason=reason, detail=detail)
    warning = (
        f"[Grimoire] Avertissement : gate « fini » non évaluable pour {task_id} (profil {profile}) — {cause}{remedy}"
    )
    context = f"{decision.context}\n{warning}" if decision.context else warning
    return replace(decision, context=context, detail=detail)


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


def _no_task_closure(
    hook: HookInput, profile: str, candidates: tuple[str, ...] = (), expired: tuple[str, ...] = ()
) -> Decision | None:
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
    detail["unattached_work"] = record_unattached_work(hook, mutations)
    reason = no_task_stop_reason(profile, mutations, candidates, expired)
    if profile in _BLOCKING_PROFILES:
        return Decision(outcome=Outcome.BLOCK, reason=reason, detail=detail)
    return Decision(context=reason, detail=detail)
