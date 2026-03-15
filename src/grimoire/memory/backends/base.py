"""Abstract base for memory backends.

Every backend must implement the seven core methods defined here.
Optional methods (delete, update, store_many) have default implementations
that raise ``NotImplementedError`` — override them for full CRUD support.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class MemoryEntry:
    """A single memory item stored or retrieved by a backend."""

    id: str
    text: str
    user_id: str = "global"
    tags: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "user_id": self.user_id,
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "score": self.score,
        }


@dataclass(frozen=True, slots=True)
class BackendStatus:
    """Health / status report from a backend."""

    backend: str
    healthy: bool
    entries: int
    detail: dict[str, Any] = field(default_factory=dict)


class MemoryBackend(abc.ABC):
    """Abstract base class for all Grimoire memory backends.

    Subclasses must implement store, recall, search, get_all, count,
    health_check, and consolidate.

    Optional methods ``delete``, ``update``, and ``store_many`` provide
    default implementations (``NotImplementedError`` / sequential fallback)
    so that existing subclasses are not broken.
    """

    @abc.abstractmethod
    def store(self, text: str, *, user_id: str = "", tags: tuple[str, ...] = (), metadata: dict[str, Any] | None = None) -> MemoryEntry:
        """Persist a new memory entry."""
        ...

    @abc.abstractmethod
    def recall(self, entry_id: str) -> MemoryEntry | None:
        """Retrieve a specific entry by ID."""
        ...

    @abc.abstractmethod
    def search(self, query: str, *, user_id: str = "", limit: int = 5) -> list[MemoryEntry]:
        """Search memories by keyword or semantic similarity."""
        ...

    @abc.abstractmethod
    def get_all(self, *, user_id: str = "", offset: int = 0, limit: int | None = None) -> list[MemoryEntry]:
        """Return entries, optionally filtered by user_id with pagination."""
        ...

    @abc.abstractmethod
    def count(self) -> int:
        """Total number of stored entries."""
        ...

    @abc.abstractmethod
    def health_check(self) -> BackendStatus:
        """Check backend health and return status."""
        ...

    @abc.abstractmethod
    def consolidate(self) -> int:
        """Consolidate / compact memories.  Returns number of entries affected."""
        ...

    # ── Optional CRUD extensions (default impls, override to enable) ──

    def delete(self, entry_id: str) -> bool:
        """Delete an entry by ID.  Returns True if deleted, False if not found."""
        raise NotImplementedError(f"{type(self).__name__} does not support delete()")

    def update(self, entry_id: str, *, text: str | None = None, tags: tuple[str, ...] | None = None, metadata: dict[str, Any] | None = None) -> MemoryEntry | None:
        """Update an entry's text, tags, or metadata.  Returns updated entry or None."""
        raise NotImplementedError(f"{type(self).__name__} does not support update()")

    def store_many(self, entries: list[dict[str, Any]]) -> list[MemoryEntry]:
        """Batch-store multiple entries.  Default: sequential ``store()`` calls."""
        results: list[MemoryEntry] = []
        for e in entries:
            results.append(self.store(
                e["text"],
                user_id=e.get("user_id", ""),
                tags=tuple(e.get("tags", ())),
                metadata=e.get("metadata"),
            ))
        return results
