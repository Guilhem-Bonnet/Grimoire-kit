"""Tests for grimoire.memory.backends.lexical — LexicalMemoryBackend (SQLite FTS5)."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from grimoire.memory.backends.base import BackendStatus, MemoryEntry
from grimoire.memory.backends.lexical import LexicalMemoryBackend, fts5_available

pytestmark = pytest.mark.skipif(not fts5_available(), reason="SQLite build lacks FTS5")


@pytest.fixture()
def backend(tmp_path: Path) -> LexicalMemoryBackend:
    return LexicalMemoryBackend(tmp_path / "mem_lexical.sqlite3")


# ── store / recall ────────────────────────────────────────────────────────────

class TestStoreRecall:
    def test_store_returns_entry(self, backend: LexicalMemoryBackend) -> None:
        entry = backend.store("hello world")
        assert isinstance(entry, MemoryEntry)
        assert entry.text == "hello world"
        assert entry.user_id == "global"
        assert entry.id

    def test_recall_by_id(self, backend: LexicalMemoryBackend) -> None:
        entry = backend.store("test")
        recalled = backend.recall(entry.id)
        assert recalled is not None
        assert recalled.text == "test"

    def test_recall_missing(self, backend: LexicalMemoryBackend) -> None:
        assert backend.recall("nonexistent") is None

    def test_store_with_user_id(self, backend: LexicalMemoryBackend) -> None:
        entry = backend.store("data", user_id="alice")
        assert entry.user_id == "alice"

    def test_store_with_metadata(self, backend: LexicalMemoryBackend) -> None:
        entry = backend.store("data", metadata={"tag": "important"})
        assert entry.metadata["tag"] == "important"

    def test_store_with_tags(self, backend: LexicalMemoryBackend) -> None:
        entry = backend.store("data", tags=("python", "memory"))
        recalled = backend.recall(entry.id)
        assert recalled is not None
        assert recalled.tags == ("python", "memory")


# ── search (BM25) ─────────────────────────────────────────────────────────────

class TestSearch:
    def test_search_finds_match(self, backend: LexicalMemoryBackend) -> None:
        backend.store("python programming language")
        backend.store("javascript runtime")
        results = backend.search("python")
        assert len(results) >= 1
        assert results[0].text == "python programming language"

    def test_search_empty_query(self, backend: LexicalMemoryBackend) -> None:
        backend.store("anything")
        assert backend.search("") == []

    def test_search_no_match(self, backend: LexicalMemoryBackend) -> None:
        backend.store("hello world")
        assert backend.search("zyxwvut") == []

    def test_search_respects_limit(self, backend: LexicalMemoryBackend) -> None:
        for i in range(10):
            backend.store(f"item {i} with common keyword")
        results = backend.search("keyword", limit=3)
        assert len(results) == 3

    def test_search_filters_by_user(self, backend: LexicalMemoryBackend) -> None:
        backend.store("python for alice", user_id="alice")
        backend.store("python for bob", user_id="bob")
        results = backend.search("python", user_id="alice")
        assert len(results) == 1
        assert results[0].user_id == "alice"

    def test_bm25_ranks_denser_match_higher(self, backend: LexicalMemoryBackend) -> None:
        backend.store("python appears once in this rather long text about other topics entirely")
        backend.store("python python tutorial")
        results = backend.search("python")
        assert len(results) == 2
        assert results[0].text == "python python tutorial"
        assert results[0].score >= results[1].score

    def test_search_ignores_fts_operators(self, backend: LexicalMemoryBackend) -> None:
        backend.store("plain fact")
        # Raw FTS5 syntax in the query must not raise.
        assert backend.search('fact AND OR NOT "*') != []

    def test_diacritics_insensitive(self, backend: LexicalMemoryBackend) -> None:
        backend.store("évènement réservé aux membres")
        results = backend.search("evenement reserve")
        assert len(results) == 1


# ── get_all / count ───────────────────────────────────────────────────────────

class TestGetAll:
    def test_empty(self, backend: LexicalMemoryBackend) -> None:
        assert backend.get_all() == []

    def test_returns_all(self, backend: LexicalMemoryBackend) -> None:
        backend.store("a")
        backend.store("b")
        assert len(backend.get_all()) == 2

    def test_filters_by_user(self, backend: LexicalMemoryBackend) -> None:
        backend.store("a", user_id="alice")
        backend.store("b", user_id="bob")
        assert len(backend.get_all(user_id="alice")) == 1

    def test_pagination_offset_and_limit(self, backend: LexicalMemoryBackend) -> None:
        for i in range(10):
            backend.store(f"item-{i}")
        result = backend.get_all(offset=3, limit=4)
        assert len(result) == 4
        assert result[0].text == "item-3"

    def test_count(self, backend: LexicalMemoryBackend) -> None:
        assert backend.count() == 0
        backend.store("a")
        backend.store("b")
        assert backend.count() == 2


# ── health / consolidate ──────────────────────────────────────────────────────

class TestHealthConsolidate:
    def test_healthy(self, backend: LexicalMemoryBackend) -> None:
        status = backend.health_check()
        assert isinstance(status, BackendStatus)
        assert status.backend == "lexical"
        assert status.healthy
        assert status.detail["search"] == "fts5-bm25"

    def test_consolidate_removes_duplicates(self, backend: LexicalMemoryBackend) -> None:
        backend.store("dup")
        backend.store("dup")
        backend.store("unique")
        assert backend.consolidate() == 1
        assert backend.count() == 2

    def test_different_users_not_duplicate(self, backend: LexicalMemoryBackend) -> None:
        backend.store("same text", user_id="alice")
        backend.store("same text", user_id="bob")
        assert backend.consolidate() == 0


# ── CRUD extensions ───────────────────────────────────────────────────────────

class TestCrud:
    def test_delete_existing(self, backend: LexicalMemoryBackend) -> None:
        entry = backend.store("to delete")
        deleted = backend.delete(entry.id)
        assert deleted is True
        assert backend.recall(entry.id) is None
        assert backend.search("delete") == []

    def test_delete_missing(self, backend: LexicalMemoryBackend) -> None:
        deleted = backend.delete("nonexistent")
        assert deleted is False

    def test_update_text_reindexes(self, backend: LexicalMemoryBackend) -> None:
        entry = backend.store("original wording")
        updated = backend.update(entry.id, text="replacement phrasing")
        assert updated is not None
        assert updated.text == "replacement phrasing"
        assert updated.updated_at != ""
        assert backend.search("original") == []
        assert len(backend.search("replacement")) == 1

    def test_update_missing(self, backend: LexicalMemoryBackend) -> None:
        assert backend.update("nonexistent", text="x") is None

    def test_update_preserves_unchanged_fields(self, backend: LexicalMemoryBackend) -> None:
        entry = backend.store("original", tags=("keep",), metadata={"k": "v"})
        updated = backend.update(entry.id, text="changed")
        assert updated is not None
        assert updated.tags == ("keep",)
        assert updated.metadata == {"k": "v"}

    def test_upsert_creates_then_replaces(self, backend: LexicalMemoryBackend) -> None:
        created = backend.upsert("fixed-id", "first version")
        assert created.id == "fixed-id"
        replaced = backend.upsert("fixed-id", "second version")
        assert replaced.text == "second version"
        assert backend.count() == 1
        assert len(backend.search("second")) == 1

    def test_store_many_batch(self, backend: LexicalMemoryBackend) -> None:
        entries = backend.store_many([
            {"text": "batch one"},
            {"text": "batch two", "user_id": "alice", "tags": ["t"]},
        ])
        assert len(entries) == 2
        assert backend.count() == 2
        assert len(backend.search("batch")) == 2


# ── persistence / migration ───────────────────────────────────────────────────

class TestPersistence:
    def test_data_survives_reload(self, tmp_path: Path) -> None:
        f = tmp_path / "persist.sqlite3"
        b1 = LexicalMemoryBackend(f)
        b1.store("persistent fact", tags=("a", "b"))
        b1.close()
        b2 = LexicalMemoryBackend(f)
        assert b2.count() == 1
        assert b2.get_all()[0].tags == ("a", "b")

    def test_migrates_legacy_json(self, tmp_path: Path) -> None:
        legacy = tmp_path / "grimoire.json"
        legacy.write_text(json.dumps([
            {"id": "legacy-1", "text": "ancienne mémoire", "user_id": "guilhem",
             "tags": ["projet"], "metadata": {"k": "v"}, "created_at": "2026-01-01T00:00:00"},
            {"id": "legacy-2", "text": "autre souvenir", "user_id": "global",
             "tags": [], "metadata": {}, "created_at": "2026-01-02T00:00:00"},
        ]), encoding="utf-8")
        backend = LexicalMemoryBackend(tmp_path / "mem.sqlite3", legacy_json=legacy)
        assert backend.count() == 2
        migrated = backend.recall("legacy-1")
        assert migrated is not None
        assert migrated.user_id == "guilhem"
        assert migrated.tags == ("projet",)
        assert migrated.created_at == "2026-01-01T00:00:00"
        assert len(backend.search("memoire")) == 1  # diacritics-insensitive too

    def test_migration_skipped_when_db_populated(self, tmp_path: Path) -> None:
        legacy = tmp_path / "grimoire.json"
        legacy.write_text(json.dumps([{"id": "x", "text": "legacy"}]), encoding="utf-8")
        f = tmp_path / "mem.sqlite3"
        b1 = LexicalMemoryBackend(f)
        b1.store("already here")
        b1.close()
        b2 = LexicalMemoryBackend(f, legacy_json=legacy)
        assert b2.count() == 1
        assert b2.recall("x") is None

    def test_migration_tolerates_corrupt_json(self, tmp_path: Path) -> None:
        legacy = tmp_path / "grimoire.json"
        legacy.write_text("not valid json{{{", encoding="utf-8")
        backend = LexicalMemoryBackend(tmp_path / "mem.sqlite3", legacy_json=legacy)
        assert backend.count() == 0


# ── thread safety ─────────────────────────────────────────────────────────────

class TestThreadSafety:
    def test_concurrent_stores(self, backend: LexicalMemoryBackend) -> None:
        errors: list[Exception] = []

        def batch_store(start: int) -> None:
            try:
                for i in range(20):
                    backend.store(f"thread-{start}-item-{i}")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=batch_store, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert backend.count() == 100
