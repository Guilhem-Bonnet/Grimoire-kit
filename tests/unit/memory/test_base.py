"""Tests for grimoire.memory.backends.base — ABC + data models."""

from __future__ import annotations

from typing import Any

import pytest

from grimoire.memory.backends.base import BackendStatus, MemoryBackend, MemoryEntry

# ── MemoryEntry ───────────────────────────────────────────────────────────────

class TestMemoryEntry:
    def test_defaults(self) -> None:
        e = MemoryEntry(id="1", text="hello")
        assert e.user_id == "global"
        assert e.tags == ()
        assert e.metadata == {}
        assert e.created_at == ""
        assert e.updated_at == ""
        assert e.score == 0.0

    def test_to_dict(self) -> None:
        e = MemoryEntry(id="x", text="t", user_id="u", tags=("a", "b"), metadata={"k": "v"}, created_at="2025-01-01", updated_at="2025-06-01", score=0.8)
        d = e.to_dict()
        assert d["id"] == "x"
        assert d["score"] == 0.8
        assert d["metadata"] == {"k": "v"}
        assert d["tags"] == ["a", "b"]
        assert d["updated_at"] == "2025-06-01"

    def test_frozen(self) -> None:
        e = MemoryEntry(id="1", text="t")
        with pytest.raises(AttributeError):
            e.text = "new"  # type: ignore[misc]

    def test_tags_are_tuple(self) -> None:
        e = MemoryEntry(id="1", text="t", tags=("x", "y"))
        assert isinstance(e.tags, tuple)
        assert e.tags == ("x", "y")


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

class _FullBackend(MemoryBackend):
    """Minimal concrete backend for contract testing."""

    def store(self, text: str, *, user_id: str = "", tags: tuple[str, ...] = (), metadata: dict[str, Any] | None = None) -> MemoryEntry:
        return MemoryEntry(id="1", text=text, tags=tags)

    def recall(self, entry_id: str) -> MemoryEntry | None:
        return None

    def search(self, query: str, *, user_id: str = "", limit: int = 5) -> list[MemoryEntry]:
        return []

    def get_all(self, *, user_id: str = "", offset: int = 0, limit: int | None = None) -> list[MemoryEntry]:
        return []

    def count(self) -> int:
        return 0

    def health_check(self) -> BackendStatus:
        return BackendStatus(backend="test", healthy=True, entries=0)

    def consolidate(self) -> int:
        return 0


class TestMemoryBackendABC:
    def test_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            MemoryBackend()  # type: ignore[abstract]

    def test_subclass_must_implement_all(self) -> None:
        class Partial(MemoryBackend):
            def store(self, text: str, *, user_id: str = "", tags: tuple[str, ...] = (), metadata: dict[str, Any] | None = None) -> MemoryEntry:
                return MemoryEntry(id="1", text=text)

        with pytest.raises(TypeError):
            Partial()  # type: ignore[abstract]

    def test_full_subclass(self) -> None:
        b = _FullBackend()
        assert b.count() == 0
        assert b.health_check().healthy


# ── Optional methods (default impls) ─────────────────────────────────────────

class TestOptionalMethods:
    def test_delete_raises_not_implemented(self) -> None:
        b = _FullBackend()
        with pytest.raises(NotImplementedError, match="_FullBackend"):
            b.delete("xxx")

    def test_update_raises_not_implemented(self) -> None:
        b = _FullBackend()
        with pytest.raises(NotImplementedError, match="_FullBackend"):
            b.update("xxx", text="new")

    def test_store_many_sequential_fallback(self) -> None:
        b = _FullBackend()
        entries = [{"text": "a"}, {"text": "b", "user_id": "alice", "tags": ["t"]}]
        results = b.store_many(entries)
        assert len(results) == 2
        assert results[0].text == "a"
        assert results[1].text == "b"
        assert results[1].tags == ("t",)
