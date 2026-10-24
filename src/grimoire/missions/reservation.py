"""Réservation de fichiers exclusifs (W1-07) : une seule règle, deux appelants.

``TaskService`` la lit avant le gate pour dire *pourquoi* (verdict structuré, trace) ;
le ledger la rappelle *sous son verrou d'écriture*, par ``claim_guard`` (K10/K11), pour
qu'entre le contrôle et l'écriture aucun autre claim ne s'intercale. Sans ce second
passage, deux claims jugés sur le même état périmé réservaient le même fichier.

Fonctions sans état ni écriture.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from grimoire.missions.schemas import MissionTask, TaskClaim, TaskState
from grimoire.missions.waiting import answer_hint

__all__ = ["ReservationConflict", "find_conflict", "reservation_refusal"]

_HOLDING = (TaskState.CLAIMED, TaskState.RUNNING, TaskState.WAITING)


@dataclass(frozen=True, slots=True)
class ReservationConflict:
    """Le fichier demandé, la tâche qui le tient, et ce qu'on en dit à l'appelant."""

    file: str
    holder: MissionTask
    reason: str
    remedy: str


def find_conflict(tasks: Mapping[str, MissionTask], task: MissionTask, claim: TaskClaim) -> ReservationConflict | None:
    """Le premier conflit entre les fichiers exclusifs de *claim* et ceux d'une autre tâche tenue.

    Une autre tâche CLAIMED, RUNNING ou WAITING dont le claim n'a pas expiré bloque. Une
    tâche en attente (K6) garde ses fichiers : elle reprend sur place, son bail n'est plus
    jugé. Un claim expiré ne bloque plus personne : le temps a suffi, sans geste humain.
    """
    if not claim.exclusive_files:
        return None
    requested = set(claim.exclusive_files)
    for other in tasks.values():
        if other.id == task.id or other.status not in _HOLDING:
            continue
        held = other.claim
        parked = other.status is TaskState.WAITING
        expired = held.is_expired() if held is not None and not parked else False
        if held is None or not held.exclusive_files or expired:
            continue
        overlap = requested & set(held.exclusive_files)
        if not overlap:
            continue
        if parked:
            waiting = other.waiting
            due = waiting.until if waiting is not None else ""
            reason = (
                f"réservé par {other.id} ({held.actor_id}), en attente"
                + (f" ({waiting.reason})" if waiting is not None else "")
                + (f" jusqu'à {due}" if due and due != "unknown" else "")
            )
            asked = waiting.question.id if waiting is not None and waiting.question is not None else ""
            remedy = (
                f"répondre à sa question ({answer_hint(other.id, asked)}) ou attendre son réveil "
                "(`grimoire task wake`), ou réclamer une autre tâche"
            )
        else:
            reason = f"réservé par {other.id} ({held.actor_id}) jusqu'à {held.expires_at or 'sans expiration'}"
            remedy = "attendre l'expiration du claim ou réclamer une autre tâche"
        return ReservationConflict(min(overlap), other, reason, remedy)
    return None


def reservation_refusal(tasks: Mapping[str, MissionTask], task: MissionTask, claim: TaskClaim | None) -> str:
    """Le motif de refus pour ``claim_guard`` ; ``""`` si aucun fichier n'est en conflit."""
    if claim is None:
        return ""
    conflict = find_conflict(tasks, task, claim)
    return "" if conflict is None else f"fichier {conflict.file} {conflict.reason} — {conflict.remedy}"
