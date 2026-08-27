"""Projection du Mission Ledger vers le task board du standard (ADR-005).

Le ledger est la source de vérité ; ``_grimoire/standard/task-board.yaml`` en est
une **projection exportée**. Ce module porte la conversion, et il est le seul
endroit où elle est écrite : toute surface qui a besoin de traduire un état passe
par ici.

La projection est délibérément à sens unique. La fonction inverse
(:func:`task_state_of`) existe pour **importer** un board écrit à la main dans un
ledger — une migration ponctuelle — et elle est lossy par construction : neuf
états ledger se projettent sur huit états board, donc l'aller-retour ne restitue
pas l'état de départ, seulement une classe d'équivalence.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from grimoire.missions.schemas import DependencyKind, RiskProfile, TaskState

if TYPE_CHECKING:
    from grimoire.missions.ledger import MissionLedger
    from grimoire.missions.schemas import MissionTask

__all__ = [
    "BOARD_LIFECYCLE",
    "board_status_of",
    "build_board",
    "task_state_of",
]

# Ordre normatif du cycle de vie, tel que le vérificateur du standard l'attend.
BOARD_LIFECYCLE: tuple[str, ...] = (
    "proposed",
    "ready",
    "in_progress",
    "blocked",
    "review",
    "accepted",
    "released",
    "archived",
)

# Ledger → board. Total : tout état du ledger a une colonne.
_TO_BOARD: dict[TaskState, str] = {
    TaskState.PROPOSED: "proposed",
    TaskState.READY: "ready",
    # Réclamée mais pas encore démarrée : du point de vue du tableau, le travail
    # a commencé — quelqu'un s'est engagé et la carte n'est plus disponible.
    TaskState.CLAIMED: "in_progress",
    TaskState.RUNNING: "in_progress",
    TaskState.BLOCKED: "blocked",
    TaskState.NEEDS_VERIFICATION: "review",
    # Le board n'a pas de colonne d'échec. « Bloqué » est l'endroit honnête : la
    # tâche n'avance plus et demande une remédiation explicite.
    TaskState.FAILED: "blocked",
    # Le ledger ne modélise pas le cycle de publication : une tâche close est
    # acceptée, elle ne devient « publiée » que par une décision de release.
    TaskState.CLOSED: "accepted",
    TaskState.CANCELLED: "archived",
}

# Board → ledger, pour l'import d'un board existant. Les fusions ci-dessus sont
# irréversibles : on retient l'état le plus prudent de chaque classe.
_FROM_BOARD: dict[str, TaskState] = {
    "proposed": TaskState.PROPOSED,
    "ready": TaskState.READY,
    "in_progress": TaskState.RUNNING,
    "blocked": TaskState.BLOCKED,
    "review": TaskState.NEEDS_VERIFICATION,
    "accepted": TaskState.CLOSED,
    # Une carte publiée est close côté ledger : la publication est un fait de
    # release, pas un état de travail.
    "released": TaskState.CLOSED,
    "archived": TaskState.CANCELLED,
}

_PRIORITY_BY_RISK: dict[RiskProfile, str] = {
    RiskProfile.LIGHT: "low",
    RiskProfile.STANDARD: "medium",
    RiskProfile.STRICT: "high",
    RiskProfile.SECURITY_CRITICAL: "high",
    RiskProfile.RELEASE: "high",
}


def board_status_of(state: TaskState) -> str:
    """Colonne du board pour un état du ledger."""
    return _TO_BOARD[state]


def task_state_of(status: str) -> TaskState:
    """État ledger pour une colonne du board — import seulement, et lossy.

    Lève :class:`ValueError` sur une colonne inconnue : mieux vaut refuser un
    board hors norme que de deviner un état de travail.
    """
    try:
        return _FROM_BOARD[status]
    except KeyError:
        msg = f"Statut de board inconnu : {status!r}. Attendus : {', '.join(BOARD_LIFECYCLE)}."
        raise ValueError(msg) from None


def _blockers(task: MissionTask) -> list[dict[str, str]]:
    """Blocages déclarés — le vérificateur exige un motif sur toute carte bloquée."""
    out = [
        {"reason": f"dépend de {dep.target}", "target": dep.target}
        for dep in task.dependencies
        if dep.kind is DependencyKind.BLOCKS
    ]
    if not out and task.status is TaskState.FAILED:
        out.append({"reason": "échec de la tâche, remédiation requise", "target": task.id})
    if not out and task.status is TaskState.BLOCKED:
        out.append({"reason": "blocage déclaré sans dépendance nommée", "target": task.id})
    return out


def _task_entry(task: MissionTask) -> dict[str, Any]:
    status = board_status_of(task.status)
    owner = task.owner or (task.claim.actor_id if task.claim else "")
    entry: dict[str, Any] = {
        "task_id": task.id,
        "title": task.title,
        "status": status,
        "priority": _PRIORITY_BY_RISK.get(task.risk_profile, "medium"),
        "owner": owner,
        "agent_roles": [task.type.value],
        "acceptance_criteria": list(task.acceptance),
        "blockers": _blockers(task),
        # Chemins conventionnels du standard, indexés par task_id.
        "context_bundle_ref": f"_grimoire-output/context/{task.id}/context-bundle.yaml",
        "decision_trace_ref": f"_grimoire-output/decisions/{task.id}/decision-trace.yaml",
        "evidence_pack_ref": f"_grimoire-output/evidence/{task.id}/evidence-pack.md",
    }
    # Ce que le YAML ignorait et que le ledger sait : de quoi la carte parle.
    if task.description:
        entry["description"] = task.description
    if task.guardrails:
        entry["guardrails"] = list(task.guardrails)
    if task.expected_evidence:
        entry["expected_evidence"] = list(task.expected_evidence)
    if task.surface:
        entry["surface"] = task.surface
    if status == "blocked":
        entry["remediation_ref"] = "_grimoire/standard/remediation-plan.yaml"
    return entry


def build_board(
    ledger: MissionLedger,
    *,
    project: str = "",
    mission_id: str | None = None,
) -> dict[str, Any]:
    """Construit la projection board complète depuis le ledger.

    Le résultat est destiné à ``_grimoire/standard/task-board.yaml`` et satisfait
    le vérificateur du standard : cycle de vie normatif, clés requises sur chaque
    carte, motif de blocage sur toute carte bloquée.
    """
    tasks = sorted(ledger.list_tasks(mission_id), key=lambda t: (BOARD_LIFECYCLE.index(board_status_of(t.status)), t.id))
    return {
        "$schema": "grimoire-agentic-standard-task-board/v1",
        "metadata": {
            "project": project,
            "generated_by": "grimoire task board export",
            "purpose": "Projection du Mission Ledger (ADR-005) — ne pas éditer à la main.",
            "source": "mission-ledger",
        },
        "states": list(BOARD_LIFECYCLE),
        "transitions": _TRANSITIONS,
        "tasks": [_task_entry(task) for task in tasks],
    }


# Portes du cycle de vie — inchangées : c'est la promesse du standard, pas une
# conséquence du ledger.
_TRANSITIONS: dict[str, dict[str, list[str]]] = {
    "proposed_to_ready": {"requires": ["acceptance_criteria", "owner_or_agent_role"]},
    "ready_to_in_progress": {"requires": ["context_bundle", "provider_policy"]},
    "in_progress_to_review": {"requires": ["evidence_pack", "decision_trace"]},
    "review_to_accepted": {"requires": ["review_gate", "evidence_gate"]},
    "accepted_to_released": {"requires": ["compliance_score", "release_authorization"]},
    "any_to_blocked": {"requires": ["blocker_reason", "remediation_plan"]},
}
