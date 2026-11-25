"""Ce qui est une leçon, ce qui n'en est pas une — et le ménage de ce qui n'en est pas (levier 6).

Mesure sur un projet réel : 854 souvenirs, dont une quinzaine de vraies leçons.
Le reste : des fiches de clôture qui recopiaient le ledger (« Tâche X clôturée :
<titre>. Critères : … »), des entrées laissées par des tests ou des fixtures, et
des doublons exacts. Le rappel de tâche les servait au ``SessionStart`` : des
tokens dépensés pour relire ce que le Mission Ledger sait déjà.

Ce module tient les deux usages d'un même jugement :

* :func:`is_lesson` — le filtre du rappel : seul ce qui ne se déduit pas du
  ledger (une décision, un piège, la raison d'un blocage ou d'un abandon) est
  servi ;
* :func:`plan_prune` / :func:`apply_prune` — ``grimoire memory prune`` : liste
  les fiches recopiées, les entrées de fixtures et les doublons exacts, puis,
  sur demande, les **retire** (geste ``retract`` : plus servies, jamais
  supprimées, réversibles par ``memory unretract``). Un retiré n'est plus relu
  par le plan suivant : la commande est idempotente.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from grimoire.memory.backends.base import MemoryEntry

__all__ = [
    "DUPLICATE",
    "FIXTURE",
    "LEDGER_COPY",
    "LESSON_TAG",
    "PruneCandidate",
    "PrunePlan",
    "apply_prune",
    "is_fixture",
    "is_ledger_copy",
    "is_lesson",
    "plan_prune",
    "prune_reason",
]

#: Étiquette posée par la clôture sur ce qu'elle écrit : une leçon nommée comme telle.
LESSON_TAG = "lesson"

LEDGER_COPY = "ledger_copy"
FIXTURE = "fixture"
DUPLICATE = "duplicate"

#: La forme exacte de l'ancienne fiche de clôture (``recall._closure_summary`` avant le levier 6).
_CLOSURE_COPY_RE = re.compile(r"^\s*Tâche \S+ clôturée : ")
_FIXTURE_TAGS = frozenset({"test", "tests", "fixture", "fixtures", "pytest"})
_FIXTURE_AUTHORS = frozenset({"test", "tests", "fixture", "pytest"})
_FIXTURE_PATH_MARKS = ("pytest-of-", "/pytest-")


def is_ledger_copy(entry: MemoryEntry) -> bool:
    """Une fiche de clôture automatique : titre et critères recopiés du ledger, rien d'autre."""
    return bool(_CLOSURE_COPY_RE.match(entry.text or ""))


def _path_values(metadata: dict[str, Any]) -> Iterable[str]:
    for key in ("source_uri", "source_path", "path"):
        value = metadata.get(key)
        if isinstance(value, str) and value:
            yield value


def is_fixture(entry: MemoryEntry) -> bool:
    """Une entrée écrite par un test ou une fixture, jamais par le travail du projet."""
    metadata = entry.metadata or {}
    if metadata.get("fixture") is True:
        return True
    if {t.lower() for t in entry.tags} & _FIXTURE_TAGS:
        return True
    author = str(metadata.get("agent") or entry.user_id or "").strip().lower()
    if author in _FIXTURE_AUTHORS:
        return True
    for value in _path_values(metadata):
        if value.startswith("tests/") or any(mark in value for mark in _FIXTURE_PATH_MARKS):
            return True
    return False


def is_lesson(entry: MemoryEntry) -> bool:
    """Ce que le rappel peut servir : ni une recopie du ledger, ni un reste de test."""
    return not is_ledger_copy(entry) and not is_fixture(entry)


def _memory_type(entry: MemoryEntry) -> str:
    metadata = entry.metadata or {}
    return str(metadata.get("type") or metadata.get("memory_type") or "")


def _normalized(text: str) -> str:
    return " ".join((text or "").split())


@dataclass(frozen=True, slots=True)
class PruneCandidate:
    """Un souvenir à retirer, et pourquoi."""

    id: str
    kind: str
    text: str
    duplicate_of: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = {"id": self.id, "kind": self.kind, "text": self.text[:160]}
        if self.duplicate_of:
            payload["duplicate_of"] = self.duplicate_of
        return payload


@dataclass(slots=True)
class PrunePlan:
    """Ce que le ménage retirerait : compte avant, candidats, compte après."""

    before: int
    candidates: list[PruneCandidate] = field(default_factory=list)
    retracted: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    applied: bool = False

    @property
    def after(self) -> int:
        """Souvenirs encore servis : après application si elle a eu lieu, sinon prévus."""
        removed = len(self.retracted) if self.applied else len(self.candidates)
        return self.before - removed

    def counts(self) -> dict[str, int]:
        found = {LEDGER_COPY: 0, FIXTURE: 0, DUPLICATE: 0}
        for candidate in self.candidates:
            found[candidate.kind] += 1
        return found

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "before": self.before,
            "after": self.after,
            "counts": self.counts(),
            "candidates": [c.to_dict() for c in self.candidates],
            "retracted": list(self.retracted),
            "skipped": dict(self.skipped),
        }


def prune_reason(candidate: PruneCandidate) -> str:
    """La raison écrite dans la marque de retrait — lisible par ``memory show``."""
    if candidate.kind == LEDGER_COPY:
        return "memory prune : fiche de clôture recopiée du ledger, aucune leçon"
    if candidate.kind == FIXTURE:
        return "memory prune : entrée issue d'un test ou d'une fixture"
    return f"memory prune : doublon exact de {candidate.duplicate_of}"


def plan_prune(entries: Iterable[MemoryEntry]) -> PrunePlan:
    """Classe les souvenirs **servis** en candidats au retrait ; n'écrit rien.

    Ordre de jugement : recopie du ledger, puis fixture, puis doublon exact
    (même type, même texte aux blancs près) — le plus ancien est gardé, les
    suivants sont candidats. Une entrée ne compte qu'une fois.
    """
    served = list(entries)
    plan = PrunePlan(before=len(served))
    kept_by_key: dict[tuple[str, str], MemoryEntry] = {}
    for entry in sorted(served, key=lambda e: (e.created_at or "", e.id)):
        if is_ledger_copy(entry):
            plan.candidates.append(PruneCandidate(entry.id, LEDGER_COPY, entry.text))
            continue
        if is_fixture(entry):
            plan.candidates.append(PruneCandidate(entry.id, FIXTURE, entry.text))
            continue
        key = (_memory_type(entry), _normalized(entry.text))
        first = kept_by_key.get(key)
        if first is None:
            kept_by_key[key] = entry
            continue
        plan.candidates.append(PruneCandidate(entry.id, DUPLICATE, entry.text, duplicate_of=first.id))
    return plan


def apply_prune(manager: Any, plan: PrunePlan) -> PrunePlan:
    """Retire chaque candidat du plan par le geste ``retract`` ; un refus est noté, jamais fatal.

    Le geste garde ses propres refus (souvenir d'un autre projet, backend qui
    ne sait pas modifier) : le candidat refusé reste servi et la raison du
    refus est rendue dans ``skipped``.
    """
    for candidate in plan.candidates:
        try:
            done = manager.retract(candidate.id, reason=prune_reason(candidate), actor="memory-prune")
        except Exception as exc:  # un refus du geste ne casse pas le ménage des autres
            plan.skipped[candidate.id] = str(exc)
            continue
        if done is None:
            plan.skipped[candidate.id] = "introuvable"
            continue
        plan.retracted.append(candidate.id)
    plan.applied = True
    return plan
