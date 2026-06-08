"""SQLite sidecar for temporal facts and agent diary entries.

This keeps durable structured memory next to the primary backend without
changing the source of truth used for semantic recall.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "DiaryRecord",
    "KnowledgeFact",
    "MemorySidecar",
]


@dataclass(frozen=True, slots=True)
class KnowledgeFact:
    """A temporal fact stored in the sidecar knowledge graph."""

    id: str
    subject: str
    predicate: str
    object: str
    valid_from: str = ""
    valid_to: str = ""
    confidence: float = 1.0
    source_memory_id: str = ""
    wing: str = ""
    hall: str = ""
    room: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "confidence": self.confidence,
            "source_memory_id": self.source_memory_id,
            "wing": self.wing,
            "hall": self.hall,
            "room": self.room,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class DiaryRecord:
    """A journal entry associated with one agent."""

    id: str
    agent_name: str
    topic: str
    entry: str
    entry_format: str = "markdown"
    related_memory_id: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent_name": self.agent_name,
            "topic": self.topic,
            "entry": self.entry,
            "entry_format": self.entry_format,
            "related_memory_id": self.related_memory_id,
            "created_at": self.created_at,
        }


class MemorySidecar:
    """Structured sidecar built on SQLite + JSONL WAL."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._wal_path = db_path.with_suffix(".wal.jsonl")
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None
        self._init_db()

    @property
    def db_path(self) -> Path:
        return self._db_path

    def _conn(self) -> sqlite3.Connection:
        if self._connection is None:
            self._connection = sqlite3.connect(self._db_path, timeout=10, check_same_thread=False)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA journal_mode=WAL")
        return self._connection

    def _init_db(self) -> None:
        conn = self._conn()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS facts (
                id TEXT PRIMARY KEY,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object TEXT NOT NULL,
                valid_from TEXT DEFAULT '',
                valid_to TEXT DEFAULT '',
                confidence REAL DEFAULT 1.0,
                source_memory_id TEXT DEFAULT '',
                wing TEXT DEFAULT '',
                hall TEXT DEFAULT '',
                room TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject);
            CREATE INDEX IF NOT EXISTS idx_facts_object ON facts(object);
            CREATE INDEX IF NOT EXISTS idx_facts_predicate ON facts(predicate);
            CREATE INDEX IF NOT EXISTS idx_facts_validity ON facts(valid_from, valid_to);

            CREATE TABLE IF NOT EXISTS diary (
                id TEXT PRIMARY KEY,
                agent_name TEXT NOT NULL,
                topic TEXT DEFAULT 'general',
                entry TEXT NOT NULL,
                entry_format TEXT DEFAULT 'markdown',
                related_memory_id TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_diary_agent_created ON diary(agent_name, created_at DESC);
            """
        )
        conn.commit()

    def _wal_log(self, operation: str, payload: dict[str, Any]) -> None:
        entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "operation": operation,
            "payload": payload,
        }
        try:
            with open(self._wal_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError:
            return

    @staticmethod
    def _fact_id(subject: str, predicate: str, object_: str, valid_from: str) -> str:
        raw = f"{subject}|{predicate}|{object_}|{valid_from}".encode()
        return hashlib.sha256(raw).hexdigest()[:32]

    @staticmethod
    def _fact_from_row(row: sqlite3.Row) -> KnowledgeFact:
        return KnowledgeFact(
            id=str(row["id"]),
            subject=str(row["subject"]),
            predicate=str(row["predicate"]),
            object=str(row["object"]),
            valid_from=str(row["valid_from"] or ""),
            valid_to=str(row["valid_to"] or ""),
            confidence=float(row["confidence"]),
            source_memory_id=str(row["source_memory_id"] or ""),
            wing=str(row["wing"] or ""),
            hall=str(row["hall"] or ""),
            room=str(row["room"] or ""),
            created_at=str(row["created_at"] or ""),
        )

    @staticmethod
    def _diary_from_row(row: sqlite3.Row) -> DiaryRecord:
        return DiaryRecord(
            id=str(row["id"]),
            agent_name=str(row["agent_name"]),
            topic=str(row["topic"]),
            entry=str(row["entry"]),
            entry_format=str(row["entry_format"]),
            related_memory_id=str(row["related_memory_id"] or ""),
            created_at=str(row["created_at"] or ""),
        )

    def add_fact(
        self,
        subject: str,
        predicate: str,
        object_: str,
        *,
        valid_from: str = "",
        confidence: float = 1.0,
        source_memory_id: str = "",
        wing: str = "",
        hall: str = "",
        room: str = "",
    ) -> KnowledgeFact:
        """Insert a temporal fact. Duplicate inserts are idempotent."""
        fact_id = self._fact_id(subject, predicate, object_, valid_from)
        self._wal_log(
            "add_fact",
            {
                "id": fact_id,
                "subject": subject,
                "predicate": predicate,
                "object": object_,
                "valid_from": valid_from,
                "confidence": confidence,
                "source_memory_id": source_memory_id,
            },
        )
        with self._lock:
            conn = self._conn()
            conn.execute(
                """
                INSERT INTO facts (
                    id, subject, predicate, object, valid_from, confidence,
                    source_memory_id, wing, hall, room, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (
                    fact_id,
                    subject,
                    predicate,
                    object_,
                    valid_from,
                    confidence,
                    source_memory_id,
                    wing,
                    hall,
                    room,
                    time.strftime("%Y-%m-%dT%H:%M:%S"),
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()
        assert row is not None
        return self._fact_from_row(row)

    def invalidate_fact(self, subject: str, predicate: str, object_: str, *, ended: str = "") -> int:
        """Mark matching active facts as no longer true."""
        ended_at = ended or time.strftime("%Y-%m-%d")
        self._wal_log(
            "invalidate_fact",
            {
                "subject": subject,
                "predicate": predicate,
                "object": object_,
                "ended": ended_at,
            },
        )
        with self._lock:
            conn = self._conn()
            cur = conn.execute(
                """
                UPDATE facts
                SET valid_to = ?
                WHERE subject = ? AND predicate = ? AND object = ?
                  AND COALESCE(valid_to, '') = ''
                """,
                (ended_at, subject, predicate, object_),
            )
            conn.commit()
            return int(cur.rowcount)

    def query_facts(self, entity: str, *, as_of: str = "", direction: str = "both") -> list[KnowledgeFact]:
        """Return matching facts for one entity."""
        clauses: list[str] = []
        params: list[Any] = []

        if direction == "incoming":
            clauses.append("object = ?")
            params.append(entity)
        elif direction == "outgoing":
            clauses.append("subject = ?")
            params.append(entity)
        else:
            clauses.append("(subject = ? OR object = ?)")
            params.extend([entity, entity])

        if as_of:
            clauses.append("(COALESCE(valid_from, '') = '' OR valid_from <= ?)")
            clauses.append("(COALESCE(valid_to, '') = '' OR valid_to > ?)")
            params.extend([as_of, as_of])

        sql = "SELECT * FROM facts"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY COALESCE(valid_from, created_at), created_at"

        with self._lock:
            rows = self._conn().execute(sql, params).fetchall()
        return [self._fact_from_row(row) for row in rows]

    def timeline(self, entity: str = "") -> list[KnowledgeFact]:
        """Return chronological facts, optionally scoped to one entity."""
        if entity:
            return self.query_facts(entity)
        with self._lock:
            rows = self._conn().execute(
                "SELECT * FROM facts ORDER BY COALESCE(valid_from, created_at), created_at"
            ).fetchall()
        return [self._fact_from_row(row) for row in rows]

    def facts_stats(self) -> dict[str, Any]:
        """Return aggregate counts for the fact graph."""
        with self._lock:
            conn = self._conn()
            total = int(conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0])
            active = int(
                conn.execute("SELECT COUNT(*) FROM facts WHERE COALESCE(valid_to, '') = ''").fetchone()[0]
            )
            predicates = {
                str(row[0]): int(row[1])
                for row in conn.execute(
                    "SELECT predicate, COUNT(*) FROM facts GROUP BY predicate ORDER BY COUNT(*) DESC"
                ).fetchall()
            }
        return {
            "db_path": str(self._db_path),
            "facts": total,
            "active_facts": active,
            "expired_facts": max(total - active, 0),
            "predicates": predicates,
        }

    def write_diary(
        self,
        agent_name: str,
        entry: str,
        *,
        topic: str = "general",
        entry_format: str = "markdown",
        related_memory_id: str = "",
    ) -> DiaryRecord:
        """Append one diary entry for an agent."""
        record_id = f"diary_{uuid.uuid4().hex}"
        self._wal_log(
            "write_diary",
            {
                "id": record_id,
                "agent_name": agent_name,
                "topic": topic,
                "related_memory_id": related_memory_id,
            },
        )
        created_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self._lock:
            conn = self._conn()
            conn.execute(
                """
                INSERT INTO diary (
                    id, agent_name, topic, entry, entry_format, related_memory_id, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (record_id, agent_name, topic, entry, entry_format, related_memory_id, created_at),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM diary WHERE id = ?", (record_id,)).fetchone()
        assert row is not None
        return self._diary_from_row(row)

    def read_diary(self, agent_name: str, *, last_n: int = 10) -> list[DiaryRecord]:
        """Return recent diary entries for an agent, newest first."""
        with self._lock:
            rows = self._conn().execute(
                """
                SELECT *
                FROM diary
                WHERE agent_name = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (agent_name, last_n),
            ).fetchall()
        return [self._diary_from_row(row) for row in rows]

    def diary_stats(self) -> dict[str, Any]:
        """Return aggregate counts for agent diaries."""
        with self._lock:
            conn = self._conn()
            entries = int(conn.execute("SELECT COUNT(*) FROM diary").fetchone()[0])
            agents = int(conn.execute("SELECT COUNT(DISTINCT agent_name) FROM diary").fetchone()[0])
        return {
            "db_path": str(self._db_path),
            "diary_entries": entries,
            "agents": agents,
        }
