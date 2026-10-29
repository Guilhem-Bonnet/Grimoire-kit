"""Les rounds d'une question : chaque fois qu'une même tâche redemande (P92, cahier §6).

Tout vient du ledger, rien n'est déduit : la demande (``task.question_asked``), la réponse donnée
(``task.human_answered``) et la suite — le premier événement de la tâche après la réponse, tel qu'il a
été écrit (une transition avec sa raison, un lien de PR, une nouvelle question). Sans événement,
``aftermath`` reste vide.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from grimoire.missions.waiting import HUMAN_ANSWERED_EVENT, QUESTION_ASKED_EVENT, Question

if TYPE_CHECKING:
    from grimoire.missions.ledger import MissionLedger
    from grimoire.missions.schemas import LedgerEvent

__all__ = ["next_round", "rounds_of"]

#: Les événements qui racontent une suite ; le bruit (bail, directives, notification) n'en fait pas partie.
_AFTERMATH_EVENTS = frozenset({"task.transitioned", "task.linked", QUESTION_ASKED_EVENT})


def _same_thread(asked: Question, other: dict[str, Any]) -> bool:
    """Même fil : même ``key`` quand il y en a une, sinon même ``kind`` (cahier §6)."""
    if asked.key:
        return str(other.get("key", "") or "") == asked.key
    return not other.get("key") and str(other.get("kind", "") or "") == asked.kind


def _thread(events: list[LedgerEvent], question: Question) -> list[LedgerEvent]:
    return [
        e
        for e in events
        if e.event_type == QUESTION_ASKED_EVENT
        and isinstance(e.payload.get("question"), dict)
        and _same_thread(question, e.payload["question"])
    ]


def next_round(ledger: MissionLedger, task_id: str, question: Question) -> tuple[int, str]:
    """``(round, supersedes)`` de *question* si elle est posée maintenant sur *task_id*."""
    earlier = _thread(ledger.list_events(task_id), question)
    if not earlier:
        return 1, ""
    last = earlier[-1].payload["question"]
    return len(earlier) + 1, str(last.get("id", "") or "")


def _answer_label(event: LedgerEvent, question: dict[str, Any]) -> str:
    """L'étiquette de la réponse : le libellé du choix si la valeur en est un, sinon le texte envoyé."""
    data = event.payload.get("answer_data")
    values = data.get("choices") if isinstance(data, dict) else None
    labels = {
        str(c.get("value")): str(c.get("label") or c.get("value"))
        for c in question.get("choices") or ()
        if isinstance(c, dict)
    }
    if isinstance(values, list) and values:
        return ", ".join(labels.get(str(v), str(v)) for v in values)
    answer = str(event.payload.get("answer", "") or "")
    return labels.get(answer, answer)


def _aftermath(event: LedgerEvent) -> str:
    payload = event.payload
    if event.event_type == "task.transitioned":
        reason = str(payload.get("reason", "") or "")
        move = f"{payload.get('from_state', '?')} → {payload.get('to_state', '?')}"
        return f"{move} : {reason}" if reason else move
    if event.event_type == QUESTION_ASKED_EVENT:
        question = payload.get("question")
        text = question.get("title") or question.get("text") if isinstance(question, dict) else ""
        return f"nouvelle question : {text}" if text else "nouvelle question"
    return str(payload.get("reason", "") or event.event_type)


def rounds_of(ledger: MissionLedger, task_id: str, question: Question) -> list[dict[str, Any]]:
    """Les rounds de la tâche pour le fil de *question*, du plus ancien au plus récent.

    ``{round, question_id, asked_at, answered_at, answer_label, aftermath}`` ; la réponse et la suite
    sont vides tant qu'elles n'existent pas au ledger.
    """
    events = ledger.list_events(task_id)
    rounds: list[dict[str, Any]] = []
    for number, asked in enumerate(_thread(events, question), start=1):
        payload = asked.payload["question"]
        qid = str(payload.get("id", "") or "")
        answer = next(
            (e for e in events if e.event_type == HUMAN_ANSWERED_EVENT and e.payload.get("question_id") == qid),
            None,
        )
        follow = ""
        if answer is not None:
            after = events[events.index(answer) + 1 :]
            follow = next((_aftermath(e) for e in after if e.event_type in _AFTERMATH_EVENTS), "")
        rounds.append(
            {
                "round": number,
                "question_id": qid,
                "asked_at": str(payload.get("asked_at") or asked.created_at),
                "answered_at": answer.created_at if answer is not None else "",
                "answer_label": _answer_label(answer, payload) if answer is not None else "",
                "aftermath": follow,
            }
        )
    return rounds
