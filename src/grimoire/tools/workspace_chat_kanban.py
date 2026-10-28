"""Le repli du chat d'orchestrateur sans interface externe (ACP3) : le kanban comme canal.

Copilot dans VS Code ne se pilote pas de l'extérieur. Le chat écrit alors **une consigne sur
une tâche** (canal humain, ``task comment --kind directive``) — la session qui la tient la lit au
tour suivant — et le fil affiche **ce que les sessions déposent** sur cette tâche : leurs notes
de progression, leurs questions, leurs commentaires. C'est en différé, mais ça marche partout.

Rien n'est mémorisé à part : le fil se relit du ledger (les consignes posées par ce chat portent
l'acteur :data:`CHAT_ACTOR`), il survit donc au redémarrage du cockpit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from grimoire.missions.schemas import ORIGIN_HUMAN
from grimoire.missions.service import TaskService

__all__ = ["CHAT_ACTOR", "pick_task", "post", "thread"]

#: L'acteur sous lequel le chat pose ses consignes — ce qui les distingue des autres au ledger.
CHAT_ACTOR = "cockpit-chat"

#: Les tâches qu'une session tient : seules elles peuvent lire une consigne.
_LIVE = ("claimed", "running", "waiting", "needs_verification")

#: Événements par lesquels une session répond sur une tâche.
_REPLY_EVENTS = ("task.progress_noted", "task.question_asked", "task.directive_added")


def pick_task(service: TaskService, task_id: str | None) -> str:
    """La tâche visée : celle qu'on nomme, sinon l'unique tâche tenue par une session."""
    if task_id:
        service.require(task_id)
        return task_id
    live = [t.id for t in service.list_tasks() if t.status.value in _LIVE]
    if len(live) == 1:
        return live[0]
    if not live:
        raise ValueError(
            "aucune tâche n'est tenue par une session : réclamez-en une, ou nommez la tâche visée (task_id)"
        )
    raise ValueError(f"plusieurs tâches sont tenues ({', '.join(live)}) : nommez la tâche visée (task_id)")


def post(root: Path, text: str, task_id: str | None = None) -> dict[str, str]:
    """Dépose *text* comme consigne humaine sur la tâche ; rend la tâche et l'identifiant de la consigne."""
    service = TaskService(root)
    target = pick_task(service, task_id)
    note = service.comment(target, text, CHAT_ACTOR, "directive", origin=ORIGIN_HUMAN)
    directive = note.directive
    return {"task_id": target, "directive_id": directive.id if directive is not None else ""}


def _reply_text(event_type: str, payload: dict[str, Any]) -> str:
    if event_type == "task.progress_noted":
        return str(payload.get("note") or "")
    if event_type == "task.question_asked":
        question = payload.get("question")
        return str(question.get("text") or "") if isinstance(question, dict) else ""
    directive = payload.get("directive")
    return str(directive.get("text") or "") if isinstance(directive, dict) else ""


def thread(root: Path) -> list[dict[str, Any]]:
    """Le fil du chat, du plus ancien au plus récent : nos consignes et ce que les sessions y ont répondu.

    Chaque entrée : ``id`` (stable), ``role`` (``user`` | ``agent``), ``task_id``, ``text``, ``at``, ``actor``.
    Pour chaque tâche où le chat a posé une consigne, les réponses sont les événements des sessions
    postérieurs à la première de ces consignes (le commentaire d'une session, jamais celui du chat).
    """
    service = TaskService(root)
    entries: list[dict[str, Any]] = []
    for task in service.list_tasks():
        ours = [d for d in task.directives if d.author == CHAT_ACTOR]
        if not ours:
            continue
        for directive in ours:
            entries.append(
                {
                    "id": directive.id,
                    "role": "user",
                    "task_id": task.id,
                    "text": directive.text,
                    "at": directive.created_at,
                    "actor": CHAT_ACTOR,
                }
            )
        since = min(d.created_at for d in ours)
        for event in service.ledger.list_events(task.id):
            if event.event_type not in _REPLY_EVENTS or event.created_at < since:
                continue
            if event.event_type == "task.directive_added" and event.actor_id == CHAT_ACTOR:
                continue
            text = _reply_text(event.event_type, event.payload).strip()
            if text:
                entries.append(
                    {
                        "id": event.id,
                        "role": "agent",
                        "task_id": task.id,
                        "text": text,
                        "at": event.created_at,
                        "actor": event.actor_id,
                    }
                )
    entries.sort(key=lambda e: e["at"])  # tri stable : à date égale, l'ordre d'écriture
    return entries
