"""Palace-oriented taxonomy helpers for memory metadata and reporting.

The taxonomy borrows the structural ideas from MemPalace while staying backend
agnostic:

- ``wing``  — major domain scope (project, agent, person)
- ``hall``  — memory corridor / high-level category
- ``room``  — concrete topic or slice within the hall

These helpers normalize metadata so existing Grimoire backends can expose a
stable palace-like structure without changing the storage source of truth.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from grimoire.memory.backends.base import MemoryEntry

__all__ = [
    "DEFAULT_HALL",
    "PalaceDescriptor",
    "build_taxonomy",
    "entry_matches_filters",
    "flatten_taxonomy",
    "normalize_palace_metadata",
    "slugify",
]

_HALL_FALLBACK = "hall_discoveries"
DEFAULT_HALL = _HALL_FALLBACK

_VALID_HALLS = frozenset({
    "hall_facts",
    "hall_events",
    "hall_discoveries",
    "hall_preferences",
    "hall_advice",
})

_HALL_ALIASES = {
    "facts": "hall_facts",
    "fact": "hall_facts",
    "decisions": "hall_facts",
    "decision": "hall_facts",
    "context": "hall_facts",
    "shared-context": "hall_facts",
    "shared_context": "hall_facts",
    "events": "hall_events",
    "event": "hall_events",
    "failures": "hall_events",
    "failure": "hall_events",
    "stories": "hall_events",
    "story": "hall_events",
    "discoveries": "hall_discoveries",
    "discovery": "hall_discoveries",
    "learnings": "hall_discoveries",
    "learning": "hall_discoveries",
    "agent-learnings": "hall_discoveries",
    "agent_learnings": "hall_discoveries",
    "preferences": "hall_preferences",
    "preference": "hall_preferences",
    "advice": "hall_advice",
}

_MEMORY_TYPE_TO_HALL = {
    "shared-context": "hall_facts",
    "shared_context": "hall_facts",
    "decisions": "hall_facts",
    "decision": "hall_facts",
    "agent-learnings": "hall_discoveries",
    "agent_learnings": "hall_discoveries",
    "stories": "hall_events",
    "story": "hall_events",
    "failures": "hall_events",
    "failure": "hall_events",
}


@dataclass(frozen=True, slots=True)
class PalaceDescriptor:
    """Normalized palace location for a memory entry."""

    wing: str
    hall: str
    room: str

    @property
    def palace_key(self) -> str:
        return f"{self.wing}/{self.hall}/{self.room}"


def slugify(value: str, *, default: str = "general") -> str:
    """Convert free text into a stable lowercase slug."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return cleaned or default


def _normalize_hall(value: str) -> str:
    raw = value.strip().lower()
    if not raw:
        return _HALL_FALLBACK
    if raw in _VALID_HALLS:
        return raw
    raw = raw.replace("-", "_")
    if raw in _VALID_HALLS:
        return raw
    if raw.startswith("hall_") and raw in _VALID_HALLS:
        return raw
    return _HALL_ALIASES.get(raw, _HALL_FALLBACK)


def _derive_hall(metadata: dict[str, Any], tags: tuple[str, ...]) -> str:
    hall = str(metadata.get("hall", "")).strip()
    if hall:
        return _normalize_hall(hall)

    memory_type = str(metadata.get("memory_type") or metadata.get("type") or "").strip().lower()
    if memory_type:
        return _MEMORY_TYPE_TO_HALL.get(memory_type, _HALL_FALLBACK)

    for tag in tags:
        hall_alias = _HALL_ALIASES.get(tag.strip().lower())
        if hall_alias:
            return hall_alias

    return _HALL_FALLBACK


def _derive_wing(metadata: dict[str, Any], *, project_name: str, user_id: str) -> str:
    explicit = str(metadata.get("wing", "")).strip()
    if explicit:
        return slugify(explicit)

    agent_name = str(metadata.get("agent_name") or metadata.get("agent") or metadata.get("owner_agent") or "").strip()
    if agent_name:
        return f"agent-{slugify(agent_name)}"

    scope = str(metadata.get("scope", "")).strip().lower()
    if scope == "user" and user_id:
        return f"user-{slugify(user_id)}"

    return f"project-{slugify(project_name)}"


def _derive_room(metadata: dict[str, Any], tags: tuple[str, ...]) -> str:
    for key in ("room", "topic", "memory_type", "type"):
        value = str(metadata.get(key, "")).strip()
        if value:
            return slugify(value)

    if tags:
        return slugify(tags[0])

    return "general"


def normalize_palace_metadata(
    metadata: dict[str, Any] | None,
    *,
    project_name: str,
    user_id: str = "",
    tags: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Return metadata enriched with palace taxonomy fields."""
    normalized = dict(metadata or {})

    descriptor = PalaceDescriptor(
        wing=_derive_wing(normalized, project_name=project_name, user_id=user_id),
        hall=_derive_hall(normalized, tags),
        room=_derive_room(normalized, tags),
    )
    normalized.setdefault("project_name", project_name)
    normalized.setdefault("source_kind", "memory")

    if "memory_type" not in normalized and "type" in normalized:
        normalized["memory_type"] = normalized["type"]

    normalized["wing"] = descriptor.wing
    normalized["hall"] = descriptor.hall
    normalized["room"] = descriptor.room
    normalized["palace_key"] = descriptor.palace_key
    return normalized


def entry_matches_filters(
    entry: MemoryEntry,
    *,
    wing: str = "",
    hall: str = "",
    room: str = "",
) -> bool:
    """Return whether an entry matches the requested palace filters."""
    metadata = entry.metadata or {}
    if wing and metadata.get("wing") != wing:
        return False
    if hall and metadata.get("hall") != hall:
        return False
    return not (room and metadata.get("room") != room)


def build_taxonomy(entries: Iterable[MemoryEntry]) -> dict[str, Any]:
    """Build a nested ``wing -> hall -> room`` count tree."""
    taxonomy: dict[str, Any] = {}
    for entry in entries:
        metadata = entry.metadata or {}
        wing = str(metadata.get("wing", "unknown"))
        hall = str(metadata.get("hall", _HALL_FALLBACK))
        room = str(metadata.get("room", "general"))

        wing_bucket = taxonomy.setdefault(wing, {"total": 0, "halls": {}})
        wing_bucket["total"] += 1
        hall_bucket = wing_bucket["halls"].setdefault(hall, {"total": 0, "rooms": {}})
        hall_bucket["total"] += 1
        hall_bucket["rooms"][room] = hall_bucket["rooms"].get(room, 0) + 1
    return taxonomy


def flatten_taxonomy(taxonomy: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a taxonomy tree into row-oriented data for CLI rendering."""
    rows: list[dict[str, Any]] = []
    for wing, wing_bucket in sorted(taxonomy.items()):
        halls = wing_bucket.get("halls", {})
        for hall, hall_bucket in sorted(halls.items()):
            rooms = hall_bucket.get("rooms", {})
            for room, count in sorted(rooms.items()):
                rows.append({
                    "wing": wing,
                    "hall": hall,
                    "room": room,
                    "count": count,
                    "wing_total": wing_bucket.get("total", 0),
                    "hall_total": hall_bucket.get("total", 0),
                })
    return rows
