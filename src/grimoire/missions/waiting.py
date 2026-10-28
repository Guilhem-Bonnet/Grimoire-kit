"""L'état ``waiting`` d'une tâche : une attente nommée, jamais un blocage muet (K6, issue #771).

Avant ce lot, une tâche qui attendait quelqu'un restait ``running`` (le bail
finissait par périr) ou passait ``blocked`` avec une raison libre ; la question
posée à l'humain n'existait que comme un texte dans le fil de discussion, et
rien ne la faisait remonter. Ici l'attente est un état du ledger :

- ``reason`` ∈ ``{human, ci, quota, paused}`` (D1 : un seul état, quatre raisons) ;
- ``until`` : une date ISO, ``unknown`` (on ne sait pas, un sondeur le dira) ou
  rien (on attend un humain : aucune échéance ne le décide à sa place) ;
- ``question`` : ce qu'on demande à l'humain — obligatoire pour ``human``, absente
  des autres raisons ;
- ``resume_hint`` : de quoi reprendre la session qui attendait.

Ce module ne dépend ni du ledger ni des schémas : ce sont des valeurs et des
fonctions pures, que ``schemas`` (la tâche porte son attente), ``ledger`` (les
transitions) et ``service`` (les gestes) importent.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

__all__ = [
    "ABANDON",
    "HUMAN_ANSWERED_EVENT",
    "PROGRESS_NOTED_EVENT",
    "QUESTION_ASKED_EVENT",
    "QUESTION_KINDS",
    "QUESTION_NOTIFIED_EVENT",
    "UNTIL_UNKNOWN",
    "WAIT_REASONS",
    "DecisionLint",
    "Question",
    "ResumeHint",
    "Waiting",
    "answer_command",
    "answer_hint",
    "attention_rank",
    "lint_decision_text",
    "new_question_id",
    "parse_until",
]

#: Les quatre raisons d'attendre (D1). ``resource`` n'en est pas une : le mode jeu est hors produit.
WAIT_REASONS: tuple[str, ...] = ("human", "ci", "quota", "paused")
#: Ce qu'une question demande à l'humain (§2.1).
QUESTION_KINDS: tuple[str, ...] = (
    "merge", "permission", "choice", "resume_branch", "resume_unfenced", "retry_exhausted",
)
#: ``until`` quand on sait qu'on attend mais pas jusqu'à quand.
UNTIL_UNKNOWN = "unknown"
#: La seule option de réponse qui n'est pas une reprise : elle annule la tâche.
ABANDON = "abandonner"

#: Une question posée à l'humain : la tâche passe ``waiting(human)``.
QUESTION_ASKED_EVENT = "task.question_asked"
#: Une réponse tirée du canal humain (``origin=human``) ; remet le compteur de tentatives à zéro.
HUMAN_ANSWERED_EVENT = "task.human_answered"
#: Une note de progression de l'agent — information, jamais décision.
PROGRESS_NOTED_EVENT = "task.progress_noted"
#: La commande de notification a été lancée pour cette question (au plus une fois par question).
QUESTION_NOTIFIED_EVENT = "task.question_notified"


def answer_command(task_id: str, question_id: str = "") -> str:
    """La commande humaine qui répond à une question, exacte et exécutable telle quelle.

    La CLI attend la réponse en argument positionnel et la question en option :
    ``task answer <T> "<réponse>" --question <q-id>`` (sans ``--question``, la seule question ouverte).
    """
    base = f'grimoire task answer {task_id} "<réponse>"'
    return f"{base} --question {question_id}" if question_id else base


#: Où lancer ces commandes : le binaire du projet, pas un ``grimoire`` d'un autre venv ou d'un autre pipx.
PROJECT_BINARY_HINT = "à lancer avec le binaire du projet, `.venv/bin/grimoire` si `grimoire` n'est pas dans le PATH"


def answer_hint(task_id: str, question_id: str = "") -> str:
    """``answer_command`` entre accents graves, suivi de la consigne sur le binaire à utiliser."""
    return f"`{answer_command(task_id, question_id)}`, {PROJECT_BINARY_HINT}"


def new_question_id() -> str:
    return f"q-{uuid.uuid4().hex[:12]}"


def parse_until(value: str) -> datetime | None:
    """L'échéance ISO 8601 de *value* (UTC si naïve) ; ``None`` pour ``unknown``, le vide ou un texte illisible."""
    text = (value or "").strip()
    if not text or text == UNTIL_UNKNOWN:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Question:
    """Ce qu'on demande à l'humain, et qui le demande (``origin`` : ``agent``, ``tick`` ou ``flow``).

    W6-07 (issue #564) : ``blocking`` (une question non bloquante ne gare pas la tâche), ``why``
    (pourquoi elle est posée — ce que le reçu de la tâche montre) et ``key`` (identité stable de la
    question, pour qu'une reprise ne la repose pas). Écrits seulement s'ils diffèrent du défaut : une
    ligne de ledger antérieure garde sa forme et se relit à l'identique.
    """

    id: str
    kind: str
    text: str
    options: tuple[str, ...] = ()
    origin: str = "agent"
    asked_at: str = ""
    blocking: bool = True
    why: str = ""
    key: str = ""
    #: L'option que l'auteur de la question recommande (P82) : le pilote automatique la prend, sinon il tranche lui-même.
    recommended: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id, "kind": self.kind, "text": self.text,
            "options": list(self.options), "origin": self.origin,
        }
        if self.asked_at:
            data["asked_at"] = self.asked_at
        if not self.blocking:
            data["blocking"] = False
        if self.why:
            data["why"] = self.why
        if self.key:
            data["key"] = self.key
        if self.recommended:
            data["recommended"] = self.recommended
        return data

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Question:
        return cls(
            id=str(d.get("id", "") or ""),
            kind=str(d.get("kind", "") or ""),
            text=str(d.get("text", "") or ""),
            options=tuple(str(o) for o in d.get("options") or () if isinstance(o, str)),
            origin=str(d.get("origin", "agent") or "agent"),
            asked_at=str(d.get("asked_at", "") or ""),
            blocking=d.get("blocking") is not False,
            why=str(d.get("why", "") or ""),
            key=str(d.get("key", "") or ""),
            recommended=str(d.get("recommended", "") or ""),
        )


@dataclass(frozen=True, slots=True)
class ResumeHint:
    """De quoi reprendre la session qui attendait — des indices, jamais une preuve."""

    session_id: str = ""
    host: str = ""
    branch: str = ""
    observed_head: str = ""
    resume_command: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id, "host": self.host, "branch": self.branch,
            "observed_head": self.observed_head, "resume_command": self.resume_command,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ResumeHint:
        return cls(
            session_id=str(d.get("session_id", "") or ""),
            host=str(d.get("host", "") or ""),
            branch=str(d.get("branch", "") or ""),
            observed_head=str(d.get("observed_head", "") or ""),
            resume_command=str(d.get("resume_command", "") or ""),
        )


@dataclass(frozen=True, slots=True)
class Waiting:
    """L'attente d'une tâche (§2.1). Lue sans exception d'un ledger plus récent : voir :meth:`problem`."""

    reason: str
    until: str = ""
    question: Question | None = None
    resume_hint: ResumeHint | None = None
    since: str = ""
    #: W6-07 (issue #564) : les autres questions posées **dans le même lot** que ``question`` — une seule
    #: interruption, une seule notification. La tâche n'est rendue que quand toutes les bloquantes du lot
    #: ont leur réponse humaine.
    batch: tuple[Question, ...] = ()

    @property
    def group(self) -> tuple[Question, ...]:
        """Toutes les questions du lot, celle de tête d'abord ; ``()`` sans question."""
        return () if self.question is None else (self.question, *self.batch)

    def problem(self) -> str:
        """Pourquoi cette attente ne s'écrit pas ; ``""`` si elle est valide.

        Séparé du constructeur : un lecteur qui rencontre une attente écrite par
        une version plus récente (raison inconnue) la garde telle quelle au lieu
        de rendre le ledger illisible ; seuls les écrivains appellent ceci.
        """
        if self.reason not in WAIT_REASONS:
            return f"raison d'attente inconnue : {self.reason!r} — parmi {', '.join(WAIT_REASONS)}"
        if self.reason == "human":
            if self.question is None or not self.question.text.strip() or not self.question.id:
                return "waiting(human) exige une question (id et texte) : une décision attendue n'est jamais implicite"
            if self.question.kind not in QUESTION_KINDS:
                return f"nature de question inconnue : {self.question.kind!r} — parmi {', '.join(QUESTION_KINDS)}"
            if self.until:
                return "waiting(human) n'a pas d'échéance : aucune sortie au minuteur, jamais de décision à la place de l'humain"
            if not self.question.blocking:
                return "la question de tête d'une attente humaine est bloquante : une question non bloquante ne gare pas la tâche"
            return _batch_problem(self.group)
        if self.question is not None or self.batch:
            return f"waiting({self.reason}) ne porte pas de question : seule l'attente d'un humain en pose une"
        if self.until and self.until != UNTIL_UNKNOWN and parse_until(self.until) is None:
            return f"échéance illisible : {self.until!r} — une date ISO 8601, ou 'unknown'"
        return ""

    @property
    def is_human(self) -> bool:
        return self.reason == "human"

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"reason": self.reason}
        if self.until:
            data["until"] = self.until
        if self.question is not None:
            data["question"] = self.question.to_dict()
        if self.resume_hint is not None:
            data["resume_hint"] = self.resume_hint.to_dict()
        if self.since:
            data["since"] = self.since
        if self.batch:
            data["batch"] = [q.to_dict() for q in self.batch]
        return data

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Waiting:
        question = d.get("question")
        hint = d.get("resume_hint")
        return cls(
            reason=str(d.get("reason", "") or ""),
            until=str(d.get("until", "") or ""),
            question=Question.from_dict(question) if isinstance(question, dict) else None,
            resume_hint=ResumeHint.from_dict(hint) if isinstance(hint, dict) else None,
            since=str(d.get("since", "") or ""),
            batch=tuple(Question.from_dict(q) for q in d.get("batch") or () if isinstance(q, dict)),
        )


def _batch_problem(group: tuple[Question, ...]) -> str:
    """Pourquoi ce lot de questions ne s'écrit pas ; ``""`` s'il est valide (identifiants uniques, textes, natures)."""
    seen: set[str] = set()
    for question in group:
        if not question.id or not question.text.strip():
            return "chaque question du lot exige un identifiant et un texte"
        if question.kind not in QUESTION_KINDS:
            return f"nature de question inconnue : {question.kind!r} — parmi {', '.join(QUESTION_KINDS)}"
        if question.id in seen:
            return f"question {question.id} en double dans le lot"
        seen.add(question.id)
    return ""


# ── lint-decisions : une décision implicite dans un commentaire ──────────────

#: Les mots qui annoncent une décision à prendre (§5.3). Heuristique : un indicateur, pas une preuve.
_DECISION_WORDS = re.compile(r"\b(?:fusion\w*|merge\w*|permission\w*|autoris\w*|valid\w*)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class DecisionLint:
    """Un texte qui ressemble à une décision demandée sans que `task ask` l'ait posée."""

    task_id: str
    source: str
    text: str
    at: str

    def to_dict(self) -> dict[str, str]:
        return {"task_id": self.task_id, "source": self.source, "text": self.text, "at": self.at}


def lint_decision_text(text: str) -> bool:
    """``True`` si *text* contient un mot de décision *et* un point d'interrogation."""
    return "?" in text and _DECISION_WORDS.search(text) is not None


def attention_rank(task: Any) -> int:
    """Rang de tri d'attention : ``0`` pour ce qui attend la décision d'un humain, ``1`` pour le reste.

    Une tâche ``waiting(human)`` passe en tête du board, du cockpit et de ``task list`` : c'est la
    seule chose du tableau que personne d'autre qu'un humain ne peut faire avancer.
    """
    waiting = getattr(task, "waiting", None)
    status = getattr(getattr(task, "status", None), "value", "")
    return 0 if status == "waiting" and waiting is not None and waiting.is_human else 1
