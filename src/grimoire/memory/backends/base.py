"""Abstract base for memory backends.

Every backend must implement the five methods defined here.
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
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "user_id": self.user_id,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
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

    Subclasses must implement store, recall, search, consolidate, health_check.
    """

    @abc.abstractmethod
    def store(self, text: str, *, user_id: str = "", metadata: dict[str, Any] | None = None) -> MemoryEntry:
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
    def get_all(self, *, user_id: str = "") -> list[MemoryEntry]:
        """Return all entries, optionally filtered by user_id."""
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
