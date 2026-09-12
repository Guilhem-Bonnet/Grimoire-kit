"""``PostToolUse``: remind the agent that a write owes a line of proof.

Also the one place :func:`grimoire.policies.temporal.record_post_tool_use_approval`
is called from (issue #429, point 3, fixed after review 2026-09-12): a
``require_approval`` temporal rule must not be marked approved by the mere
act of asking at ``PreToolUse`` — a host emits ``PostToolUse`` only when the
tool actually ran, which is the only proof that a prior ``ask`` was granted.
See that function's docstring for the fail-open bug this closes.
"""

from __future__ import annotations

from grimoire.core.standard_state import active_task_id, is_standard_enrolled
from grimoire.hosts.decisions._shared import Decision, HookInput, Outcome
from grimoire.hosts.decisions.tool_facts import classify_tool
from grimoire.policies.schemas import ActionKind


def _record_temporal_approval(hook: HookInput) -> None:
    """Best-effort, and a no-op cost (one ``Path.is_file()``) when the
    project declares no ``require_approval`` rule at all — same guard
    shape as :mod:`grimoire.hosts.decisions.tool_policy`'s own fast path.
    Never raises: a session state that fails to load or save degrades to
    "nothing recorded this call", not a broken ``PostToolUse``.
    """
    if not hook.session_id:
        return
    try:
        from grimoire.policies.rules_config import load_custom_rules

        custom_rules = load_custom_rules(hook.project_root)
        if not any(rule.require_approval for rule in custom_rules):
            return

        from datetime import UTC, datetime

        from grimoire.policies.session_state import load_session_state, save_session_state
        from grimoire.policies.temporal import record_post_tool_use_approval

        now_iso = datetime.now(UTC).isoformat()
        state = load_session_state(hook.project_root, hook.session_id, now_iso=now_iso)
        if record_post_tool_use_approval(custom_rules, state, tool_name=hook.tool_name or "unknown"):
            save_session_state(hook.project_root, state, now_iso=now_iso)
    except Exception:
        return


def decide_evidence_trace(hook: HookInput) -> Decision:
    """Post tool use: remind the agent that a write owes a line of proof."""
    _record_temporal_approval(hook)
    if not is_standard_enrolled(hook.project_root):
        return Decision()
    facts = classify_tool(hook.tool_name, hook.tool_input)
    if facts.kind is not ActionKind.FILE_WRITE:
        return Decision()
    task_id = active_task_id(hook.project_root)
    touched = ", ".join(facts.targets[:3]) or "le fichier modifié"
    context = (
        f"[Grimoire] Écriture enregistrée ({touched}). Ajoute la preuve correspondante à "
        f"_grimoire-output/evidence/{task_id}/evidence-pack.md — commande exécutée, test vert ou diff clé."
    )
    return Decision(outcome=Outcome.ALLOW, context=context, detail={"task_id": task_id, "targets": list(facts.targets)})
