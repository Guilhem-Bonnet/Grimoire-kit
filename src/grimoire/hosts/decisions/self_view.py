"""``UserPromptSubmit`` : la ligne de vue de soi (K5, issue #770).

Une session tenant une tâche lit, à chaque tour, où en est son propre bail :
epoch, dernier battement, vivacité calculée, dernière progression. Si le bail a
été repris (le fichier de bail porte un epoch plus récent que son claim, ou
n'a plus de titulaire), la ligne le dit : l'effet d'une session périmée n'est
pas fencé partout, la prévenir est la seule parade côté ancienne session.

Deux chemins, parce que la tâche reprise n'est plus celle de la session : le
statut est alors ``ready`` (récolte) ou le claim est à une autre session, et la
tâche active résolue est ``bootstrap`` ou une autre tâche. :func:`self_view_context`
ne parle que de la tâche tenue ; :func:`takeover_notices` relit le ledger par
*session* — les tâches rattachées à cette session dont un bail perdu a été
récolté depuis — et ne dépend d'aucune tâche active.

Best-effort comme tout le contexte de hook : un ledger absent ou illisible
rend ``""``, jamais une session cassée. Ne lit que le ledger et le fichier de bail.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from grimoire.core.standard_state import LEDGER_RELPATH

__all__ = ["self_view_context", "takeover_notices"]


def self_view_context(project_root: Path, task_id: str, session_id: str = "", *, now: datetime | None = None) -> str:
    root = project_root.resolve()
    if not (root / LEDGER_RELPATH / "events.jsonl").is_file():
        return ""
    try:
        from grimoire.missions.ledger import MissionLedger
        from grimoire.missions.liveness import (
            attention,
            claimed_at,
            liveness,
            self_view_line,
            task_progress,
        )
        from grimoire.missions.schemas import TaskState

        moment = now or datetime.now(UTC)
        ledger = MissionLedger(root / LEDGER_RELPATH)
        task = ledger.get_task(task_id)
        if task is None or task.claim is None or task.status not in (TaskState.CLAIMED, TaskState.RUNNING):
            return ""
        claim = task.claim
        if claim.session_id and session_id and claim.session_id != session_id:
            return ""
        lease = ledger.lease_file(task_id)
        progress = task_progress(ledger, task_id, root)
        # Le tour en cours prouve que la session parle : journal_at = maintenant.
        state = liveness(now=moment, lease=lease, journal_at=moment)
        line = self_view_line(
            task_id=task_id, epoch=claim.lease_epoch, lease=lease, live=state, progress=progress, now=moment
        )
        if lease is not None and (lease.epoch > claim.lease_epoch or not lease.holder):
            line += f" Ta tâche a été reprise (epoch {lease.epoch}) : n'émets plus d'effet distant (push, commentaire, PR)."
        warn = attention(state, progress, now=moment, since_work=claimed_at(ledger, task_id))
        if warn:
            line += f" Attention : {warn.reason}."
        return line
    except Exception:
        return ""


#: Au plus ce nombre d'annonces par tour : le contexte d'un hook reste court.
_MAX_NOTICES = 3
_TERMINAL = ("closed", "cancelled", "failed")


def takeover_notices(project_root: Path, session_id: str) -> str:
    """Annonce, à *session_id*, les tâches dont son bail a été repris — quel que soit leur statut.

    Rejeu du journal, tâche par tâche : une tâche est « reprise » pour la
    session quand elle lui a été rattachée (``task.session_attached``) puis
    qu'une récolte de bail perdu (``task.transitioned`` portant ``lease_lost``)
    est venue après, sans nouveau rattachement de cette session depuis. Un
    rendu volontaire du bail (release, clôture) n'est pas une reprise ; une
    tâche terminée n'est plus annoncée. Les événements refusés au rejeu (epoch
    périmé) ne comptent pas. Best-effort : ``""`` sur tout échec.
    """
    session = session_id.strip()
    root = project_root.resolve()
    if not session or not (root / LEDGER_RELPATH / "events.jsonl").is_file():
        return ""
    try:
        from grimoire.missions.ledger import LEASE_LOST_KEY, SESSION_ATTACHED_EVENT, MissionLedger

        ledger = MissionLedger(root / LEDGER_RELPATH)
        rejected = ledger.rejected_events()
        holder: dict[str, str] = {}
        taken: dict[str, int] = {}
        for evt in ledger.list_events():
            if evt.id in rejected:
                continue
            tid = evt.entity_id
            if evt.event_type == SESSION_ATTACHED_EVENT:
                holder[tid] = str(evt.payload.get("session_id", ""))
                if holder[tid] == session:
                    taken.pop(tid, None)
            elif evt.event_type == "task.transitioned" and LEASE_LOST_KEY in evt.payload:
                if holder.pop(tid, "") == session:
                    taken[tid] = int(evt.payload.get("fence_epoch", 0) or 0)
        lines: list[str] = []
        for tid, fence in taken.items():
            task = ledger.get_task(tid)
            if task is None or task.status.value in _TERMINAL:
                continue
            epoch = max(fence, task.claim.lease_epoch if task.claim else 0)
            lease = ledger.lease_file(tid)
            if lease is not None:
                epoch = max(epoch, lease.epoch)
            lines.append(
                f"[Grimoire] Ta tâche {tid} a été reprise (epoch {epoch}) : son bail est perdu, "
                "n'émets plus d'effet distant (push, commentaire, PR) pour elle."
            )
        return "\n".join(lines[:_MAX_NOTICES])
    except Exception:
        return ""
