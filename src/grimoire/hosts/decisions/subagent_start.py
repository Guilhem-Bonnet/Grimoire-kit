"""``SubagentStart`` sous le harness Copilot : dire au sous-agent sa classe de vérifiabilité (#1280, P3).

Le hook ``SubagentStart`` de VS Code accepte ``hookSpecificOutput.additionalContext``
« ajouté au contexte du sous-agent »
(https://code.visualstudio.com/docs/agents/reference/hooks-reference). Le contexte
dit la classe V0/V1/V2 de la tâche réclamée et le palier que la politique de
dispatch recommande — le calcul est celui de ``task_claim`` (:func:`dispatch_advice`),
jamais une seconde classification. Le nom de modèle Copilot n'y figure que si le
projet l'a déclaré (``dispatch.models.copilot``).

Consultatif : aucune capacité bloquante n'est annoncée (règle #1304, pas de
capture). Hors harness Copilot, ou sans tâche réclamée, la décision s'abstient :
Claude Code et le harness Local gardent leur sortie actuelle. Ne lève jamais.

``--host copilot`` ne suffit pas : VS Code exécute
``.github/hooks/grimoire-subagent-start.json`` sous ses deux harness, et le
harness Local (déprécié) envoie une charge snake_case sans ``timestamp`` que la
forme ne distingue pas d'une autre. La décision n'émet donc que si la charge
porte une marque sourcée du harness Copilot
(:func:`grimoire.hosts.copilot_wire.copilot_harness_payload`) ; une charge
indécidable, ou de forme Claude Code, garde la sortie actuelle : rien.
"""

from __future__ import annotations

from grimoire.hosts import copilot_wire
from grimoire.hosts.decisions._shared import Decision, HookInput
from grimoire.hosts.decisions.dispatch_model import _class_tier, _is_copilot, copilot_model_for
from grimoire.hosts.model_table import kit_model

#: Identifiant de la décision, référencé par l'émetteur Copilot.
DECISION_ID = "grimoire.subagent-start"


def decide_subagent_start(hook: HookInput) -> Decision:
    """Le contexte de dispatch d'un sous-agent qui démarre, ou une abstention."""
    try:
        if not _is_copilot(hook) or not copilot_wire.copilot_harness_payload(hook.raw or {}):
            return Decision(abstain=True)
        tier, detail = _class_tier(hook)
        cls = str(detail.get("task_class") or "")
        if not cls:
            return Decision(abstain=True)
        task_id = detail.get("task_id")
        if not tier:
            advice = "le modèle de la session (palier session) : ne change pas de modèle"
        else:
            label = kit_model(tier) or tier
            name = copilot_model_for(hook.project_root, tier)
            advice = f"palier {label}" + (f" (modèle Copilot {name})" if name else "")
        return Decision(
            context=(
                f"[Grimoire] Tâche {task_id} de classe de vérifiabilité {cls} : modèle recommandé pour ce "
                f"sous-agent — {advice}."
            ),
            detail={**detail, "tier": tier or "session", "subagent_start": "copilot"},
        )
    except Exception as exc:
        return Decision(abstain=True, detail={"subagent_start": "error", "error": f"{type(exc).__name__}: {exc}"})
