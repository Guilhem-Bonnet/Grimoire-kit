"""Les questions d'une tâche comme objets du ledger, posées par lot (W6-07, issue #564).

K6 (#771) a donné à une tâche une question à la fois : ``task ask`` la gare en
``waiting(human)``, la réponse humaine la rend. Ce module étend ce même ledger,
sans second registre :

- **registre** : chaque question posée sur une tâche y reste, avec sa réponse et
  son auteur (``MissionTask.questions``), rejoué depuis les événements que K6
  écrivait déjà (``task.question_asked``, ``task.human_answered``) plus un seul
  nouveau, ``task.question_resolved`` (réponse déduite par l'agent, source citée) ;
- **lot** : plusieurs questions posées en une fois forment **une** attente
  (``Waiting.batch``), donc une interruption et une notification ; la tâche
  n'est rendue que quand toutes les bloquantes du lot ont leur réponse humaine ;
- **bloquante ou non** : une question non bloquante ne gare pas la tâche ; elle
  peut être tranchée par l'humain plus tard, ou déduite par l'agent **avec sa
  source** (jamais une bloquante : seul un humain la tranche) ;
- **reprise** : une question déjà répondue (même ``key``) n'est pas reposée.

Le statut d'une question se lit, il ne se stocke pas : ``open``, ``answered``
(humain), ``deduced`` (agent, source citée) ou ``not_awaited`` — une bloquante
sans réponse que la tâche n'attend pas ou plus (run de flow abandonné, tâche
annulée, checkpoint de flow posé hors attente) : elle n'est pas « répondue », rien
ne le prétend, et un lot qui la repose la pose de nouveau.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from grimoire.missions.waiting import (
    HUMAN_ANSWERED_EVENT,
    QUESTION_ASKED_EVENT,
    QUESTION_KINDS,
    Question,
    Waiting,
    new_question_id,
)

if TYPE_CHECKING:
    from grimoire.missions.schemas import MissionTask

__all__ = [
    "ANSWERED",
    "DEDUCED",
    "LAPSED",
    "OPEN",
    "QUESTION_EVENTS",
    "QUESTION_RESOLVED_EVENT",
    "BatchPlan",
    "QuestionRecord",
    "QuestionSpec",
    "group_settled",
    "open_in_group",
    "parse_specs",
    "plan_batch",
    "question_key",
    "question_rows",
    "questions_summary",
    "records_of",
    "replay_question",
    "status_of",
]

#: Une réponse déduite par l'agent pour une question **non bloquante**, avec sa source citée.
QUESTION_RESOLVED_EVENT = "task.question_resolved"
#: Les événements que le rejeu du ledger reporte dans ``MissionTask.questions``.
QUESTION_EVENTS: frozenset[str] = frozenset({QUESTION_ASKED_EVENT, HUMAN_ANSWERED_EVENT, QUESTION_RESOLVED_EVENT})

OPEN, ANSWERED, DEDUCED, LAPSED = "open", "answered", "deduced", "not_awaited"
#: L'origine du canal humain (``schemas.ORIGIN_HUMAN``, non importé : ``schemas`` importe ce module).
_HUMAN = "human"
_AGENT = "agent"


def question_key(text: str) -> str:
    """Identité stable d'une question : son texte, casse et blancs normalisés, haché.

    Deux formulations identiques à la casse et aux espaces près sont la même question ; une
    reformulation n'en est pas une (on préfère reposer une question que croire y avoir répondu).
    """
    normalized = re.sub(r"\s+", " ", text).strip().casefold()
    return "k-" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class QuestionRecord:
    """Une question posée sur une tâche, et ce qu'on y a répondu — qui, par quel canal, sur quelle source."""

    question: Question
    answer: str = ""
    answered_at: str = ""
    answered_by: str = ""
    #: ``human`` (canal humain : cockpit, CLI interactive) ou ``agent`` (déduite, ``source`` citée).
    answer_origin: str = ""
    source: str = ""
    #: L'identifiant de la question de tête du lot qui a garé la tâche ; ``""`` hors lot.
    batch: str = ""

    @property
    def id(self) -> str:
        return self.question.id

    @property
    def key(self) -> str:
        return self.question.key or question_key(self.question.text)

    @property
    def base_status(self) -> str:
        if self.answer_origin == _HUMAN:
            return ANSWERED
        if self.answer_origin == _AGENT and self.source:
            return DEDUCED
        return OPEN

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"question": self.question.to_dict()}
        for name in ("answer", "answered_at", "answered_by", "answer_origin", "source", "batch"):
            value = getattr(self, name)
            if value:
                data[name] = value
        return data

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> QuestionRecord:
        raw = d.get("question")
        return cls(
            question=Question.from_dict(raw if isinstance(raw, dict) else {}),
            **{name: str(d.get(name, "") or "") for name in (
                "answer", "answered_at", "answered_by", "answer_origin", "source", "batch",
            )},
        )


# ── rejeu ────────────────────────────────────────────────────────────────────


def replay_question(data: dict[str, Any], evt_type: str, payload: dict[str, Any], at: str) -> None:
    """Reporte un événement de question dans ``data["questions"]`` (la tâche en dictionnaire).

    Append-only : une question n'est jamais réécrite ; la première réponse valide l'emporte. Une
    réponse dont l'origine n'est pas humaine ne compte pas (même règle que ``wake_due``) ; une réponse
    déduite sur une question bloquante, ou sans source, non plus.
    """
    records: list[dict[str, Any]] = [dict(r) for r in data.get("questions") or () if isinstance(r, dict)]
    ids = [str((r.get("question") or {}).get("id", "")) for r in records]
    qid = str(payload.get("question_id", "") or "")
    if evt_type == QUESTION_ASKED_EVENT:
        raw = payload.get("question")
        if not isinstance(raw, dict) or not raw.get("id") or raw.get("id") in ids:
            return
        record: dict[str, Any] = {"question": dict(raw)}
        if payload.get("batch"):
            record["batch"] = str(payload["batch"])
        records.append(record)
    elif evt_type in (HUMAN_ANSWERED_EVENT, QUESTION_RESOLVED_EVENT):
        human = evt_type == HUMAN_ANSWERED_EVENT
        if human and payload.get("origin") != _HUMAN:
            return
        if qid not in ids:
            # Une question posée sans ``task.question_asked`` (la récolte, K2) : celle que la tâche attend.
            waiting = Waiting.from_dict(data["waiting"]) if isinstance(data.get("waiting"), dict) else None
            known = {q.id: q for q in (waiting.group if waiting else ())}
            if qid not in known:
                return
            records.append({"question": known[qid].to_dict()})
            ids.append(qid)
        index = ids.index(qid)
        current = QuestionRecord.from_dict(records[index])
        if current.base_status != OPEN:
            return
        source = str(payload.get("source", "") or "")
        if not human and (current.question.blocking or not source.strip()):
            return
        records[index] = {
            **records[index],
            "answer": str(payload.get("answer", "") or ""),
            "answered_at": at,
            "answered_by": str(payload.get("actor_id", "") or ""),
            "answer_origin": _HUMAN if human else _AGENT,
            **({} if human else {"source": source}),
        }
    else:
        return
    data["questions"] = records


# ── lectures ─────────────────────────────────────────────────────────────────


def _waiting_group(task: MissionTask) -> tuple[Question, ...]:
    waiting = task.waiting
    if getattr(task.status, "value", "") != "waiting" or waiting is None or not waiting.is_human:
        return ()
    return waiting.group


def records_of(task: MissionTask) -> list[QuestionRecord]:
    """Le registre de la tâche, plus les questions de l'attente en cours qu'aucun événement n'a posées (récolte)."""
    records = list(task.questions)
    known = {r.id for r in records}
    records += [QuestionRecord(q) for q in _waiting_group(task) if q.id not in known]
    return records


def status_of(task: MissionTask, record: QuestionRecord) -> str:
    """``open``, ``answered``, ``deduced`` ou ``not_awaited`` (bloquante sans réponse que la tâche n'attend pas)."""
    status = record.base_status
    if status == OPEN and record.question.blocking and record.id not in {q.id for q in _waiting_group(task)}:
        return LAPSED
    return status


def open_in_group(task: MissionTask) -> list[QuestionRecord]:
    """Les questions du lot en cours qui n'ont pas encore de réponse, dans l'ordre du lot."""
    by_id = {r.id: r for r in records_of(task)}
    return [by_id[q.id] for q in _waiting_group(task) if q.id in by_id and by_id[q.id].base_status == OPEN]


def group_settled(task: MissionTask) -> bool:
    """La tâche attend un humain et toutes les questions bloquantes de son lot ont leur réponse humaine."""
    return bool(_waiting_group(task)) and not any(r.question.blocking for r in open_in_group(task))


def question_rows(task: MissionTask) -> list[dict[str, Any]]:
    """Le registre à plat, statut lu compris — ce que ``task questions`` et l'outil MCP rendent."""
    rows = []
    for record in records_of(task):
        q = record.question
        row: dict[str, Any] = {
            "task_id": task.id, "id": q.id, "key": record.key, "kind": q.kind, "text": q.text,
            "options": list(q.options), "blocking": q.blocking, "why": q.why, "asked_by": q.origin,
            "asked_at": q.asked_at, "batch": record.batch, "status": status_of(task, record),
        }
        if record.answer_origin:
            row.update({
                "answer": record.answer, "answered_by": record.answered_by,
                "answer_origin": record.answer_origin, "answered_at": record.answered_at,
            })
        if record.source:
            row["source"] = record.source
        rows.append(row)
    return rows


def questions_summary(task: MissionTask) -> dict[str, Any]:
    """Le reçu des questions d'une tâche : combien, posées à qui, pourquoi, en combien d'interruptions."""
    rows = question_rows(task)
    count: dict[str, int] = {s: sum(1 for r in rows if r["status"] == s) for s in (OPEN, ANSWERED, DEDUCED, LAPSED)}
    interruptions = {r["batch"] or r["id"] for r in rows if r["status"] != DEDUCED and r["blocking"]}
    return {
        "task_id": task.id,
        "total": len(rows),
        "asked_to_human": sum(1 for r in rows if r["status"] != DEDUCED),
        "blocking": sum(1 for r in rows if r["blocking"]),
        "interruptions": len(interruptions),
        **count,
        "questions": rows,
    }


# ── lot : ce qu'on pose, ce qu'on réutilise, ce qu'on déduit ─────────────────


@dataclass(frozen=True, slots=True)
class QuestionSpec:
    """Une question telle que l'agent la propose, avant qu'elle ait un identifiant."""

    text: str
    kind: str = "choice"
    options: tuple[str, ...] = ()
    blocking: bool = True
    why: str = ""
    key: str = ""
    #: Réponse déduite (non bloquante seulement), avec ``source`` obligatoire : jamais posée à l'humain.
    answer: str = ""
    source: str = ""

    def problem(self) -> str:
        if not self.text.strip():
            return "une question sans texte ne demande rien"
        if self.kind not in QUESTION_KINDS:
            return f"nature de question inconnue : {self.kind!r} — parmi {', '.join(QUESTION_KINDS)}"
        if self.answer.strip() and self.blocking:
            return (
                f"« {self.text.strip()} » est bloquante : une question bloquante ne se déduit pas, seul un humain la "
                "tranche — posez-la, ou déclarez-la non bloquante avec sa source"
            )
        if self.answer.strip() and not self.source.strip():
            return f"« {self.text.strip()} » : une réponse déduite cite sa source (fichier, mémoire, commande)"
        if self.source.strip() and not self.answer.strip():
            return f"« {self.text.strip()} » : une source sans réponse ne déduit rien"
        return ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> QuestionSpec:
        options = d.get("options") or ()
        return cls(
            text=str(d.get("text", "") or ""),
            kind=str(d.get("kind", "choice") or "choice"),
            options=tuple(str(o) for o in options) if isinstance(options, (list, tuple)) else (),
            blocking=d.get("blocking") is not False,
            why=str(d.get("why", "") or ""),
            key=str(d.get("key", "") or ""),
            answer=str(d.get("answer", "") or ""),
            source=str(d.get("source", "") or ""),
        )


def parse_specs(raw: Any) -> list[QuestionSpec]:
    """Un lot lu d'un JSON ou YAML : une liste de questions, ou ``{"questions": [...]}``. Lève ``ValueError``."""
    items = raw.get("questions") if isinstance(raw, dict) else raw
    if not isinstance(items, list) or not items:
        raise ValueError("un lot de questions est une liste non vide (ou {questions: [...]})")
    specs = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(f"question illisible dans le lot : {item!r}")
        specs.append(QuestionSpec.from_dict(item))
    return specs


@dataclass(frozen=True, slots=True)
class BatchPlan:
    """Ce qu'un lot écrira : les questions neuves posées à l'humain, celles déduites, celles déjà connues."""

    asked: tuple[Question, ...] = ()
    deduced: tuple[tuple[Question, QuestionSpec], ...] = ()
    reused: tuple[QuestionRecord, ...] = ()
    blocking: tuple[Question, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "asked": [q.to_dict() for q in self.asked],
            "deduced": [{**q.to_dict(), "answer": s.answer, "source": s.source} for q, s in self.deduced],
            "reused": [r.to_dict() for r in self.reused],
            "parked": bool(self.blocking),
        }


def plan_batch(task: MissionTask, specs: list[QuestionSpec], *, origin: str = "agent") -> BatchPlan:
    """Le plan d'un lot, sans rien écrire. Lève ``ValueError`` sur une question invalide.

    Une question dont la clé est déjà au registre (répondue, déduite, ou encore ouverte) n'est pas
    reposée : c'est ce qui rend une reprise sûre. Une bloquante ``not_awaited`` (jamais répondue) l'est.
    """
    for spec in specs:
        if problem := spec.problem():
            raise ValueError(problem)
    known = {r.key: r for r in records_of(task) if status_of(task, r) != LAPSED}
    moment = datetime.now(UTC).isoformat()
    asked: list[Question] = []
    deduced: list[tuple[Question, QuestionSpec]] = []
    reused: list[QuestionRecord] = []
    seen: set[str] = set()
    for spec in specs:
        key = spec.key.strip() or question_key(spec.text)
        if key in seen:
            continue
        seen.add(key)
        if key in known:
            reused.append(known[key])
            continue
        question = Question(
            new_question_id(), spec.kind, spec.text.strip(), spec.options, origin, moment,
            blocking=spec.blocking, why=spec.why.strip(), key=key,
        )
        if spec.answer.strip():
            deduced.append((question, spec))
        else:
            asked.append(question)
    return BatchPlan(tuple(asked), tuple(deduced), tuple(reused), tuple(q for q in asked if q.blocking))
