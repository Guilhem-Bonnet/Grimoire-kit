"""``PostToolUse``: remind the agent that a write owes a line of proof."""

from __future__ import annotations

from grimoire.core.standard_state import active_task_id, is_standard_enrolled
from grimoire.hosts.decisions._shared import Decision, HookInput, Outcome
from grimoire.hosts.decisions.tool_facts import classify_tool
from grimoire.policies.schemas import ActionKind


def decide_evidence_trace(hook: HookInput) -> Decision:
    """Post tool use: remind the agent that a write owes a line of proof."""
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
