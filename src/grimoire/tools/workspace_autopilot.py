"""Routes du pilote automatique (P82, P83) : état, journal des décisions, « Revenir ». L'écriture d'activation reste
dans `workspace_routes` (`_autopilot_set`), derrière la clé de contrôle."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def state(project_root: Path, _query: dict[str, list[str]]) -> Any:
    """État du pilote automatique (P82) : lecture seule, jamais d'activation ici ; les décisions sont au ledger."""
    from grimoire.missions.autopilot import RESERVED, AutopilotStore, load_autopilot_policy

    policy = load_autopilot_policy(project_root.resolve())
    return {
        **AutopilotStore(project_root).state().to_dict(),
        "reserved": sorted(RESERVED), "extra_reserved": list(policy.extra), "policy_error": policy.error,
    }


def decisions(project_root: Path, _query: dict[str, list[str]]) -> Any:
    """Le journal « Décidé par le pilote » (P83) : les décisions du ledger, la plus récente d'abord. Lecture seule."""
    from grimoire.missions.autopilot_view import journal
    from grimoire.missions.service import TaskService

    service = TaskService(project_root.resolve())
    if not service.has_ledger:
        return {"decisions": []}
    return {"decisions": journal(service)}


def revert(project_root: Path, body: dict[str, Any]) -> Any:
    """« Revenir » sur une décision du pilote (P83) : rouvre la tâche ou la rend à l'humain. Le jeton suffit :
    c'est reprendre une décision, pas en prendre une."""
    from grimoire.missions.autopilot_view import revert as revert_decision
    from grimoire.missions.service import TaskService

    task_id, question_id = str(body.get("task_id") or ""), str(body.get("question_id") or "")
    if not task_id or not question_id:
        raise ValueError("`task_id` et `question_id` requis")
    return revert_decision(
        TaskService(project_root.resolve()), task_id, question_id, str(body.get("actor") or "workspace"),
    )
