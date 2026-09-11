"""``SubagentStop``: report gate state upward without blocking the sub-agent."""

from __future__ import annotations

from grimoire.core.standard_state import active_task_id, is_standard_enrolled
from grimoire.hosts.decisions._shared import Decision, HookInput
from grimoire.hosts.decisions.gate_summary import _gate_summary


def decide_subagent_gate(hook: HookInput) -> Decision:
    """Subagent stop: report gate state upward without blocking the sub-agent.

    A sub-agent owns a slice of the work, not the closure of the task; blocking
    it would strand the orchestrator with no way to finish the remaining slices.
    """
    if not is_standard_enrolled(hook.project_root):
        return Decision()
    task_id = active_task_id(hook.project_root)
    try:
        ok, summary, detail = _gate_summary(hook.project_root, task_id)
    except Exception as exc:
        return Decision(
            context=f"[Grimoire] Sous-agent terminé, gates non évaluables pour {task_id} : {type(exc).__name__}: {exc}",
            detail={"task_id": task_id, "error": str(exc)},
        )
    if ok:
        return Decision(detail=detail)
    return Decision(
        context=f"[Grimoire] Sous-agent terminé, gates encore rouges pour {task_id} :\n{summary}",
        detail=detail,
    )
