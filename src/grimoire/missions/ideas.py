"""La boîte à idées du projet : garder une idée sans la trier (I13).

Un journal append-only ``<ledger>/ideas.jsonl`` ; l'état d'une idée (``open``,
``promoted``, ``dropped``) se dérive du pli de ses événements, jamais d'une
réécriture. Un « annuler » est un événement de plus (``restore``), pas une
suppression de ligne. L'écriture tient le verrou de fichier du kit
(:func:`grimoire.core.fsio.locked`) : deux processus qui ajoutent ne perdent
aucune ligne.

``promote`` passe par l'intake déterministe (:mod:`grimoire.missions.intake_tasks`),
comme ``grimoire task add --intake --create``.
"""

from __future__ import annotations

import builtins
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.core.fsio import locked
from grimoire.missions.ledger_append import append_bytes

__all__ = ["IDEA_MAX_CHARS", "IDEA_SOURCES", "Idea", "IdeaError", "IdeaStore"]

IDEAS_FILE = "ideas.jsonl"
DEFAULT_LEDGER = Path("_grimoire-runtime-output/ledger")
#: Borne d'une idée : une phrase à garder, pas un brief (le même plafond que le champ du cockpit).
IDEA_MAX_CHARS = 500
IDEA_SOURCES = ("cli", "cockpit", "agent")


class IdeaError(ValueError):
    """Une demande refusée sur la boîte à idées : le message dit pourquoi."""


@dataclass
class Idea:
    id: str
    text: str
    source: str
    created_at: str
    state: str = "open"
    tasks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "source": self.source,
            "created_at": self.created_at,
            "state": self.state,
            "tasks": list(self.tasks),
        }


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class IdeaStore:
    """Lecture et écriture de ``ideas.jsonl`` pour un projet."""

    def __init__(self, project_root: Path, ledger_root: Path = DEFAULT_LEDGER) -> None:
        root = ledger_root if ledger_root.is_absolute() else project_root / ledger_root
        self.path = root / IDEAS_FILE

    # ── lecture ──────────────────────────────────────────────────────────────

    def _events(self) -> builtins.list[dict[str, Any]]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError:
            return []
        events: builtins.list[dict[str, Any]] = []
        for line in raw.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue  # une fin tronquée ne casse pas la boîte
            if isinstance(event, dict):
                events.append(event)
        return events

    @staticmethod
    def _fold(events: builtins.list[dict[str, Any]]) -> dict[str, Idea]:
        ideas: dict[str, Idea] = {}
        for event in events:
            kind, idea_id = event.get("event"), str(event.get("id") or "")
            if kind == "add":
                ideas[idea_id] = Idea(
                    idea_id,
                    str(event.get("text") or ""),
                    str(event.get("source") or "cli"),
                    str(event.get("ts") or ""),
                )
                continue
            idea = ideas.get(idea_id)
            if idea is None:
                continue
            if kind == "drop" and idea.state == "open":
                idea.state = "dropped"
            elif kind == "restore" and idea.state == "dropped":
                idea.state = "open"
            elif kind == "promote" and idea.state != "promoted":
                idea.state = "promoted"
                idea.tasks = [str(t) for t in event.get("tasks") or ()]
        return ideas

    def list(self, state: str = "") -> builtins.list[Idea]:
        """Les idées dans l'ordre d'ajout ; *state* filtre (``open``, ``promoted``, ``dropped``)."""
        return [i for i in self._fold(self._events()).values() if not state or i.state == state]

    def get(self, idea_id: str) -> Idea:
        idea = self._fold(self._events()).get(idea_id)
        if idea is None:
            raise IdeaError(f"idée inconnue : {idea_id}")
        return idea

    # ── écriture ─────────────────────────────────────────────────────────────

    def _append(self, event: dict[str, Any]) -> None:
        append_bytes(self.path, (json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))

    def add(self, text: str, source: str = "cli") -> Idea:
        text = " ".join(text.split())
        if not text:
            raise IdeaError("une idée vide n'est pas gardée")
        if len(text) > IDEA_MAX_CHARS:
            raise IdeaError(f"idée trop longue : {len(text)} caractères, {IDEA_MAX_CHARS} au plus")
        if source not in IDEA_SOURCES:
            raise IdeaError(f"source inconnue : {source} (attendu : {', '.join(IDEA_SOURCES)})")
        with locked(self.path):
            events = self._events()
            number = 1 + sum(1 for e in events if e.get("event") == "add")
            idea_id = f"IDEA-{number:04d}"
            ts = _now()
            self._append({"event": "add", "id": idea_id, "text": text, "source": source, "ts": ts})
        return Idea(idea_id, text, source, ts)

    def _transition(self, idea_id: str, kind: str, allowed: str, why: str, extra: dict[str, Any] | None = None) -> Idea:
        with locked(self.path):
            idea = self._fold(self._events()).get(idea_id)
            if idea is None:
                raise IdeaError(f"idée inconnue : {idea_id}")
            if idea.state != allowed:
                raise IdeaError(f"{idea_id} est {idea.state} : {why}")
            self._append({"event": kind, "id": idea_id, "ts": _now(), **(extra or {})})
        return self.get(idea_id)

    def drop(self, idea_id: str) -> Idea:
        return self._transition(idea_id, "drop", "open", "seule une idée ouverte s'écarte")

    def restore(self, idea_id: str) -> Idea:
        return self._transition(idea_id, "restore", "dropped", "seule une idée écartée se restaure")

    def mark_promoted(self, idea_id: str, task_ids: builtins.list[str]) -> Idea:
        """Marque l'idée ``promoted`` avec les tâches créées (le cockpit crée par l'intake, puis marque)."""
        return self._transition(
            idea_id,
            "promote",
            "open",
            "elle a déjà été promue ou écartée",
            {"tasks": [str(t) for t in task_ids]},
        )

    def promote(
        self, idea_id: str, service: Any, *, actor: str = "cli", origin: str = "cli"
    ) -> tuple[Idea, builtins.list[dict[str, Any]]]:
        """Crée les tâches non critiques que l'intake propose, puis marque l'idée ``promoted``.

        Une seconde promotion est refusée avant toute création. Les propositions critiques
        ne sont jamais créées d'office : elles sont rendues dans ``skipped`` du premier retour.
        """
        from grimoire.missions.intake_tasks import create_from_proposals, proposals_for

        with locked(self.path):
            idea = self._fold(self._events()).get(idea_id)
            if idea is None:
                raise IdeaError(f"idée inconnue : {idea_id}")
            if idea.state != "open":
                raise IdeaError(f"{idea_id} est {idea.state} : seule une idée ouverte se promeut")
            chosen = [p for p in proposals_for(idea.text) if p["checked"]]
            if not chosen:
                raise IdeaError(f"{idea_id} : l'intake ne propose que des tâches critiques, à créer à la main")
            created = create_from_proposals(service, [{**p, "check": ""} for p in chosen], actor=actor, origin=origin)
            self._append({"event": "promote", "id": idea_id, "ts": _now(), "tasks": [c["id"] for c in created]})
        return self.get(idea_id), created
