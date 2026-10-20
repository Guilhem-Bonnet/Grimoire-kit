"""``PreCompact``: keep the governance state across a context reset."""

from __future__ import annotations

from grimoire.core.standard_state import active_task_id, is_standard_enrolled
from grimoire.flows.live_runs import active_flow_lines
from grimoire.hosts.decisions._shared import Decision, HookInput, Outcome
from grimoire.hosts.decisions.gate_summary import _gate_summary
from grimoire.hosts.decisions.profile_floor import session_profile


def decide_context_capsule(hook: HookInput) -> Decision:
    """Pre compact: keep the governance state across a context reset.

    Compaction summarises the conversation; it does not know that the task id
    and the open gates are the two facts the next window cannot rebuild. The
    flow run in flight (W2-05) is the third: its run_id and node live in the
    kernel only.
    """
    if not is_standard_enrolled(hook.project_root):
        return Decision()
    task_id = active_task_id(hook.project_root, session_id=hook.session_id, agent_id=hook.agent_id)
    profile = session_profile(hook)
    try:
        ok, summary, detail = _gate_summary(hook.project_root, task_id)
    except Exception as exc:
        # The gates are unknown, the task id and profile are not: the next
        # window needs those two facts more than it needs a verdict.
        ok, summary = False, f"non évaluables — {type(exc).__name__}: {exc}"
        detail = {"task_id": task_id, "profile": profile, "error": str(exc)}
    state = "verts" if ok else f"rouges :\n{summary}"
    context = (
        f"[Grimoire — capsule] Tâche {task_id}, profil {profile}. Gates de preuve {state}\n"
        f"Preuves : _grimoire-output/evidence/{task_id}/ — à compléter avant toute clôture."
    )
    flow_lines = active_flow_lines(hook.project_root)
    if flow_lines:
        # Sans run en vol, la capsule reste identique au caractère près.
        context += "\n" + "\n".join(flow_lines)
    return Decision(outcome=Outcome.ALLOW, context=context, detail=detail)
