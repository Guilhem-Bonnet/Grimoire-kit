"""Ce que l'orchestrateur humain a dit à la session depuis le cockpit (issue #638, lot B).

Deux faits que le Mission Ledger porte et qu'aucun hook ne relisait :

- les **consignes** posées sur la tâche active (``TaskService.comment``) —
  injectées une fois, au ``UserPromptSubmit`` ou au ``SessionStart`` suivant,
  puis marquées livrées par un événement du ledger ;
- l'**annulation** de la tâche que la session tenait — dite en clair, avec sa
  raison, au tour suivant, une fois par session.

Pas une décision à part entière : un helper que :mod:`.task_context` et
:mod:`.activation` appellent, importé à l'usage. Il ne lit que le ledger
(:class:`~grimoire.missions.ledger.MissionLedger`, sans le service ni ses
gates) : le chemin du hook reste léger, et rien n'est importé tant que le
projet n'a pas d'``events.jsonl``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.core.standard_state import ACTOR_ENV, LEDGER_RELPATH

if TYPE_CHECKING:
    from grimoire.missions.ledger import MissionLedger
    from grimoire.missions.schemas import MissionTask

__all__ = ["steering_context"]

#: Le préfixe que la session lit — même famille que « rappel de tâche ».
DIRECTIVE_PREFIX = "[Grimoire — consigne de l'orchestrateur]"
CANCELLED_PREFIX = "[Grimoire — tâche annulée]"
#: Événement d'observabilité du ledger : « cette session a été prévenue de
#: l'annulation ». Ne change pas l'état de la tâche ; empêche de le redire.
_NOTIFIED_EVENT = "task.cancellation_notified"


def steering_context(project_root: Path, task_id: str, session_id: str = "") -> tuple[str, dict[str, Any]]:
    """Le texte à ajouter au contexte de la session, et ce qui a été livré.

    Best-effort comme tout le contexte de hook : un ledger absent ou illisible
    rend ``("", {})``, jamais une session cassée.
    """
    root = project_root.resolve()
    if not (root / LEDGER_RELPATH / "events.jsonl").is_file():
        return "", {}
    try:
        from grimoire.missions.ledger import MissionLedger

        ledger = MissionLedger(root / LEDGER_RELPATH)
        parts: list[str] = []
        detail: dict[str, Any] = {}
        delivered = _deliver_directives(ledger, task_id)
        if delivered:
            parts.append(delivered[0])
            detail["directives_delivered"] = delivered[1]
        cancelled = _announce_cancellations(ledger, session_id)
        if cancelled:
            parts.append(cancelled[0])
            detail["cancelled_announced"] = cancelled[1]
        return "\n".join(parts), detail
    except Exception:
        return "", {}


def _deliver_directives(ledger: MissionLedger, task_id: str) -> tuple[str, list[str]] | None:
    task = ledger.get_task(task_id)
    if task is None:
        return None
    pending = task.pending_directives
    if not pending:
        return None
    ledger.mark_directives_delivered(task_id, tuple(d.id for d in pending), actor_id="hook")
    lines = [f"{DIRECTIVE_PREFIX} {d.author}, {d.created_at[:16]} : {d.text}" for d in pending]
    first = pending[0].id
    lines.append(
        f"Accuse réception quand tu l'as prise en compte : `grimoire task ack {task_id} {first}` "
        f'(ou l\'outil MCP `task_update` avec action="ack", directive_id="{first}").'
    )
    return "\n".join(lines), [d.id for d in pending]


def _announce_cancellations(ledger: MissionLedger, session_id: str) -> tuple[str, list[str]] | None:
    """Les tâches annulées qu'une session tenait, jamais encore dites à cette session.

    Trois signaux, du plus sûr au plus faible, pour savoir si *cette* session
    tenait la tâche : l'identifiant de session porté par le claim (lot A de
    l'issue #638, lu s'il existe), l'acteur du claim contre ``GRIMOIRE_ACTOR``,
    et à défaut la première session qui passe — une fois, pour que l'annulation
    soit dite quelque part plutôt que nulle part.
    """
    from grimoire.missions.schemas import TaskState

    actor_env = os.environ.get(ACTOR_ENV, "").strip()
    notified = {
        (str(e.payload.get("task_id", "")), str(e.payload.get("session_id", "")))
        for e in ledger.list_events()
        if e.event_type == _NOTIFIED_EVENT
    }
    to_announce: list[MissionTask] = []
    for task in ledger.list_tasks():
        if task.status is not TaskState.CANCELLED or task.claim is None:
            continue
        claim_session = str(getattr(task.claim, "session_id", "") or "")
        if claim_session:
            if claim_session != session_id:
                continue
        elif actor_env:
            if task.claim.actor_id != actor_env:
                continue
        elif any(tid == task.id for tid, _ in notified):
            continue
        if (task.id, session_id) in notified:
            continue
        to_announce.append(task)
    if not to_announce:
        return None
    lines: list[str] = []
    for task in to_announce:
        cancel = next(
            (
                e
                for e in reversed(ledger.list_events(task.id))
                if e.event_type == "task.transitioned" and e.payload.get("to_state") == TaskState.CANCELLED.value
            ),
            None,
        )
        by = str(cancel.payload.get("cancelled_by") or cancel.actor_id) if cancel else "inconnu"
        at = cancel.created_at[:16] if cancel else ""
        reason = str(cancel.payload.get("reason", "")) if cancel else ""
        lines.append(
            f"{CANCELLED_PREFIX} {task.id} ({task.title}) a été annulée par {by}"
            + (f" le {at}" if at else "")
            + (f" : {reason}" if reason else "")
            + ". Ne poursuis pas ce chantier ; aucun gate de preuve ne sera réclamé pour elle."
        )
        ledger.append_event(_NOTIFIED_EVENT, task.id, "task", "hook", {"task_id": task.id, "session_id": session_id})
    return "\n".join(lines), [t.id for t in to_announce]
