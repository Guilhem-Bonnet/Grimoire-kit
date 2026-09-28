"""``UserPromptSubmit``: state which task the work will be charged to."""

from __future__ import annotations

from grimoire.core.standard_state import active_profile_id, is_standard_enrolled, resolve_active_task
from grimoire.hosts.decisions._shared import Decision, HookInput, Outcome
from grimoire.hosts.decisions.enrolment import link_session, no_task_context


def decide_task_context(hook: HookInput) -> Decision:
    """Prompt submit: state which task the work will be charged to.

    Cheap, non-blocking, and it removes the single most common failure of the
    evidence protocol — writing proof under the wrong task id.

    Issue #638 lot A: when nothing designates a task (``bootstrap`` fallback),
    the context says so and hands over the copiable remedy instead of
    announcing a "current task" that is a placeholder. When a ledger claim is
    the active task, this is also where the claim learns the session that
    carries it (:func:`link_session`) — the agent that claimed never knew its
    own ``session_id``; only the host payload does.
    """
    if not is_standard_enrolled(hook.project_root):
        return Decision()
    active = resolve_active_task(hook.project_root)
    profile = active_profile_id(hook.project_root)
    detail = {"task_id": active.task_id, "profile": profile, "task_source": active.source}
    # Issue #638 lot B : ce que l'orchestrateur humain a dit depuis le cockpit
    # — consignes non livrées de la tâche active, annulation de celle que la
    # session tenait. Importé à l'usage, et muet quand il n'y a rien. Appelé
    # même quand plus aucune tâche n'est active (repli ``bootstrap``) : une
    # annulation vide la tâche que la session tenait, `resolve_active_task`
    # retombe alors sur le repli, mais l'annonce se fait par `session_id`
    # (lu au ledger), pas par la tâche resolue ici — elle doit sortir dans
    # les deux branches.
    from grimoire.hosts.decisions.steering import steering_context

    steering, steering_detail = steering_context(hook.project_root, active.task_id, hook.session_id)
    if active.source == "bootstrap":
        context = no_task_context(profile)
        if steering:
            context = f"{context}\n{steering}"
        return Decision(outcome=Outcome.ALLOW, context=context, detail={**detail, **steering_detail})
    link = link_session(hook, active)
    context = (
        f"[Grimoire] Tâche courante : {active.task_id} (profil {profile}). "
        f"Toute preuve va dans _grimoire-output/evidence/{active.task_id}/."
    )
    if steering:
        context = f"{context}\n{steering}"
    return Decision(outcome=Outcome.ALLOW, context=context, detail={**detail, **link, **steering_detail})
