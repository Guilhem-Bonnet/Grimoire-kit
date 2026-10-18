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

from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.missions.schemas import DependencyKind, RiskProfile, TaskState
from grimoire.missions.verifiability import as_dict as verifiability_as_dict
from grimoire.missions.waiting import attention_rank

if TYPE_CHECKING:
    from grimoire.missions.ledger import MissionLedger
    from grimoire.missions.schemas import MissionTask

__all__ = [
    "BOARD_LIFECYCLE",
    "PRIORITIES",
    "board_status_of",
    "build_board",
    "priority_of",
    "priority_rank",
    "task_state_of",
    "write_board",
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
    # État écrit par une version plus récente : la tâche est figée et n'avance
    # plus ici ; « bloqué » est la colonne la plus proche, la valeur brute
    # restant dans le ledger.
    TaskState.UNKNOWN: "blocked",
    # Une attente nommée (K6) : la tâche n'avance pas tant qu'on n'a pas réglé ce qu'elle
    # attend. Le board du standard n'a que huit colonnes ; « bloqué » est la plus proche,
    # et la carte dit pourquoi (``_blockers``). C'est aussi ce qui la garde hors de
    # « en cours » : une tâche garée n'est plus celle que la session doit clore.
    TaskState.WAITING: "blocked",
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

#: L'échelle de priorité reconnue, de la moins pressante à la plus pressante
#: (issue #638, lot B). Les trois valeurs que `_PRIORITY_BY_RISK` dérivait déjà,
#: plus ``critical`` pour ce qu'un humain veut voir passer devant tout le reste.
#: Une valeur hors de cette échelle est refusée par ``TaskService.prioritize``.
PRIORITIES: tuple[str, ...] = ("low", "medium", "high", "critical")


def priority_of(task: MissionTask) -> str:
    """La priorité effective d'une tâche : déclarée, sinon dérivée du ``risk_profile``."""
    return task.priority or _PRIORITY_BY_RISK.get(task.risk_profile, "medium")


def priority_rank(priority: str) -> int:
    """Rang de tri d'une priorité — plus grand = plus pressant ; inconnue = la plus basse."""
    return PRIORITIES.index(priority) if priority in PRIORITIES else -1


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


def _waiting_reason(task: MissionTask) -> str:
    """Le motif de blocage d'une tâche en attente : la raison, et la question quand c'est un humain."""
    waiting = task.waiting
    if waiting is None:
        return "en attente"
    if waiting.question is not None:
        return f"en attente d'une décision humaine : {waiting.question.text}"
    until = f" jusqu'à {waiting.until}" if waiting.until else ""
    return f"en attente ({waiting.reason}){until}"


def _blockers(task: MissionTask, closed: frozenset[str] = frozenset()) -> list[dict[str, str]]:
    """Blocages déclarés — le vérificateur exige un motif sur toute carte bloquée.

    Une dépendance ``blocks`` déjà close ne bloque plus rien (W5-01 : les liens
    sont désormais rejoués, une carte ne garde pas un blocage levé).
    """
    out = [
        {"reason": f"dépend de {dep.target}", "target": dep.target}
        for dep in task.dependencies
        if dep.kind is DependencyKind.BLOCKS and dep.target not in closed
    ]
    if not out and task.status is TaskState.FAILED:
        out.append({"reason": "échec de la tâche, remédiation requise", "target": task.id})
    if not out and task.status is TaskState.BLOCKED:
        out.append({"reason": "blocage déclaré sans dépendance nommée", "target": task.id})
    if not out and task.status is TaskState.WAITING:
        out.append({"reason": _waiting_reason(task), "target": task.id})
    if not out and task.status is TaskState.UNKNOWN:
        # Le board range l'état inconnu en « blocked » : sans motif, le
        # vérificateur rejette la carte (board.blocker_reason_missing) et chaque
        # geste du service qui projette le board échoue.
        raw = task.raw_status or "?"
        out.append({"reason": f"état inconnu de cette version ({raw}) : mettre grimoire à jour", "target": task.id})
    return out


def _task_entry(task: MissionTask, closed: frozenset[str] = frozenset()) -> dict[str, Any]:
    status = board_status_of(task.status)
    owner = task.owner or (task.claim.actor_id if task.claim else "")
    entry: dict[str, Any] = {
        "task_id": task.id,
        "title": task.title,
        "status": status,
        # `task.priority` porte une priorité de board importée telle quelle
        # (ADR-007) ; une tâche créée directement dans le ledger n'en a pas et
        # retombe sur la dérivation historique depuis `risk_profile`.
        "priority": priority_of(task),
        "owner": owner,
        # Même logique pour les rôles : des rôles de board importés priment sur
        # la dérivation à une seule valeur depuis `type`.
        "agent_roles": list(task.agent_roles) if task.agent_roles else [task.type.value],
        "acceptance_criteria": list(task.acceptance),
        "verifiability": verifiability_as_dict(task),
        "blockers": _blockers(task, closed),
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
    if task.finition:
        entry["finition"] = task.finition
    # Consignes de l'orchestrateur (issue #638) : le board ne porte que le
    # compte de ce qui n'a pas encore été lu — le texte reste au ledger, que
    # `task show` et l'espace Exécuter lisent directement.
    if task.directives:
        entry["directives_pending"] = len(task.pending_directives)
        entry["directives_unacknowledged"] = len(task.unacknowledged_directives)
    # Une référence de remédiation explicite (ADR-007) prime toujours ; à
    # défaut, une carte bloquée en reçoit une par convention.
    if task.remediation_ref:
        entry["remediation_ref"] = task.remediation_ref
    elif status == "blocked":
        entry["remediation_ref"] = "_grimoire/standard/remediation-plan.yaml"
    # Ce que ce schéma ne modélise pas encore (ADR-007) : reporté tel quel,
    # sans jamais écraser une clé déjà posée ci-dessus par la projection.
    for key, value in task.extra.items():
        entry.setdefault(key, value)
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
    # Ce qui attend la décision d'un humain passe en tête (K6) ; puis, dans chaque colonne, la plus
    # pressante d'abord (issue #638) ; à priorité égale, l'identifiant garde l'ordre stable qu'un diff attend.
    all_tasks = ledger.list_tasks()
    closed = frozenset(t.id for t in all_tasks if t.status is TaskState.CLOSED)
    tasks = sorted(
        (t for t in all_tasks if mission_id is None or t.mission_id == mission_id),
        key=lambda t: (
            attention_rank(t), BOARD_LIFECYCLE.index(board_status_of(t.status)), -priority_rank(priority_of(t)), t.id
        ),
    )
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
        "tasks": [_task_entry(task, closed) for task in tasks],
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


def write_board(dest: Path, board: dict[str, Any]) -> None:
    """Écrit une projection là où on la demande, en YAML lisible.

    Seul point d'écriture du board : ``task board export`` et chaque transition
    du :class:`~grimoire.missions.service.TaskService` passent par ici.
    """
    import io

    from ruamel.yaml import YAML

    yaml = YAML()
    yaml.default_flow_style = False
    yaml.width = 120
    stream = io.StringIO()
    yaml.dump(board, stream)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(stream.getvalue(), encoding="utf-8")
