"""``UserPromptSubmit`` : la ligne de vue de soi (K5, issue #770).

Une session tenant une tâche lit, à chaque tour, où en est son propre bail :
epoch, dernier battement, vivacité calculée, dernière progression. Si le bail a
été repris (le fichier de bail porte un epoch plus récent que son claim, ou
n'a plus de titulaire), la ligne le dit : l'effet d'une session périmée n'est
pas fencé partout, la prévenir est la seule parade côté ancienne session.

Best-effort comme tout le contexte de hook : un ledger absent ou illisible
rend ``""``, jamais une session cassée. Ne lit que le ledger et le fichier de bail.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from grimoire.core.standard_state import LEDGER_RELPATH

__all__ = ["self_view_context"]


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
