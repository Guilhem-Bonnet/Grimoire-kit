"""``Stop``: refuse to end a governed task whose evidence gates are red."""

from __future__ import annotations

from grimoire.core.standard_state import active_profile_id, active_task_id, is_standard_enrolled
from grimoire.hosts.decisions._shared import Decision, HookInput, Outcome
from grimoire.hosts.decisions.gate_summary import _gate_summary

#: Board states that owe no evidence artifact yet. A task parked here passes
#: every gate by construction — see ``check_evidence_gates``.
_STATES_WITHOUT_EVIDENCE = {"proposed", "", None}

#: Profiles whose ``Stop`` hook refuses a closure — red gates and unevaluable
#: gates alike. Kept in one place so the two paths cannot drift apart.
_BLOCKING_PROFILES = frozenset({"governed", "production"})


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

    task_id = active_task_id(hook.project_root)
    profile = active_profile_id(hook.project_root)
    try:
        ok, summary, detail = _gate_summary(hook.project_root, task_id)
    except Exception as exc:
        return _unevaluable_gate(task_id, profile, exc)

    if ok:
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
        return Decision(detail=detail)
    if profile not in _BLOCKING_PROFILES:
        return Decision(
            context=(f"[Grimoire] Gates de preuve rouges pour {task_id} (profil {profile}, non bloquant) :\n{summary}"),
            detail=detail,
        )
    reason = (
        f"[Grimoire] Tâche {task_id} non terminée : les gates de preuve sont rouges (profil {profile}).\n"
        f"{summary}\n"
        f"Complète _grimoire-output/evidence/{task_id}/ puis relance "
        f"`grimoire standard gate check --task-id {task_id} --strict`. "
        "Si la tâche doit rester ouverte, dis-le explicitement à l'utilisateur au lieu de conclure."
    )
    return Decision(outcome=Outcome.BLOCK, reason=reason, detail=detail)


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
