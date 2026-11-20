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
    context = _with_resume_state(hook, task_id, context, ok=ok, summary=summary)
    return Decision(outcome=Outcome.ALLOW, context=context, detail=detail)


def _with_resume_state(hook: HookInput, task_id: str, context: str, *, ok: bool, summary: str) -> str:
    """Ajoute l'état de reprise (objectif, fichiers, test, git…) sous le bloc de gouvernance.

    Le total reste sous ``MAX_CAPSULE_CHARS`` quand la gouvernance le permet ; elle
    n'est jamais amputée, l'état de reprise reçoit au moins 400 caractères. Sans
    aucune source (ni ledger, ni journal, ni git), le texte est celui d'avant.
    """
    from grimoire.hosts.decisions.resume_capsule import MAX_CAPSULE_CHARS, build_resume_state

    try:
        hint = next((ln.strip(" -") for ln in summary.splitlines() if ln.strip()), "")
        state = build_resume_state(
            hook.project_root,
            task_id,
            session_id=hook.session_id,
            gates_ok=ok,
            gates_hint=hint,
            budget=max(MAX_CAPSULE_CHARS - len(context) - 1, 400),
        )
    except Exception:
        return context
    return f"{context}\n{state}" if state else context
