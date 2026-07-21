"""Dérivation d'un ``handoff-packet`` (ORC-03) depuis une capsule de sous-agent.

Capacité produit rapatriée d'un hook d'atelier : produire un ``handoff-packet``
**conforme au contrat du catalogue** (ORC-03) à partir de la trace d'un
SubagentStop, de façon **déterministe** — sans moteur LLM. Les champs dérivables
de la capsule (``task_id``, ``summary``, ``evidence``, ``next_trigger``) sont
remplis ; ceux qui exigent une analyse (``changes``, ``assumptions``, ``risks``,
``memory_candidates``) sont marqués comme à enrichir (par ``context-summarizer``
plus tard) plutôt qu'inventés. Le contrat est honoré, jamais défini ici.
"""

from __future__ import annotations

from typing import Any

HANDOFF_SCHEMA_VERSION = "grimoire-handoff-packet/v1"
_SUMMARY_MAX = 600
_NEEDS_ENRICHMENT = "à enrichir (context-summarizer)"


def build_handoff(capsule: dict[str, Any]) -> dict[str, Any]:
    """Construit un handoff-packet ORC-03 depuis une capsule subagent-stop.

    La capsule attendue porte au moins ``agent``, ``task``, ``outputPreview``,
    ``explicitFailure`` et ``timestamp`` (trace SubagentStop).
    """
    failed = bool(capsule.get("explicitFailure"))
    task = str(capsule.get("task") or "").strip()
    preview = str(capsule.get("outputPreview") or "").strip()
    summary = preview or (f"Tâche traitée : {task}" if task else "(aucun résumé)")
    return {
        "schemaVersion": HANDOFF_SCHEMA_VERSION,
        "contract": "handoff-packet",
        "pattern": "ORC-03",
        "from": {"agent": capsule.get("agent") or "unknown", "role": "subagent"},
        # Champs du contrat.
        "task_id": task or "unknown",
        "summary": summary[:_SUMMARY_MAX],
        "changes": _NEEDS_ENRICHMENT,
        "evidence": preview[:_SUMMARY_MAX] if preview else "aucune preuve capturée",
        "assumptions": _NEEDS_ENRICHMENT,
        "risks": ["échec explicite signalé"] if failed else _NEEDS_ENRICHMENT,
        "memory_candidates": _NEEDS_ENRICHMENT,
        "next_trigger": (
            "corriger ou escalader (échec explicite)" if failed else "poursuivre le flow"
        ),
        "status": "failed" if failed else "ok",
        "producedAt": capsule.get("timestamp") or "",
        # Traçabilité : ce handoff est dérivé déterministe, pas une analyse LLM.
        "derivation": "deterministic-from-subagent-capsule",
    }


def is_subagent_stop(capsule: dict[str, Any]) -> bool:
    """Vrai si la capsule est bien une trace SubagentStop exploitable."""
    return isinstance(capsule, dict) and capsule.get("event") == "SubagentStop"
