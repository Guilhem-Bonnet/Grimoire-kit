"""Les gestes d'écriture des questions structurées (W6-07, issue #564) : poser un lot, déduire une réponse.

Séparés de :mod:`grimoire.missions.questions` (lu par ``schemas`` au rejeu, donc sans dépendance au
service) et de ``service.py`` (plafond de taille) ; :class:`~grimoire.missions.service.TaskService`
les expose par :meth:`ask_batch` et :meth:`resolve_question`. La réponse humaine reste
:meth:`TaskService.answer` (K6) : un seul chemin, une seule règle d'autorité.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions.questions import (
    OPEN,
    QUESTION_RESOLVED_EVENT,
    BatchPlan,
    QuestionSpec,
    plan_batch,
    question_rows,
    records_of,
)
from grimoire.missions.schemas import TaskState
from grimoire.missions.waiting import QUESTION_ASKED_EVENT, Waiting

if TYPE_CHECKING:
    from grimoire.missions.schemas import MissionTask
    from grimoire.missions.service import TaskService

__all__ = ["BatchOutcome", "ask_batch", "resolve_question"]

_TERMINAL = (TaskState.CLOSED, TaskState.CANCELLED)


@dataclass(frozen=True, slots=True)
class BatchOutcome:
    """Ce qu'un lot a écrit : la tâche après coup, et le plan (posées, déduites, réutilisées)."""

    task: MissionTask
    plan: BatchPlan
    previous: TaskState

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task.id,
            "status": self.task.status.value,
            "transition": f"{self.previous.value} → {self.task.status.value}",
            **self.plan.to_dict(),
            "questions": question_rows(self.task),
        }


def ask_batch(
    service: TaskService, task_id: str, specs: list[QuestionSpec], actor: str, *,
    origin: str = "agent", epoch: int | None = None,
) -> BatchOutcome:
    """Pose un lot de questions **en une seule interruption**.

    - une question déjà au registre (même clé) n'est pas reposée (reprise) ;
    - une question non bloquante avec ``answer`` et ``source`` est déduite : tracée, jamais posée ;
    - s'il reste au moins une bloquante neuve, la tâche se gare **une fois** en ``waiting(human)`` avec
      tout le lot (``Waiting.batch``) ; sinon les questions sont seulement inscrites au registre.

    La transition passe d'abord (machine à états, gate, bail) : refusée, rien n'est écrit.
    """
    if not specs:
        raise ValueError("un lot de questions est une liste non vide")
    task = service.require(task_id)
    if task.status in _TERMINAL:
        raise GrimoireMissionError(f"{task_id} est {task.status.value} : on ne lui pose plus de question")
    plan = plan_batch(task, specs, origin=origin)
    previous = task.status
    if plan.blocking:
        head = plan.blocking[0]
        batch = tuple(q for q in plan.asked if q.id != head.id)
        moment = datetime.now(UTC).isoformat()
        waiting = Waiting("human", "", head, service._hint_of(task), moment, batch=batch)
        if problem := waiting.problem():
            raise ValueError(problem)
        note = f"question : {head.text}" if not batch else f"{len(batch) + 1} questions en un lot : {head.text} …"
        service.transition(
            task_id, TaskState.WAITING, actor, note,
            extra_payload={"waiting": waiting.to_dict()}, lease_epoch=service._held_epoch(task, actor, epoch),
        )
    group = plan.blocking[0].id if plan.blocking else ""
    for question in plan.asked:
        service.ledger.append_event(
            QUESTION_ASKED_EVENT, task_id, "task", actor,
            {"task_id": task_id, "reason": "human" if group else "", "question": question.to_dict(),
             "actor_id": actor, "batch": group},
        )
    for question, spec in plan.deduced:
        service.ledger.append_event(
            QUESTION_ASKED_EVENT, task_id, "task", actor,
            {"task_id": task_id, "reason": "", "question": question.to_dict(), "actor_id": actor},
        )
        service.ledger.append_event(
            QUESTION_RESOLVED_EVENT, task_id, "task", actor,
            {"task_id": task_id, "question_id": question.id, "answer": spec.answer.strip(),
             "source": spec.source.strip(), "origin": "agent", "actor_id": actor},
        )
    return BatchOutcome(service.require(task_id), plan, previous)


def resolve_question(
    service: TaskService, task_id: str, question_id: str, answer: str, source: str, actor: str
) -> MissionTask:
    """Déduit la réponse d'une question **non bloquante** encore ouverte, source citée.

    Une bloquante est refusée : seul le canal humain la tranche (:meth:`TaskService.answer`). La
    réponse déduite n'est jamais présentée comme humaine (``answer_origin: agent``).
    """
    if not answer.strip():
        raise ValueError("`answer` requis : une réponse vide ne tranche rien")
    if not source.strip():
        raise ValueError("`source` requise : une réponse déduite cite d'où elle vient (fichier, mémoire, commande)")
    task = service.require(task_id)
    record = next((r for r in records_of(task) if r.id == question_id), None)
    if record is None:
        raise GrimoireMissionError(f"Question {question_id} inconnue pour {task_id} (`grimoire task questions {task_id}`)")
    if record.question.blocking:
        raise GrimoireMissionError(
            f"Question {question_id} bloquante : seul un humain la tranche (cockpit ou `grimoire task answer`), "
            "jamais une déduction de l'agent"
        )
    if record.base_status != OPEN:
        raise GrimoireMissionError(f"Question {question_id} déjà tranchée ({record.base_status})")
    service.ledger.append_event(
        QUESTION_RESOLVED_EVENT, task_id, "task", actor,
        {"task_id": task_id, "question_id": question_id, "answer": answer.strip(), "source": source.strip(),
         "origin": "agent", "actor_id": actor},
    )
    return service.require(task_id)
