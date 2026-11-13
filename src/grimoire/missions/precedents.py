"""Les précédents : une réponse humaine vaut pour toutes les questions de la même classe.

Quand un humain tranche une question (origine ``human``), un précédent est enregistré **pour le projet** : la
classe de la question, l'option choisie, la date et la question d'origine. La classe réunit le rôle qui
demande (``merge``, ``review``, ``task``, ``criterion``…) et la cause normalisée de la question
(``protected_paths``, ``not_ready_cycles``, ``human_validation``, ``paid``…) : ``merge:protected_paths``,
``review:not_ready_cycles``, ``criterion:human_validation``.

Avant de laisser une question à l'humain, le pilote automatique cherche un précédent de la même classe et,
s'il existe et que son option est offerte par la question, l'applique — source ``precedent`` et référence du
précédent dans ``autopilot.decided`` (:func:`find`).

**Échec fermé** — rien ne s'applique si le fichier est illisible (une ligne illisible suffit, et le pilote le
dit), si l'option du précédent n'est pas proposée par la question, si la question relève d'une catégorie
réservée à l'humain (:data:`~grimoire.missions.autopilot.RESERVED`), ou si la politique du projet ne l'a pas
activé. L'application est une nouveauté de politique **désactivée par défaut** : sans
``autopilot.precedents: true`` dans ``orchestration-policy.yaml``, le pilote se comporte comme avant.

Le fichier est un JSONL append-only sous ``_grimoire-runtime-output/precedents.jsonl`` ; ``--forget``
réécrit le fichier sans l'entrée visée (canal humain seulement).
"""

from __future__ import annotations

import contextlib
import json
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions.autopilot import AutopilotPolicy, load_autopilot_policy, reserved_reason

if TYPE_CHECKING:
    from grimoire.missions.schemas import MissionTask
    from grimoire.missions.waiting import Question

__all__ = [
    "PRECEDENTS_RELPATH",
    "Lookup",
    "Precedent",
    "PrecedentError",
    "find",
    "forget",
    "load",
    "note_human_answer",
    "precedents_path",
    "question_class",
    "record",
]

#: Le fichier des précédents, relatif à la racine du projet (comme le ledger : local au clone).
PRECEDENTS_RELPATH = Path("_grimoire-runtime-output") / "precedents.jsonl"
#: Les caractères invisibles d'un texte comparé.
_INVISIBLE = re.compile(r"[​-‏‪-‮⁠-⁤﻿­]")
#: Les identifiants volatils (tâche, question, PR, exigence) ne font pas une classe : ils changent d'une question à l'autre.
_VOLATILE = re.compile(r"\b(?:gao|req|pr|q|t|dir|rev)-[0-9a-z]+\b|\b[0-9a-f]{8,}\b|\b\d+\b", re.IGNORECASE)
_NON_WORD = re.compile(r"[^a-z0-9]+")
#: Les causes canoniques, dans l'ordre : la première reconnue nomme la classe. Un texte qui n'en reconnaît
#: aucune prend un identifiant normalisé de sa cause (``why``, sinon le texte de la question).
_CAUSES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("protected_paths", re.compile(r"protected|protege|chemin[s]?.{0,16}(?:reserve|protege)|reserved? path")),
    ("not_ready_cycles", re.compile(r"not[ _-]?ready|pas pret|ready perime|cycles?\b")),
    ("human_validation", re.compile(r"validation humaine|valide par un humain|human validation")),
    (
        "paid",
        re.compile(
            r"payant|payee?|factur|billing|\bcouts?\b|\bcoute\b|depens|\bcredits?\b|abonnement|subscription|"
            r"\bpaid\b|\bcost\b|budget|tarif|\bprix\b|\bprice\b"
        ),
    ),
    ("outside_repo", re.compile(r"hors du depot|outside (?:the )?repo")),
    ("missing_criterion", re.compile(r"sans critere|aucun critere|missing criterion")),
    ("vague_criterion", re.compile(r"critere vague|vague criterion")),
    ("duplicate", re.compile(r"doublon|duplicate")),
    (
        "red_checks",
        re.compile(
            r"(?:checks?|tests?|\bci\b|pipeline).{0,24}(?:rouge|echec|fail)|(?:rouge|echec|fail).{0,24}(?:checks?|tests?|\bci\b|pipeline)"
        ),
    ),
)
#: Les natures de question que le relecteur écrit lui-même (reprise, tentative épuisée).
_REVIEW_KINDS = frozenset({"resume_branch", "resume_unfenced", "retry_exhausted"})


class PrecedentError(GrimoireMissionError):
    """Le fichier des précédents ne se lit pas (échec fermé : rien ne s'applique)."""


def precedents_path(project_root: Path) -> Path:
    """Le fichier des précédents du projet."""
    return project_root / PRECEDENTS_RELPATH


def _fold(text: object) -> str:
    """Le texte comparé : sans caractères invisibles, sans accents ni casse, espaces réduits."""
    cleaned = _INVISIBLE.sub("", str(text))
    folded = "".join(c for c in unicodedata.normalize("NFKD", cleaned) if not unicodedata.combining(c))
    return " ".join(folded.lower().split())


def _slug(text: object) -> str:
    """Un identifiant stable : sans identifiants volatils, alphanumérique, borné."""
    folded = _VOLATILE.sub(" ", _fold(text))
    slug = _NON_WORD.sub("_", folded).strip("_")
    return slug[:48].strip("_") or "question"


def _domain(question: Question, source: str) -> str:
    """Le rôle qui demande : la source lue au ledger, sinon la nature de la question."""
    key = question.key or ""
    if "critere" in _fold(key):
        return "criterion"
    if question.kind == "merge" or source == "merger":
        return "merge"
    if question.kind in _REVIEW_KINDS or source == "reviewer":
        return "review"
    if question.kind == "permission":
        return "permission"
    if source == "reap":
        return "reap"
    return "task"


def _cause(question: Question) -> str:
    """La cause normalisée : une cause canonique reconnue, sinon un identifiant du ``why`` (ou du texte)."""
    text = _VOLATILE.sub(" ", _fold(" ".join(filter(None, (question.why, question.title, question.text)))))
    for name, pattern in _CAUSES:
        if pattern.search(text):
            return name
    return _slug(question.why or question.text)


def question_class(question: Question, source: str = "") -> str:
    """La classe d'une question : ``<rôle qui demande>:<cause normalisée>`` (``merge:protected_paths``)."""
    return f"{_domain(question, source)}:{_cause(question)}"


@dataclass(frozen=True, slots=True)
class Precedent:
    """Une réponse humaine, retenue pour toutes les questions de la même classe."""

    id: str
    question_class: str
    option: str
    answered_at: str
    question: str
    task_id: str = ""
    question_id: str = ""
    kind: str = ""
    source: str = ""

    def to_dict(self) -> dict[str, str]:
        data = {
            "id": self.id,
            "class": self.question_class,
            "option": self.option,
            "answered_at": self.answered_at,
            "question": self.question,
        }
        for name in ("task_id", "question_id", "kind", "source"):
            if value := getattr(self, name):
                data[name] = value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Precedent:
        ident = str(data.get("id", "") or "").strip()
        klass = str(data.get("class", data.get("question_class", "")) or "").strip()
        option = str(data.get("option", "") or "").strip()
        if not ident or not klass or not option:
            raise ValueError("il manque `id`, `class` ou `option`")
        return cls(
            id=ident,
            question_class=klass,
            option=option,
            answered_at=str(data.get("answered_at", "") or ""),
            question=str(data.get("question", "") or ""),
            task_id=str(data.get("task_id", "") or ""),
            question_id=str(data.get("question_id", "") or ""),
            kind=str(data.get("kind", "") or ""),
            source=str(data.get("source", "") or ""),
        )


def load(project_root: Path) -> tuple[Precedent, ...]:
    """Les précédents du projet, du plus ancien au plus récent. Lève :class:`PrecedentError` si le fichier ne se lit pas."""
    path = precedents_path(project_root)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ()
    except OSError as exc:
        raise PrecedentError(f"précédents illisibles ({path.name}) : {type(exc).__name__}") from exc
    entries: list[Precedent] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PrecedentError(f"précédents illisibles ({path.name}, ligne {number}) : {exc.msg}") from exc
        if not isinstance(raw, dict):
            raise PrecedentError(f"précédents illisibles ({path.name}, ligne {number}) : un objet est attendu")
        try:
            entries.append(Precedent.from_dict(raw))
        except ValueError as exc:
            raise PrecedentError(f"précédents illisibles ({path.name}, ligne {number}) : {exc}") from exc
    return tuple(entries)


def _write(project_root: Path, entries: tuple[Precedent, ...]) -> None:
    """Réécrit le fichier en entier (``--forget``), par un remplacement atomique."""
    path = precedents_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    temporary.write_text(
        "".join(json.dumps(entry.to_dict(), ensure_ascii=False, sort_keys=True) + "\n" for entry in entries),
        encoding="utf-8",
    )
    temporary.replace(path)


def record(
    project_root: Path,
    question: Question,
    answer: str,
    source: str = "",
    *,
    task_id: str = "",
    now: datetime | None = None,
) -> Precedent:
    """Enregistre la réponse humaine à *question* comme précédent du projet (append-only)."""
    option = " ".join(answer.split())
    if not option:
        raise ValueError("une réponse vide ne fait pas un précédent")
    precedent = Precedent(
        id=f"p-{uuid.uuid4().hex[:12]}",
        question_class=question_class(question, source),
        option=option,
        answered_at=(now or datetime.now(UTC)).isoformat(),
        question=" ".join(question.text.split())[:400],
        task_id=task_id,
        question_id=question.id,
        kind=question.kind,
        source=source,
    )
    path = precedents_path(project_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(precedent.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    except OSError as exc:
        raise PrecedentError(f"précédent non enregistré ({path.name}) : {type(exc).__name__}") from exc
    return precedent


def note_human_answer(service: Any, task_id: str, question: Question, answer: str) -> None:
    """Enregistre la réponse humaine de *service* comme précédent (best-effort : elle est déjà au ledger)."""
    from grimoire.missions.orchestrator_session import question_source

    with contextlib.suppress(Exception):  # un précédent non écrit ne défait pas la réponse humaine
        record(
            service.project_root,
            question,
            answer,
            question_source(service.ledger.events_for(task_id), question),
            task_id=task_id,
        )


def forget(project_root: Path, precedent_id: str) -> Precedent:
    """Retire le précédent *precedent_id* ; lève s'il est introuvable ou si le fichier ne se lit pas."""
    entries = load(project_root)
    kept = tuple(entry for entry in entries if entry.id != precedent_id)
    if len(kept) == len(entries):
        raise GrimoireMissionError(f"précédent {precedent_id!r} introuvable")
    _write(project_root, kept)
    return next(entry for entry in entries if entry.id == precedent_id)


def _offered(option: str, question: Question) -> bool:
    """Vrai si l'option du précédent est une réponse possible à la question (texte libre : toujours)."""
    if not question.options:
        return True
    folded = _fold(option)
    return any(folded == _fold(candidate) for candidate in question.options)


@dataclass(frozen=True, slots=True)
class Lookup:
    """Ce que le pilote trouve : le précédent applicable, ou pourquoi aucun ne s'applique.

    *error* : le fichier est illisible — échec fermé, et l'appelant le dit (raison de la question laissée à
    l'humain). *reason* : rien ne s'applique, pour une raison ordinaire (politique coupée, classe réservée,
    aucun précédent de la classe, option non proposée).
    """

    precedent: Precedent | None = None
    reason: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if self.precedent is not None:
            data["precedent"] = self.precedent.to_dict()
        if self.reason:
            data["reason"] = self.reason
        if self.error:
            data["error"] = self.error
        return data


def find(
    project_root: Path,
    question: Question,
    source: str,
    *,
    task: MissionTask,
    policy: AutopilotPolicy | None = None,
) -> Lookup:
    """Le précédent applicable à *question* — échec fermé : politique coupée, catégorie réservée ou fichier illisible.

    Un précédent ne s'applique **jamais** à une catégorie réservée (:func:`~.autopilot.reserved_reason`), ni
    quand son option n'est pas offerte par la question. À classe égale, le plus récent l'emporte.
    """
    policy = load_autopilot_policy(project_root) if policy is None else policy
    if policy.error:
        return Lookup(error=f"politique du pilote invalide ({policy.error})")
    if not policy.precedents:
        return Lookup(reason="précédents désactivés par la politique (`autopilot.precedents`)")
    if why := reserved_reason(question, task, project_root, policy):
        return Lookup(reason=why)
    try:
        entries = load(project_root)
    except PrecedentError as exc:
        return Lookup(error=str(exc))
    klass = question_class(question, source)
    for entry in reversed(entries):
        if entry.question_class != klass:
            continue
        if not _offered(entry.option, question):
            return Lookup(
                reason=f"le précédent {entry.id} ({klass}) propose « {entry.option} », absent des options de la question"
            )
        return Lookup(precedent=entry)
    return Lookup(reason=f"aucun précédent pour la classe {klass}")
