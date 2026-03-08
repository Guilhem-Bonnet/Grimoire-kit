"""Tests for grimoire.memory.backends.local — LocalMemoryBackend."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.memory.backends.base import BackendStatus, MemoryEntry
from grimoire.memory.backends.local import LocalMemoryBackend


@pytest.fixture()
def backend(tmp_path: Path) -> LocalMemoryBackend:
    return LocalMemoryBackend(tmp_path / "mem.json")


# ── store / recall ────────────────────────────────────────────────────────────

class TestStoreRecall:
    def test_store_returns_entry(self, backend: LocalMemoryBackend) -> None:
        entry = backend.store("hello world")
        assert isinstance(entry, MemoryEntry)
        assert entry.text == "hello world"
        assert entry.user_id == "global"
        assert entry.id

    def test_recall_by_id(self, backend: LocalMemoryBackend) -> None:
        entry = backend.store("test")
        recalled = backend.recall(entry.id)
        assert recalled is not None
        assert recalled.text == "test"

    def test_recall_missing(self, backend: LocalMemoryBackend) -> None:
        assert backend.recall("nonexistent") is None

    def test_store_with_user_id(self, backend: LocalMemoryBackend) -> None:
        entry = backend.store("data", user_id="alice")
        assert entry.user_id == "alice"

    def test_store_with_metadata(self, backend: LocalMemoryBackend) -> None:
        entry = backend.store("data", metadata={"tag": "important"})
        assert entry.metadata["tag"] == "important"

    def test_created_at_set(self, backend: LocalMemoryBackend) -> None:
        entry = backend.store("data")
        assert entry.created_at != ""


# ── search ────────────────────────────────────────────────────────────────────

class TestSearch:
    def test_search_finds_match(self, backend: LocalMemoryBackend) -> None:
        backend.store("python programming language")
        backend.store("javascript runtime")
        results = backend.search("python")
        assert len(results) >= 1
        assert results[0].text == "python programming language"

    def test_search_empty_query(self, backend: LocalMemoryBackend) -> None:
        backend.store("anything")
        assert backend.search("") == []

    def test_search_no_match(self, backend: LocalMemoryBackend) -> None:
        backend.store("hello world")
        assert backend.search("zyxwvut") == []

    def test_search_respects_limit(self, backend: LocalMemoryBackend) -> None:
        for i in range(10):
            backend.store(f"item {i} with common keyword")
        results = backend.search("keyword", limit=3)
        assert len(results) == 3

    def test_search_filters_by_user(self, backend: LocalMemoryBackend) -> None:
        backend.store("python for alice", user_id="alice")
        backend.store("python for bob", user_id="bob")
        results = backend.search("python", user_id="alice")
        assert len(results) == 1
        assert results[0].user_id == "alice"

    def test_search_scores_ranked(self, backend: LocalMemoryBackend) -> None:
        backend.store("python programming language")
        backend.store("python is great for programming data science")
        # Second entry has more matching words for "python programming"
        results = backend.search("python programming")
        assert len(results) == 2
        assert results[0].score >= results[1].score


# ── get_all ───────────────────────────────────────────────────────────────────

class TestGetAll:
    def test_empty(self, backend: LocalMemoryBackend) -> None:
        assert backend.get_all() == []

    def test_returns_all(self, backend: LocalMemoryBackend) -> None:
        backend.store("a")
        backend.store("b")
        assert len(backend.get_all()) == 2

    def test_filters_by_user(self, backend: LocalMemoryBackend) -> None:
        backend.store("a", user_id="alice")
        backend.store("b", user_id="bob")
        assert len(backend.get_all(user_id="alice")) == 1


# ── count ─────────────────────────────────────────────────────────────────────

class TestCount:
    def test_zero(self, backend: LocalMemoryBackend) -> None:
        assert backend.count() == 0

    def test_increments(self, backend: LocalMemoryBackend) -> None:
        backend.store("a")
        backend.store("b")
        assert backend.count() == 2


# ── health_check ──────────────────────────────────────────────────────────────

class TestHealthCheck:
    def test_healthy(self, backend: LocalMemoryBackend) -> None:
        status = backend.health_check()
        assert isinstance(status, BackendStatus)
        assert status.backend == "local"
        assert status.healthy
        assert status.entries == 0

    def test_after_store(self, backend: LocalMemoryBackend) -> None:
        backend.store("x")
        assert backend.health_check().entries == 1


# ── consolidate ───────────────────────────────────────────────────────────────

class TestConsolidate:
    def test_no_duplicates(self, backend: LocalMemoryBackend) -> None:
        backend.store("unique")
        assert backend.consolidate() == 0

    def test_removes_duplicates(self, backend: LocalMemoryBackend) -> None:
        backend.store("dup")
        backend.store("dup")
        backend.store("unique")
        removed = backend.consolidate()
        assert removed == 1
        assert backend.count() == 2

    def test_different_users_not_duplicate(self, backend: LocalMemoryBackend) -> None:
        backend.store("same text", user_id="alice")
        backend.store("same text", user_id="bob")
        assert backend.consolidate() == 0


# ── persistence ───────────────────────────────────────────────────────────────

class TestPersistence:
    def test_data_survives_reload(self, tmp_path: Path) -> None:
        f = tmp_path / "persist.json"
        b1 = LocalMemoryBackend(f)
        b1.store("persistent fact")
        # Create new instance (simulates restart)
        b2 = LocalMemoryBackend(f)
        assert b2.count() == 1
        assert b2.get_all()[0].text == "persistent fact"

    def test_corrupt_file(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.json"
        f.write_text("not valid json{{{")
        b = LocalMemoryBackend(f)
        assert b.count() == 0  # graceful recovery

    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.json"
        f.write_text("")
        b = LocalMemoryBackend(f)
        assert b.count() == 0
