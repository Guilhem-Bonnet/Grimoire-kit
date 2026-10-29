"""P90 (P2) — rendre sans attendre le bail une tâche tenue dont la session sondée est terminée.

Hors de ``ledger.py`` (ratchet R2 de taille) : le geste reprend celui de la récolte
(:meth:`MissionLedger.reap`) mais ne juge que le fait « session terminée ».
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from grimoire.missions.lease import MAX_ATTEMPTS, LeaseFile, backoff_delay, lease_path, write_lease
from grimoire.missions.schemas import TaskState

if TYPE_CHECKING:
    from grimoire.missions.ledger import MissionLedger, ReapDecision

__all__ = ["release_ended"]


def release_ended(
    ledger: MissionLedger, task_id: str, lease_epoch: int, *, actor_id: str = "tick", now: datetime | None = None
) -> ReapDecision | None:
    """Rend ``ready`` la tâche tenue à *lease_epoch* dont la session est terminée, sans attendre la fin du bail.

    Même geste que la récolte (barrière d'epoch, ``lease_lost`` avec tentative et ``retry_at``), motif
    ``session_ended``. Rien n'est écrit — ``None`` — si la tâche n'est plus tenue à cet epoch, si l'horloge est
    incohérente ou si les tentatives sont épuisées : la récolte ordinaire jugera à l'expiration du bail, avec
    ses gardes (quota, frein d'hôte, travail local non poussé).
    """
    from grimoire.missions.ledger import LEASE_LOST_KEY, ORIGIN_REAP, ReapDecision, _ledger_file_lock

    with _ledger_file_lock(ledger._events_path):
        ledger._invalidate()
        ledger._load()
        moment = now or ledger._now()
        task = ledger._tasks.get(task_id)
        if (
            task is None
            or task.status not in (TaskState.CLAIMED, TaskState.RUNNING)
            or task.claim is None
            or task.claim.lease_epoch != lease_epoch
            or ledger.clock_state(moment) == "unknown"
        ):
            return None
        attempt = ledger.attempts(task_id) + 1
        if attempt > MAX_ATTEMPTS:
            return None
        retry_at = (moment + backoff_delay(attempt)).isoformat()
        fence = ledger._epoch_high.get(task_id, lease_epoch) + 1
        write_lease(
            lease_path(ledger._root, task_id),
            LeaseFile(task_id, fence, moment.isoformat(), holder=False, origin=ORIGIN_REAP),
        )
        lost = {"epoch": lease_epoch, "reason": "session_ended", "attempt": attempt, "retry_at": retry_at}
        ledger._transition_locked(
            task_id,
            TaskState.READY,
            actor_id,
            "session terminée sans rendre la tâche",
            None,
            {LEASE_LOST_KEY: lost, "fence_epoch": fence},
            None,
            reaper=True,
        )
        return ReapDecision(task_id, "ready", "session_ended", lease_epoch, attempt, retry_at, written=True)
