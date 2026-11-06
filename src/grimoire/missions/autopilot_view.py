"""Le pilote automatique vu du cockpit (P83) : ce qui reste à l'humain, le journal des décisions, « Revenir ».

Lecture et gestes de relecture seulement : l'activation reste dans :class:`~grimoire.missions.autopilot.AutopilotStore`.

- :func:`partition` — pilote activé, « À traiter » ne garde que ce que le pilote ne tranchera pas : la liste fermée, les
  questions qu'il a laissées en confiance basse, et celles qu'il ne peut pas prendre (hors du périmètre du mandat). Chacune
  porte sa raison. Une question que l'orchestrateur répondra au prochain tick sort de la liste et du compteur.
- :func:`journal` — les :data:`~grimoire.missions.autopilot.DECIDED_EVENT` du ledger, du plus récent au plus ancien,
  avec ce que « Revenir » ferait.
- :func:`revert` — « Revenir » quand le geste l'est : une tâche abandonnée par le pilote est réouverte, une tâche rendue
  prête (réponse pas encore reprise par un worker) est bloquée pour que l'humain tranche ; sinon refus, rien d'écrit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions.autopilot import (
    DECIDED_EVENT,
    AutopilotStore,
    last_deferral,
    load_autopilot_policy,
    reserved_reason,
)
from grimoire.missions.schemas import TaskState

if TYPE_CHECKING:
    from grimoire.missions.decisions import PendingDecision
    from grimoire.missions.service import TaskService

__all__ = ["REVERTED_EVENT", "Held", "journal", "partition", "revert"]

REVERTED_EVENT = "autopilot.reverted"

#: Pourquoi une question reste à l'humain pilote activé.
RESERVED_KIND = "reserved"
LOW_CONFIDENCE_KIND = "low_confidence"
OUT_OF_MANDATE_KIND = "out_of_mandate"

_OUT_OF_MANDATE = "le pilote ne peut pas la trancher : hors du périmètre du mandat"


@dataclass(frozen=True, slots=True)
class Held:
    """Une décision en attente qui reste à l'humain, avec sa raison."""

    decision: PendingDecision
    kind: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "reason": self.reason}


def partition(service: TaskService) -> tuple[bool, list[Held]]:
    """``(pilote actif, ce qui reste à l'humain)``. Pilote coupé : ``(False, [])`` — l'appelant garde tout."""
    from grimoire.missions.decisions import pending_decisions
    from grimoire.missions.mandate import MandateStore
    from grimoire.missions.orchestrator_session import eligible_questions

    root = service.project_root
    store = MandateStore(root)
    if not AutopilotStore(root, store.journal).state().active:
        return False, []
    eligible = {(q.task_id, q.question_id) for q in eligible_questions(service, store)}
    policy = load_autopilot_policy(root)
    held: list[Held] = []
    for decision in pending_decisions(service.ledger):
        questions = decision.questions or (decision.question,)
        if all((decision.task_id, q.id) in eligible for q in questions):
            continue
        task = service.ledger.get_task(decision.task_id)
        events = service.ledger.events_for(decision.task_id)
        kind, reason = OUT_OF_MANDATE_KIND, _OUT_OF_MANDATE
        for q in questions:
            if task is not None and (why := reserved_reason(q, task, root, policy)):
                kind, reason = RESERVED_KIND, why
                break
            if why := last_deferral(events, q.id):
                kind, reason = LOW_CONFIDENCE_KIND, why
                break
        held.append(Held(decision, kind, reason))
    return True, held


# ── Journal ──────────────────────────────────────────────────────────────────


def _how_to_revert(service: TaskService, task_id: str) -> str:
    """Ce que « Revenir » ferait maintenant (``""`` : le geste n'est plus réversible)."""
    task = service.ledger.get_task(task_id)
    if task is None:
        return ""
    if task.status is TaskState.FAILED:
        return "reopen"
    if task.status is TaskState.READY:
        return "hold"
    return ""


def journal(service: TaskService, limit: int = 200) -> list[dict[str, Any]]:
    """Les décisions du pilote, la plus récente d'abord ; chacune dit si elle a été reprise et si elle se défait."""
    events = service.ledger.list_events()
    reverted = {
        (e.payload.get("task_id"), e.payload.get("question_id")): e.created_at
        for e in events
        if e.event_type == REVERTED_EVENT
    }
    out: list[dict[str, Any]] = []
    for event in reversed(events):
        if event.event_type != DECIDED_EVENT:
            continue
        p = event.payload
        key = (p.get("task_id"), p.get("question_id"))
        task = service.ledger.get_task(str(p.get("task_id") or ""))
        undo = "" if key in reverted else _how_to_revert(service, str(p.get("task_id") or ""))
        out.append(
            {
                "id": event.id,
                "ts": event.created_at,
                "task_id": p.get("task_id", ""),
                "title": task.title if task is not None else "",
                "question_id": p.get("question_id", ""),
                "question": p.get("question", ""),
                "kind": p.get("kind", ""),
                "option": p.get("option", ""),
                "justification": p.get("justification", ""),
                "confidence": p.get("confidence", ""),
                "source": p.get("recommendation_source", ""),
                "recommended": p.get("recommended", ""),
                "reverted_at": reverted.get(key, ""),
                "revert": undo,
            }
        )
        if len(out) >= limit:
            break
    return out


def revert(service: TaskService, task_id: str, question_id: str, actor: str) -> dict[str, Any]:
    """« Revenir » sur la décision du pilote pour *question_id* : rouvre la tâche ou la rend à l'humain.

    Refuse (rien n'est écrit) si aucune décision du pilote ne porte cette question, si elle a déjà été reprise, ou si la
    tâche a déjà avancé (un worker a repris sa réponse : le geste n'est plus réversible).
    """
    decided = [
        e
        for e in service.ledger.events_for(task_id)
        if e.event_type == DECIDED_EVENT and e.payload.get("question_id") == question_id
    ]
    if not decided:
        raise GrimoireMissionError(f"{task_id} : aucune décision du pilote pour la question {question_id}")
    if any(
        e.event_type == REVERTED_EVENT and e.payload.get("question_id") == question_id
        for e in service.ledger.events_for(task_id)
    ):
        raise GrimoireMissionError(f"{task_id} : la décision du pilote pour {question_id} est déjà reprise")
    how = _how_to_revert(service, task_id)
    option = str(decided[-1].payload.get("option") or "")
    if how == "reopen":
        service.transition(task_id, TaskState.READY, actor, f"décision du pilote reprise par l'humain : {option}")
    elif how == "hold":
        service.transition(
            task_id,
            TaskState.BLOCKED,
            actor,
            f"décision du pilote reprise par l'humain : « {option} » est à retrancher",
        )
    else:
        raise GrimoireMissionError(
            f"{task_id} : la décision du pilote n'est plus réversible (la tâche a déjà repris sa réponse)"
        )
    service.ledger.append_event(
        REVERTED_EVENT,
        task_id,
        "task",
        actor,
        {
            "task_id": task_id,
            "question_id": question_id,
            "how": how,
            "option": option,
            "at": datetime.now(UTC).isoformat(),
        },
    )
    return {"task_id": task_id, "question_id": question_id, "how": how}
