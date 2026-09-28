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
    if active.source == "bootstrap":
        return Decision(outcome=Outcome.ALLOW, context=no_task_context(profile), detail=detail)
    link = link_session(hook, active)
    context = (
        f"[Grimoire] Tâche courante : {active.task_id} (profil {profile}). "
        f"Toute preuve va dans _grimoire-output/evidence/{active.task_id}/."
    )
    return Decision(outcome=Outcome.ALLOW, context=context, detail={**detail, **link})
