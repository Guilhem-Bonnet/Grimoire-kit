"""Le chat d'orchestrateur face au pilote automatique (ACP4).

Trois liens entre le chat (:mod:`grimoire.tools.workspace_chat`) et le pilote de P82
(:mod:`grimoire.missions.autopilot`), tous en lecture sauf la réponse humaine :

- **Questions** — pilote activé, une question qui reste à l'humain (liste réservée, ou confiance basse
  inscrite par ``autopilot.deferred``) est posée dans le chat, en plus de la liste « À traiter ».
  La réponse tapée dans le chat est une **réponse humaine** : :func:`answer` appelle
  ``TaskService.answer`` avec l'origine ``human`` posée ici — le canal humain du cockpit, comme le geste
  « répondre » de ``/tasks/<id>/answer`` — jamais lue de ce que l'agent écrit.
- **Résumé à l'ouverture** — :func:`summary` lit les décisions du pilote (``autopilot.decided``) depuis la
  dernière visite du chat (:func:`mark_visit`) ; la page y met un lien vers le journal « Décidé par le pilote ».
- **Gestes** — l'agent peut *proposer* d'activer ou de couper le pilote en écrivant ``[[pilote:on]]`` ou
  ``[[pilote:off]]`` (:func:`proposals`). Le chat n'en fait qu'un bouton : le geste part de la page, par la route
  ``autonomy/autopilot`` derrière la clé de contrôle. Rien ici n'écrit dans le journal d'autorité.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.tools.workspace_chat_kanban import CHAT_ACTOR

__all__ = ["VISIT_RELPATH", "answer", "mark_visit", "pending_questions", "proposals", "strip_markers", "summary"]

#: Dernière visite du chat (cockpit : fichier d'exécution, comme la session mémorisée).
VISIT_RELPATH = Path("_grimoire-runtime-output") / "cockpit" / "chat-visit.json"

_MARKER = re.compile(r"\[\[pilote:(on|off)\]\]")
_SUMMARY_MAX = 20
_TEXT = 300


def proposals(text: str) -> list[bool]:
    """Les gestes que *text* propose, dans l'ordre : ``True`` active le pilote, ``False`` le coupe."""
    return [m.group(1) == "on" for m in _MARKER.finditer(text)]


def strip_markers(text: str) -> str:
    """*text* sans les marqueurs de geste (le bouton les remplace)."""
    return _MARKER.sub("", text)


def _service(root: Path) -> Any:
    from grimoire.missions.service import TaskService

    return TaskService(root)


def pending_questions(root: Path) -> list[dict[str, Any]]:
    """Les questions ouvertes que le pilote laisse à l'humain — vide si le pilote n'est pas activé.

    Chaque ligne : ``task_id``, ``question_id``, ``text``, ``options``, ``recommended``, ``why`` et ``kept``,
    la raison pour laquelle la question reste à l'humain.
    """
    from grimoire.missions.autopilot import AutopilotStore, last_deferral, reserved_reason
    from grimoire.missions.questions import open_in_group

    if not AutopilotStore(root).state().active:
        return []
    service = _service(root)
    rows: list[dict[str, Any]] = []
    for task in service.list_tasks(status="waiting"):
        for record in open_in_group(task):
            question = record.question
            kept = reserved_reason(question, task, service.project_root) or last_deferral(
                service.ledger.events_for(task.id), question.id
            )
            if not kept:
                continue
            rows.append({
                "task_id": task.id, "task_title": task.title, "question_id": question.id,
                "text": question.text, "options": list(question.options), "recommended": question.recommended,
                "why": question.why, "kept": kept,
            })
    return rows


def answer(root: Path, task_id: str, question_id: str, text: str) -> dict[str, Any]:
    """Tranche *question_id* par le canal humain du cockpit ; refuse une question que le chat n'a pas posée."""
    from grimoire.core.exceptions import GrimoireError
    from grimoire.missions.schemas import ORIGIN_HUMAN

    if not any(r["task_id"] == task_id and r["question_id"] == question_id for r in pending_questions(root)):
        raise ValueError("cette question n'est plus posée dans le chat (déjà tranchée, ou le pilote la décide)")
    try:
        _service(root).answer(task_id, text, CHAT_ACTOR, origin=ORIGIN_HUMAN, question_id=question_id)
    except GrimoireError as exc:
        raise ValueError(str(exc)) from exc
    return {"ok": True, "accepted": True, "mode": "answer", "task_id": task_id, "question_id": question_id}


def _visit_file(root: Path) -> Path:
    return root / VISIT_RELPATH


def last_visit(root: Path) -> str:
    """L'instant de la dernière visite (ISO 8601), ``""`` à la première."""
    try:
        data = json.loads(_visit_file(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    value = data.get("at") if isinstance(data, dict) else None
    return value if isinstance(value, str) else ""


def mark_visit(root: Path, now: datetime | None = None) -> None:
    """Note que le chat vient d'être ouvert ; sans disque, le résumé se redonne, rien de plus."""
    path = _visit_file(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"at": (now or datetime.now(UTC)).isoformat()}), encoding="utf-8")
    except OSError:
        pass


def summary(root: Path) -> dict[str, Any] | None:
    """Les décisions du pilote depuis la dernière visite ; ``None`` si le pilote est coupé ou n'a rien décidé."""
    from grimoire.missions.autopilot import DECIDED_EVENT, AutopilotStore

    if not AutopilotStore(root).state().active:
        return None
    since = last_visit(root)
    service = _service(root)
    decided = [
        e
        for task in service.list_tasks()
        for e in service.ledger.events_for(task.id)
        if e.event_type == DECIDED_EVENT and e.created_at > since
    ]
    if not decided:
        return None
    decided.sort(key=lambda e: e.created_at)
    shown = decided[-_SUMMARY_MAX:]
    return {
        "since": since,
        "count": len(decided),
        "decisions": [
            {
                "task_id": e.payload.get("task_id", e.entity_id),
                "question": str(e.payload.get("question") or "")[:_TEXT],
                "option": str(e.payload.get("option") or "")[:_TEXT],
                "justification": str(e.payload.get("justification") or "")[:_TEXT],
                "confidence": e.payload.get("confidence", ""),
                "at": e.created_at,
            }
            for e in shown
        ],
        "journal": "#journal",
    }
