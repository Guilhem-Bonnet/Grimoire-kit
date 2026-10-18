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

    Issue #645 lot 5.2 : c'est aussi ici, avant toute question d'enrôlement,
    que la mémoire de session bornée apprend le tour humain — les seuls mots
    qui vaudront autorisation pour :mod:`.tool_policy` (voir
    :mod:`.session_memory`). Un projet non enrôlé garde la même protection
    que :func:`.tool_policy.decide_tool_policy` lui-même, qui ne conditionne
    jamais son propre verdict à l'enrôlement.

    W2-01 : c'est aussi le seul point d'écriture du journal des tours humains
    (:mod:`.human_turns`), qu'un checkpoint de flow relit pour classer sa
    décision ``attested`` ou ``unattested`` — enrôlé ou non, comme la mémoire.
    """
    from grimoire.hosts.decisions.human_turns import record_human_turn
    from grimoire.hosts.decisions.session_memory import record_user_message

    record_user_message(hook)
    record_human_turn(hook)
    if not is_standard_enrolled(hook.project_root):
        return Decision()
    active = resolve_active_task(hook.project_root, session_id=hook.session_id)
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
    # K5 : une tâche reprise n'est plus celle de la session (statut `ready`, ou
    # claim d'une autre session) — l'annonce se lit par session, pas par tâche
    # active, donc dans les deux branches.
    from grimoire.hosts.decisions.pause_brake import pause_context, pause_lifted_notices
    from grimoire.hosts.decisions.self_view import takeover_notices

    # K11 : la pause demandée, et la pause levée pour une tâche que la session avait garée.
    pause = "\n".join(
        part
        for part in (pause_context(hook, active.task_id), pause_lifted_notices(hook.project_root, hook.session_id))
        if part
    )

    if active.is_fallback:
        context = no_task_context(profile, active.candidates, active.expired)
        takeover = takeover_notices(hook.project_root, hook.session_id)
        if takeover:
            context = f"{context}\n{takeover}"
        if pause:
            context = f"{context}\n{pause}"
        if steering:
            context = f"{context}\n{steering}"
        return Decision(outcome=Outcome.ALLOW, context=context, detail={**detail, **steering_detail})
    link = link_session(hook, active)
    from grimoire.hosts.decisions.self_view import self_view_context

    self_view = self_view_context(hook.project_root, active.task_id, hook.session_id)
    context = (
        f"[Grimoire] Tâche courante : {active.task_id} (profil {profile}). "
        f"Toute preuve va dans _grimoire-output/evidence/{active.task_id}/."
    )
    if self_view:
        context = f"{context}\n{self_view}"
    takeover = takeover_notices(hook.project_root, hook.session_id)
    if takeover:
        context = f"{context}\n{takeover}"
    if pause:
        context = f"{context}\n{pause}"
    if steering:
        context = f"{context}\n{steering}"
    return Decision(outcome=Outcome.ALLOW, context=context, detail={**detail, **link, **steering_detail})
