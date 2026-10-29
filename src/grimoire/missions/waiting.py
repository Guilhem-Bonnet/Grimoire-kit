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
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

__all__ = [
    "ABANDON",
    "ANSWER_MODES",
    "ATTACHMENT_TYPES",
    "HUMAN_ANSWERED_EVENT",
    "PROGRESS_NOTED_EVENT",
    "QUESTION_ASKED_EVENT",
    "QUESTION_KINDS",
    "QUESTION_NOTIFIED_EVENT",
    "UNTIL_UNKNOWN",
    "WAIT_REASONS",
    "Attachment",
    "Choice",
    "DecisionLint",
    "Question",
    "Recommendation",
    "ResumeHint",
    "Waiting",
    "answer_command",
    "answer_hint",
    "attention_rank",
    "clip_context",
    "lint_decision_text",
    "new_question_id",
    "parse_until",
    "question_problem",
    "split_legacy",
]

#: Les quatre raisons d'attendre (D1). ``resource`` n'en est pas une : le mode jeu est hors produit.
WAIT_REASONS: tuple[str, ...] = ("human", "ci", "quota", "paused")
#: Ce qu'une question demande à l'humain (§2.1).
QUESTION_KINDS: tuple[str, ...] = (
    "merge", "permission", "choice", "resume_branch", "resume_unfenced", "retry_exhausted",
)
#: Le contrôle de réponse d'une question v2 (P92, cahier §5.1).
ANSWER_MODES: tuple[str, ...] = ("single", "multi", "text", "single_text")
#: Les types de pièces jointes d'une question v2 (cahier §4).
ATTACHMENT_TYPES: tuple[str, ...] = ("link", "file", "diff", "ci", "image", "command")
MAX_ATTACHMENTS = 6
MAX_CHOICES = 12
MAX_EXCERPT = 4000
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


def _text(d: dict[str, Any], key: str) -> str:
    return str(d.get(key, "") or "")


@dataclass(frozen=True, slots=True)
class Choice:
    """Une option riche : ce qui est envoyé (``value``), ce qui s'affiche (``label``), ce que ça change."""

    value: str
    label: str = ""
    consequence: str = ""
    danger: bool = False
    group: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"value": self.value, "label": self.label or self.value}
        if self.consequence:
            data["consequence"] = self.consequence
        if self.danger:
            data["danger"] = True
        if self.group:
            data["group"] = self.group
        return data

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Choice:
        return cls(
            value=_text(d, "value"),
            label=_text(d, "label"),
            consequence=_text(d, "consequence"),
            danger=d.get("danger") is True,
            group=_text(d, "group"),
        )


@dataclass(frozen=True, slots=True)
class Recommendation:
    """Le choix que l'émetteur recommande, pourquoi, et ce qui se passe ensuite. Jamais écrit par le cockpit."""

    value: str | tuple[str, ...]
    reason: str = ""
    consequence: str = ""

    @property
    def values(self) -> tuple[str, ...]:
        return (self.value,) if isinstance(self.value, str) else tuple(self.value)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"value": self.value if isinstance(self.value, str) else list(self.value)}
        if self.reason:
            data["reason"] = self.reason
        if self.consequence:
            data["consequence"] = self.consequence
        return data

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Recommendation:
        raw = d.get("value", "")
        value: str | tuple[str, ...] = tuple(str(v) for v in raw) if isinstance(raw, (list, tuple)) else str(raw or "")
        return cls(value=value, reason=_text(d, "reason"), consequence=_text(d, "consequence"))


@dataclass(frozen=True, slots=True)
class Attachment:
    """Une pièce qui éclaire la décision. Le contenu est relevé à l'émission : l'affichage n'exécute rien."""

    type: str
    label: str = ""
    primary: bool = False
    ref: str = ""
    excerpt: str = ""
    lines: str = ""
    state: str = ""
    stats: tuple[tuple[str, int], ...] = ()
    rev: str = ""
    fetched_at: str = ""
    alt: str = ""
    mutates: bool = False

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"type": self.type, "label": self.label}
        for key in ("ref", "excerpt", "lines", "state", "rev", "fetched_at", "alt"):
            if value := getattr(self, key):
                data[key] = value
        if self.primary:
            data["primary"] = True
        if self.mutates:
            data["mutates"] = True
        if self.stats:
            data["stats"] = dict(self.stats)
        return data

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Attachment:
        stats = d.get("stats")
        pairs = tuple(
            (str(k), v)
            for k, v in (stats.items() if isinstance(stats, dict) else ())
            if isinstance(v, int) and not isinstance(v, bool)
        )
        return cls(
            type=_text(d, "type"),
            label=_text(d, "label"),
            primary=d.get("primary") is True,
            ref=_text(d, "ref"),
            excerpt=_text(d, "excerpt"),
            lines=_text(d, "lines"),
            state=_text(d, "state"),
            stats=pairs,
            rev=_text(d, "rev"),
            fetched_at=_text(d, "fetched_at"),
            alt=_text(d, "alt"),
            mutates=d.get("mutates") is True,
        )


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
    #: P92 (question v2, cahier §9) : tout est optionnel et écrit seulement s'il diffère du défaut ;
    #: ``text`` et ``options`` restent toujours renseignés (un lecteur v1 affiche la question par eux).
    title: str = ""
    context: str = ""
    choices: tuple[Choice, ...] = ()
    answer_mode: str = ""
    attachments: tuple[Attachment, ...] = ()
    recommendation: Recommendation | None = None
    task_title: str = ""
    round: int = 1
    supersedes: str = ""

    @property
    def is_v2(self) -> bool:
        return bool(
            self.title
            or self.context
            or self.choices
            or self.answer_mode
            or self.attachments
            or self.recommendation
            or self.task_title
            or self.round != 1
            or self.supersedes
        )

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
        for key in ("title", "context", "answer_mode", "task_title", "supersedes"):
            if value := getattr(self, key):
                data[key] = value
        if self.choices:
            data["choices"] = [c.to_dict() for c in self.choices]
        if self.attachments:
            data["attachments"] = [a.to_dict() for a in self.attachments]
        if self.recommendation is not None:
            data["recommendation"] = self.recommendation.to_dict()
        if self.round != 1:
            data["round"] = self.round
        if self.is_v2:
            data["schema"] = 2
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
            title=_text(d, "title"),
            context=_text(d, "context"),
            choices=tuple(Choice.from_dict(c) for c in d.get("choices") or () if isinstance(c, dict)),
            answer_mode=_text(d, "answer_mode"),
            attachments=tuple(Attachment.from_dict(a) for a in d.get("attachments") or () if isinstance(a, dict)),
            recommendation=Recommendation.from_dict(r) if isinstance(r := d.get("recommendation"), dict) else None,
            task_title=_text(d, "task_title"),
            round=_round_of(d.get("round")),
            supersedes=_text(d, "supersedes"),
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


def _round_of(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else 1


def _http_url(ref: str) -> bool:
    parts = urlsplit(ref)
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def clip_context(text: str, limit: int = 220) -> str:
    """Le contexte d'une question tient en deux lignes (cahier §2) : au plus *limit* caractères, coupé proprement."""
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def question_problem(question: Question) -> str:
    """Pourquoi les champs v2 de *question* ne s'écrivent pas ; ``""`` s'ils sont valides (ou absents).

    Appelé à l'écriture seulement : un ledger plus récent reste lisible tel quel.
    """
    if question.answer_mode and question.answer_mode not in ANSWER_MODES:
        return f"mode de réponse inconnu : {question.answer_mode!r} — parmi {', '.join(ANSWER_MODES)}"
    if len(question.choices) > MAX_CHOICES:
        return f"trop de choix : {len(question.choices)} (au plus {MAX_CHOICES})"
    values = [c.value for c in question.choices]
    if any(not v.strip() for v in values):
        return "un choix sans valeur : chaque choix porte la valeur qui sera envoyée"
    if len(set(values)) != len(values):
        return "choix en double : chaque valeur n'apparaît qu'une fois"
    if question.recommendation is not None:
        picked = question.recommendation.values
        if not picked or any(not v for v in picked):
            return "recommandation sans valeur"
        if question.choices and any(v not in values for v in picked):
            return f"la recommandation {question.recommendation.value!r} n'est pas l'un des choix proposés"
    if len(question.attachments) > MAX_ATTACHMENTS:
        return f"trop de pièces : {len(question.attachments)} (au plus {MAX_ATTACHMENTS})"
    for attachment in question.attachments:
        if attachment.type not in ATTACHMENT_TYPES:
            return f"type de pièce inconnu : {attachment.type!r} — parmi {', '.join(ATTACHMENT_TYPES)}"
        if attachment.type == "link" and not _http_url(attachment.ref):
            return f"pièce « link » : l'URL {attachment.ref!r} doit être http ou https"
        if len(attachment.excerpt) > MAX_EXCERPT:
            return f"extrait trop long : {len(attachment.excerpt)} caractères (au plus {MAX_EXCERPT})"
        if attachment.type == "image" and not attachment.alt.strip():
            return "pièce « image » : le texte alternatif est obligatoire"
    return ""


_SENTENCE = re.compile(r"^(.+?\?)(?:\s|$)", re.DOTALL)


def split_legacy(text: str, kind: str = "") -> tuple[str, str]:
    """``(titre, contexte)`` d'une question qui n'a que ``text`` (cahier §9.4).

    Titre = la première phrase finissant par ``?`` si elle tient en 120 caractères, sinon
    « <Nature> : décision attendue » ; contexte = le reste.
    """
    body = (text or "").strip()
    found = _SENTENCE.match(body)
    if found and len(found.group(1)) <= 120:
        return found.group(1).strip(), body[found.end() :].strip()
    return f"{_KIND_LABELS.get(kind, 'Question')} : décision attendue", body


_KIND_LABELS = {
    "merge": "Fusion",
    "permission": "Permission",
    "choice": "Choix",
    "resume_branch": "Reprise",
    "resume_unfenced": "Reprise",
    "retry_exhausted": "Relance",
}


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
        if problem := question_problem(question):
            return problem
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


def answer_data_payload(data: Mapping[str, Any] | None, question: Question) -> dict[str, Any]:
    """``{"answer_data": …}`` pour ``task.human_answered`` (P92, cahier §9.3) ; ``{}`` sans donnée structurée.

    ``{"choices": [value, …], "note": str, "question_id": str}`` — le texte sérialisé reste la réponse
    de référence, ceci s'y ajoute. Refus (``ValueError``) d'un choix qui n'est pas l'un de ceux proposés.
    """
    if not data:
        return {}
    raw = data.get("choices") or ()
    if isinstance(raw, str) or not isinstance(raw, (list, tuple)):
        raise ValueError("`answer_data.choices` : une liste de valeurs")
    picked = [str(v) for v in raw]
    allowed = {c.value for c in question.choices}
    if allowed and (unknown := [v for v in picked if v not in allowed]):
        raise ValueError(f"choix inconnu pour {question.id} : {', '.join(unknown)}")
    clean: dict[str, Any] = {"choices": picked, "question_id": question.id}
    if note := str(data.get("note", "") or "").strip():
        clean["note"] = note
    return {"answer_data": clean}
