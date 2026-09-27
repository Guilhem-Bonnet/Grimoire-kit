"""``UserPromptSubmit``: state which task the work will be charged to."""

from __future__ import annotations

from typing import Any

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
    detail: dict[str, Any] = {"task_id": task_id, "profile": profile}
    # Issue #638 lot B : ce que l'orchestrateur humain a dit depuis le cockpit
    # — consignes non livrées de la tâche active, annulation de celle que la
    # session tenait. Importé à l'usage, et muet quand il n'y a rien.
    from grimoire.hosts.decisions.steering import steering_context

    steering, steering_detail = steering_context(hook.project_root, task_id, hook.session_id)
    if steering:
        context = f"{context}\n{steering}"
        detail.update(steering_detail)
    return Decision(outcome=Outcome.ALLOW, context=context, detail=detail)
