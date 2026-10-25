"""Reprendre un claim dont le bail est perdu, sans détour par ``task move --to ready`` (KAN-FRICTIONS).

Constat du 2026-10-19 (Forge, kit 3.63.0) : un claim dont le plafond dur ``expires_at`` était
passé de trois jours restait ``claimed`` ; le réclamer de nouveau, même par la session qui le
tenait, répondait « déjà claimed par claude … jusqu'à <date passée> ». Le seul chemin était de
le rendre à la main (``task move --to ready``, refusé pour un ``running``) puis de le réclamer.

Désormais le claim d'une tâche ``claimed``/``running`` dont le bail est **perdu** (règle de
:func:`grimoire.missions.lease.lost_reason` : ``expires_at``, ``heartbeat``, ``fence_pending``)
se reprend directement. Sous le verrou du journal, comme la récolte : barrière du fichier de bail
à l'epoch suivant (le titulaire périmé est refusé par le ledger et par le frein d'outil), puis
``task.transitioned`` vers ``ready`` marqué ``reclaimed`` ; le claim suit dans le même geste, au
nouvel epoch. Ce n'est pas une tentative comptée (``lease_lost``) : rien n'a échoué.

Refus maintenus :

- un bail vivant, ou une horloge incohérente (rien n'est jugé perdu, comme pour la récolte) ;
- un autre acteur que le titulaire, quand l'hôte du claim n'a pas de frein vérifié
  (:func:`grimoire.hosts.capabilities.claim_host_is_fenced`) : sa session pourrait écrire
  encore, et c'est la question que ``grimoire task reap`` pose à l'humain (``resume_unfenced``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions.lease import LeaseFile, lease_path, read_lease, write_lease
from grimoire.missions.schemas import TaskState

if TYPE_CHECKING:
    from pathlib import Path

    from grimoire.missions.ledger import LeaseView, MissionLedger
    from grimoire.missions.schemas import MissionTask

#: La clé, dans le ``task.transitioned`` vers ``ready``, qui dit qu'un claim perdu a été repris.
RECLAIMED_KEY = "reclaimed"


def _fenced(view: LeaseView, project_root: Path | None = None) -> bool:
    from grimoire.hosts import capabilities

    try:
        return bool(capabilities.claim_host_is_fenced(view.host, view.host_version, project_root=project_root))
    except Exception:
        return False  # table illisible : hôte tenu pour non fencé (fail-closed)


def release_lost_claim(ledger: MissionLedger, task: MissionTask, actor_id: str) -> MissionTask:
    """Rend le claim perdu de *task* (barrière puis ``ready``) pour que *actor_id* le réclame ; refuse sinon.

    Appelée par :meth:`MissionLedger._transition_locked`, verrou du journal tenu, quand un claim vise
    une tâche déjà tenue. Rend la tâche relue (``ready``) ; lève :class:`GrimoireMissionError` avec le
    refus historique (« déjà claimed par … ») si le bail vit, complété du motif sinon.
    """
    held = task.claim
    assert held is not None  # l'appelant ne vient ici qu'avec un claim
    session = f", session {held.session_id}" if held.session_id else ""
    refusal = (
        f"Claim refusé pour {task.id} : déjà {task.status.value} par {held.actor_id}{session} "
        f"jusqu'à {held.expires_at or 'sans expiration'}"
    )
    moment = ledger._now()
    clock = ledger.clock_state(moment)
    view = ledger._lease_view(task, moment, clock)
    if clock == "unknown":
        raise GrimoireMissionError(f"{refusal} — horloge incohérente : aucun bail n'est jugé perdu")
    if view is None or view.status != "lost":
        raise GrimoireMissionError(refusal)
    if held.actor_id != actor_id and not _fenced(view, ledger.project_root):
        raise GrimoireMissionError(
            f"{refusal} — bail perdu ({view.reason}), mais l'hôte {view.host or 'inconnu'} est sans frein vérifié : "
            f"sa session peut écrire encore. `grimoire task reap` pose la question à l'humain ; "
            f"{held.actor_id} peut aussi reprendre son propre claim"
        )
    from grimoire.missions.ledger import ORIGIN_REAP

    path = lease_path(ledger._root, task.id)
    cached = read_lease(path)
    fence = cached.epoch if view.reason == "fence_pending" and cached is not None else (
        ledger._epoch_high.get(task.id, view.epoch) + 1
    )
    write_lease(path, LeaseFile(task.id, fence, moment.isoformat(), holder=False, origin=ORIGIN_REAP))
    reclaimed = {"epoch": view.epoch, "reason": view.reason, "by": actor_id, "holder": held.actor_id}
    ledger._transition_locked(
        task.id, TaskState.READY, actor_id, f"claim perdu ({view.reason}) repris par {actor_id}", None,
        {RECLAIMED_KEY: reclaimed, "fence_epoch": fence}, None, reaper=True,
    )
    return ledger._tasks[task.id]
