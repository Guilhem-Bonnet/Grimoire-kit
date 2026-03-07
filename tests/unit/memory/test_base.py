"""Tests for bmad.memory.backends.base — ABC + data models."""

from __future__ import annotations

from typing import Any

import pytest

from bmad.memory.backends.base import BackendStatus, MemoryBackend, MemoryEntry

# ── MemoryEntry ───────────────────────────────────────────────────────────────

class TestMemoryEntry:
    def test_defaults(self) -> None:
        e = MemoryEntry(id="1", text="hello")
        assert e.user_id == "global"
        assert e.metadata == {}
        assert e.created_at == ""
        assert e.score == 0.0

    def test_to_dict(self) -> None:
        e = MemoryEntry(id="x", text="t", user_id="u", metadata={"k": "v"}, created_at="2025-01-01", score=0.8)
        d = e.to_dict()
        assert d["id"] == "x"
        assert d["score"] == 0.8
        assert d["metadata"] == {"k": "v"}

    def test_frozen(self) -> None:
        e = MemoryEntry(id="1", text="t")
        with pytest.raises(AttributeError):
            e.text = "new"  # type: ignore[misc]


# ── BackendStatus ─────────────────────────────────────────────────────────────

class TestBackendStatus:
    def test_basic(self) -> None:
        s = BackendStatus(backend="local", healthy=True, entries=5)
        assert s.healthy
        assert s.entries == 5

    def test_with_detail(self) -> None:
        s = BackendStatus(backend="qdrant", healthy=False, entries=0, detail={"error": "timeout"})
        assert not s.healthy
        assert s.detail["error"] == "timeout"


# ── ABC contract ──────────────────────────────────────────────────────────────

class TestMemoryBackendABC:
    def test_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            MemoryBackend()  # type: ignore[abstract]

    def test_subclass_must_implement_all(self) -> None:
        class Partial(MemoryBackend):
            def store(self, text: str, *, user_id: str = "", metadata: dict[str, Any] | None = None) -> MemoryEntry:
                return MemoryEntry(id="1", text=text)

        with pytest.raises(TypeError):
            Partial()  # type: ignore[abstract]

    def test_full_subclass(self) -> None:
        class Full(MemoryBackend):
            def store(self, text: str, *, user_id: str = "", metadata: dict[str, Any] | None = None) -> MemoryEntry:
                return MemoryEntry(id="1", text=text)
            def recall(self, entry_id: str) -> MemoryEntry | None:
                return None
            def search(self, query: str, *, user_id: str = "", limit: int = 5) -> list[MemoryEntry]:
                return []
            def get_all(self, *, user_id: str = "") -> list[MemoryEntry]:
                return []
            def count(self) -> int:
                return 0
            def health_check(self) -> BackendStatus:
                return BackendStatus(backend="test", healthy=True, entries=0)
            def consolidate(self) -> int:
                return 0

        b = Full()
        assert b.count() == 0
        assert b.health_check().healthy
