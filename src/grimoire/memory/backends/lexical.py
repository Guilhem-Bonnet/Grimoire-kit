"""Lexical memory backend — SQLite FTS5 with BM25 ranking, zero external dependencies.

Implements the ``backend: lexical`` contract declared in the config schema:
full-text search with BM25 scoring and diacritics-insensitive matching
(``unicode61 remove_diacritics 2``), without any vector database or service.

Entries live in a plain ``entries`` table; an external-content FTS5 table kept
in sync by triggers provides the search index.  All public methods are
thread-safe via a reentrant lock.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from grimoire.memory.backends.base import BackendStatus, MemoryBackend, MemoryEntry
from grimoire.memory.taxonomy import build_taxonomy, entry_matches_filters

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id TEXT NOT NULL UNIQUE,
    text TEXT NOT NULL,
    user_id TEXT NOT NULL DEFAULT 'global',
    tags_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
    text,
    content='entries',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
    INSERT INTO entries_fts(rowid, text) VALUES (new.rowid, new.text);
END;
CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
END;
CREATE TRIGGER IF NOT EXISTS entries_au AFTER UPDATE ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
    INSERT INTO entries_fts(rowid, text) VALUES (new.rowid, new.text);
END;
"""


def fts5_available() -> bool:
    """Whether the bundled SQLite supports FTS5 virtual tables."""
    try:
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("CREATE VIRTUAL TABLE probe USING fts5(x)")
        finally:
            conn.close()
        return True
    except sqlite3.OperationalError:
        return False


def _match_expression(query: str) -> str:
    """Build a safe FTS5 MATCH expression from free-form user input.

    Tokens are double-quoted (phrase syntax) so FTS5 operators and
    punctuation in the query cannot break the expression, then OR-joined
    to preserve the any-keyword recall of the legacy local backend.
    """
    tokens = re.findall(r"\w+", query.lower())
    return " OR ".join(f'"{token}"' for token in tokens)


class LexicalMemoryBackend(MemoryBackend):
    """SQLite FTS5 memory backend with BM25 ranking.

    Usage::

        backend = LexicalMemoryBackend(Path("/tmp/memory_lexical.sqlite3"))
        entry = backend.store("important fact")

    When ``legacy_json`` points at a legacy :class:`LocalMemoryBackend` file
    and the database is empty, its entries are imported once (IDs and
    timestamps preserved).
    """

    def __init__(self, db_file: Path, *, legacy_json: Path | None = None) -> None:
        self._file = db_file
        self._lock = threading.RLock()
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_file), timeout=10, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)
        if legacy_json is not None:
            self._migrate_legacy_json(legacy_json)

    # ── Migration ─────────────────────────────────────────────────────────

    def _migrate_legacy_json(self, legacy_json: Path) -> int:
        """Import entries from a legacy local JSON file into an empty database."""
        if not legacy_json.exists():
            return 0
        with self._lock:
            if self.count() > 0:
                return 0
            try:
                raw = json.loads(legacy_json.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return 0
            if not isinstance(raw, list):
                return 0
            rows = [
                (
                    str(rec.get("id") or uuid.uuid4()),
                    str(rec.get("text", "")),
                    str(rec.get("user_id") or "global"),
                    json.dumps(rec.get("tags") or [], ensure_ascii=False),
                    json.dumps(rec.get("metadata") or {}, ensure_ascii=False),
                    str(rec.get("created_at", "")),
                    str(rec.get("updated_at", "")),
                )
                for rec in raw
                if isinstance(rec, dict) and rec.get("text")
            ]
            with self._conn:
                self._conn.executemany(
                    "INSERT OR IGNORE INTO entries (id, text, user_id, tags_json, metadata_json, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
            return len(rows)

    # ── Contract ──────────────────────────────────────────────────────────

    def store(self, text: str, *, user_id: str = "", tags: tuple[str, ...] = (), metadata: dict[str, Any] | None = None) -> MemoryEntry:
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        entry_id = str(uuid.uuid4())
        uid = user_id or "global"
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO entries (id, text, user_id, tags_json, metadata_json, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, '')",
                (entry_id, text, uid, json.dumps(list(tags), ensure_ascii=False),
                 json.dumps(metadata or {}, ensure_ascii=False), ts),
            )
        return MemoryEntry(id=entry_id, text=text, user_id=uid, tags=tags, metadata=metadata or {}, created_at=ts)

    def recall(self, entry_id: str) -> MemoryEntry | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        return self._to_entry(row) if row is not None else None

    def search(self, query: str, *, user_id: str = "", limit: int = 5) -> list[MemoryEntry]:
        match = _match_expression(query)
        if not match:
            return []
        sql = (
            "SELECT e.*, -bm25(entries_fts) AS score FROM entries_fts"
            " JOIN entries e ON e.rowid = entries_fts.rowid"
            " WHERE entries_fts MATCH ?"
        )
        params: list[Any] = [match]
        if user_id:
            sql += " AND e.user_id = ?"
            params.append(user_id)
        sql += " ORDER BY bm25(entries_fts) LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._to_entry(row, score=float(row["score"])) for row in rows]

    def search_filtered(
        self,
        query: str,
        *,
        user_id: str = "",
        limit: int = 5,
        filters: dict[str, str] | None = None,
    ) -> list[MemoryEntry]:
        candidates = self.search(query, user_id=user_id, limit=max(limit * 10, limit))
        if not filters:
            return candidates[:limit]
        return [
            entry
            for entry in candidates
            if entry_matches_filters(
                entry,
                wing=filters.get("wing", ""),
                hall=filters.get("hall", ""),
                room=filters.get("room", ""),
            )
        ][:limit]

    def get_all(self, *, user_id: str = "", offset: int = 0, limit: int | None = None) -> list[MemoryEntry]:
        sql = "SELECT * FROM entries"
        params: list[Any] = []
        if user_id:
            sql += " WHERE user_id = ?"
            params.append(user_id)
        sql += " ORDER BY rowid LIMIT ? OFFSET ?"
        params.extend([-1 if limit is None else limit, offset])
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._to_entry(row) for row in rows]

    def get_all_filtered(
        self,
        *,
        user_id: str = "",
        offset: int = 0,
        limit: int | None = None,
        filters: dict[str, str] | None = None,
    ) -> list[MemoryEntry]:
        entries = self.get_all(user_id=user_id, offset=0, limit=None)
        if filters:
            entries = [
                entry
                for entry in entries
                if entry_matches_filters(
                    entry,
                    wing=filters.get("wing", ""),
                    hall=filters.get("hall", ""),
                    room=filters.get("room", ""),
                )
            ]
        return entries[offset:] if limit is None else entries[offset:offset + limit]

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM entries").fetchone()
        return int(row["n"])

    def health_check(self) -> BackendStatus:
        return BackendStatus(
            backend="lexical",
            healthy=True,
            entries=self.count(),
            detail={"file": str(self._file), "search": "fts5-bm25"},
        )

    def consolidate(self) -> int:
        """Remove exact duplicates (same text + user_id), keeping the oldest."""
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM entries WHERE rowid NOT IN"
                " (SELECT MIN(rowid) FROM entries GROUP BY text, user_id)"
            )
        return int(cursor.rowcount)

    def taxonomy(self, *, user_id: str = "", filters: dict[str, str] | None = None) -> dict[str, Any]:
        return build_taxonomy(self.get_all_filtered(user_id=user_id, filters=filters))

    # ── CRUD extensions ───────────────────────────────────────────────────

    def delete(self, entry_id: str) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        return cursor.rowcount > 0

    def update(self, entry_id: str, *, text: str | None = None, tags: tuple[str, ...] | None = None, metadata: dict[str, Any] | None = None) -> MemoryEntry | None:
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        sets: list[str] = ["updated_at = ?"]
        params: list[Any] = [ts]
        if text is not None:
            sets.append("text = ?")
            params.append(text)
        if tags is not None:
            sets.append("tags_json = ?")
            params.append(json.dumps(list(tags), ensure_ascii=False))
        if metadata is not None:
            sets.append("metadata_json = ?")
            params.append(json.dumps(metadata, ensure_ascii=False))
        params.append(entry_id)
        with self._lock, self._conn:
            # `sets` only contains hardcoded column fragments — no user input.
            cursor = self._conn.execute(f"UPDATE entries SET {', '.join(sets)} WHERE id = ?", params)  # noqa: S608
            if cursor.rowcount == 0:
                return None
        return self.recall(entry_id)

    def upsert(
        self,
        entry_id: str,
        text: str,
        *,
        user_id: str = "",
        tags: tuple[str, ...] = (),
        metadata: dict[str, Any] | None = None,
    ) -> MemoryEntry:
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        uid = user_id or "global"
        tags_json = json.dumps(list(tags), ensure_ascii=False)
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE entries SET text = ?, user_id = ?, tags_json = ?, metadata_json = ?, updated_at = ?"
                " WHERE id = ?",
                (text, uid, tags_json, metadata_json, ts, entry_id),
            )
            if cursor.rowcount == 0:
                self._conn.execute(
                    "INSERT INTO entries (id, text, user_id, tags_json, metadata_json, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, '')",
                    (entry_id, text, uid, tags_json, metadata_json, ts),
                )
        entry = self.recall(entry_id)
        if entry is None:  # pragma: no cover - defensive, upsert guarantees existence
            raise RuntimeError(f"upsert failed for entry {entry_id!r}")
        return entry

    def store_many(self, entries: list[dict[str, Any]]) -> list[MemoryEntry]:
        """Batch-store in a single transaction."""
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        results: list[MemoryEntry] = []
        rows: list[tuple[Any, ...]] = []
        for e in entries:
            entry_id = str(uuid.uuid4())
            uid = str(e.get("user_id") or "global")
            tags = tuple(e.get("tags", ()))
            metadata = e.get("metadata") or {}
            rows.append((
                entry_id, e["text"], uid,
                json.dumps(list(tags), ensure_ascii=False),
                json.dumps(metadata, ensure_ascii=False), ts,
            ))
            results.append(MemoryEntry(id=entry_id, text=e["text"], user_id=uid, tags=tags, metadata=metadata, created_at=ts))
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT INTO entries (id, text, user_id, tags_json, metadata_json, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, '')",
                rows,
            )
        return results

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        with self._lock:
            self._conn.close()

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _to_entry(row: sqlite3.Row, score: float = 0.0) -> MemoryEntry:
        try:
            tags = tuple(json.loads(row["tags_json"]))
        except (json.JSONDecodeError, TypeError):
            tags = ()
        try:
            metadata = json.loads(row["metadata_json"])
        except (json.JSONDecodeError, TypeError):
            metadata = {}
        return MemoryEntry(
            id=str(row["id"]),
            text=str(row["text"]),
            user_id=str(row["user_id"]),
            tags=tags,
            metadata=metadata,
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            score=score,
        )
