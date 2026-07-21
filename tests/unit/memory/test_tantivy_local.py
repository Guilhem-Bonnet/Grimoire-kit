"""Tests for grimoire.memory.backends.tantivy_local — TantivyMemoryBackend.

Skipped when the ``tantivy`` extra is not installed
(``pip install grimoire-kit[search]``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tantivy")

from grimoire.memory.backends.base import MemoryEntry
from grimoire.memory.backends.tantivy_local import TantivyMemoryBackend


@pytest.fixture()
def backend(tmp_path: Path) -> TantivyMemoryBackend:
    return TantivyMemoryBackend(tmp_path / "tantivy-index")


# ── store / recall ────────────────────────────────────────────────────────────

class TestStoreRecall:
    def test_store_returns_entry(self, backend: TantivyMemoryBackend) -> None:
        entry = backend.store("hello world")
        assert isinstance(entry, MemoryEntry)
        assert entry.text == "hello world"
        assert entry.user_id == "global"
        assert entry.id

    def test_recall_by_id(self, backend: TantivyMemoryBackend) -> None:
        entry = backend.store("test", tags=("a",), metadata={"k": "v"})
        recalled = backend.recall(entry.id)
        assert recalled is not None
        assert recalled.text == "test"
        assert recalled.tags == ("a",)
        assert recalled.metadata == {"k": "v"}

    def test_recall_missing(self, backend: TantivyMemoryBackend) -> None:
        assert backend.recall("nonexistent") is None


# ── search (BM25 + stemming) ──────────────────────────────────────────────────

class TestSearch:
    def test_search_finds_match(self, backend: TantivyMemoryBackend) -> None:
        backend.store("python programming language")
        backend.store("javascript runtime")
        results = backend.search("python")
        assert len(results) >= 1
        assert results[0].text == "python programming language"
        assert results[0].score > 0

    def test_search_empty_query(self, backend: TantivyMemoryBackend) -> None:
        backend.store("anything")
        assert backend.search("") == []

    def test_search_no_match(self, backend: TantivyMemoryBackend) -> None:
        backend.store("hello world")
        assert backend.search("zyxwvut") == []

    def test_search_respects_limit(self, backend: TantivyMemoryBackend) -> None:
        for i in range(10):
            backend.store(f"item {i} with common keyword")
        assert len(backend.search("keyword", limit=3)) == 3

    def test_search_filters_by_user(self, backend: TantivyMemoryBackend) -> None:
        backend.store("python for alice", user_id="alice")
        backend.store("python for bob", user_id="bob")
        results = backend.search("python", user_id="alice")
        assert len(results) == 1
        assert results[0].user_id == "alice"

    def test_french_stemming(self, backend: TantivyMemoryBackend) -> None:
        backend.store("harmonisation des grimoires")
        results = backend.search("harmonisé")
        assert len(results) == 1

    def test_english_stemming(self, backend: TantivyMemoryBackend) -> None:
        backend.store("indexing large repositories")
        results = backend.search("indexed repository")
        assert len(results) == 1


# ── get_all / count / consolidate ─────────────────────────────────────────────

class TestGetAllCount:
    def test_empty(self, backend: TantivyMemoryBackend) -> None:
        assert backend.get_all() == []
        assert backend.count() == 0

    def test_returns_all_with_pagination(self, backend: TantivyMemoryBackend) -> None:
        for i in range(5):
            backend.store(f"item-{i}")
        assert backend.count() == 5
        assert len(backend.get_all(offset=2, limit=2)) == 2

    def test_filters_by_user(self, backend: TantivyMemoryBackend) -> None:
        backend.store("a", user_id="alice")
        backend.store("b", user_id="bob")
        assert len(backend.get_all(user_id="alice")) == 1

    def test_consolidate_removes_duplicates(self, backend: TantivyMemoryBackend) -> None:
        backend.store("dup")
        backend.store("dup")
        backend.store("unique")
        assert backend.consolidate() == 1
        assert backend.count() == 2

    def test_health_check(self, backend: TantivyMemoryBackend) -> None:
        status = backend.health_check()
        assert status.backend == "tantivy-local"
        assert status.healthy
        assert status.detail["stemming"] == "fr+en"


# ── CRUD extensions ───────────────────────────────────────────────────────────

class TestCrud:
    def test_delete_existing(self, backend: TantivyMemoryBackend) -> None:
        entry = backend.store("to delete")
        deleted = backend.delete(entry.id)
        assert deleted is True
        assert backend.recall(entry.id) is None

    def test_delete_missing(self, backend: TantivyMemoryBackend) -> None:
        deleted = backend.delete("nonexistent")
        assert deleted is False

    def test_update_text_reindexes(self, backend: TantivyMemoryBackend) -> None:
        entry = backend.store("original wording")
        updated = backend.update(entry.id, text="replacement phrasing")
        assert updated is not None
        assert updated.text == "replacement phrasing"
        assert backend.search("original") == []
        assert len(backend.search("replacement")) == 1
        assert backend.count() == 1

    def test_update_missing(self, backend: TantivyMemoryBackend) -> None:
        assert backend.update("nonexistent", text="x") is None

    def test_update_preserves_unchanged_fields(self, backend: TantivyMemoryBackend) -> None:
        entry = backend.store("original", tags=("keep",), metadata={"k": "v"})
        updated = backend.update(entry.id, text="changed")
        assert updated is not None
        assert updated.tags == ("keep",)
        assert updated.metadata == {"k": "v"}

    def test_upsert_creates_then_replaces(self, backend: TantivyMemoryBackend) -> None:
        created = backend.upsert("fixed-id", "first version")
        assert created.id == "fixed-id"
        replaced = backend.upsert("fixed-id", "second version")
        assert replaced.text == "second version"
        assert backend.count() == 1


# ── persistence ───────────────────────────────────────────────────────────────

class TestPersistence:
    def test_data_survives_reload(self, tmp_path: Path) -> None:
        d = tmp_path / "tantivy-index"
        b1 = TantivyMemoryBackend(d)
        b1.store("persistent fact", tags=("a",))
        b2 = TantivyMemoryBackend(d)
        assert b2.count() == 1
        assert b2.get_all()[0].tags == ("a",)
