"""``UserPromptSubmit``: state which task the work will be charged to."""

from __future__ import annotations

from grimoire.core.standard_state import active_profile_id, active_task_id, is_standard_enrolled
from grimoire.hosts.decisions._shared import Decision, HookInput, Outcome


def decide_task_context(hook: HookInput) -> Decision:
    """Prompt submit: state which task the work will be charged to.

    Cheap, non-blocking, and it removes the single most common failure of the
    evidence protocol — writing proof under the wrong task id.
    """
    if not is_standard_enrolled(hook.project_root):
        return Decision()
    task_id = active_task_id(hook.project_root)
    profile = active_profile_id(hook.project_root)
    context = (
        f"[Grimoire] Tâche courante : {task_id} (profil {profile}). "
        f"Toute preuve va dans _grimoire-output/evidence/{task_id}/."
    )
    return Decision(outcome=Outcome.ALLOW, context=context, detail={"task_id": task_id, "profile": profile})
