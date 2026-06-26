"""
Backend Lexical — sqlite FTS5, zéro dépendance externe, ZÉRO base vectorielle.

Alternative gouvernée pour les environnements qui interdisent une DB vectorielle
locale (contrainte corpo fréquente). Recherche plein-texte BM25, accent-insensible,
indexée — un seul fichier `.sqlite`, aucun service, aucun vecteur, aucun réseau.

Drop-in du backend `local` (même contrat MemoryBackend) mais avec un vrai index
FTS5 au lieu d'un scan JSON naïf. Sélectionné quand `memory.vector_database: false`.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from pathlib import Path

_MEMORY_DIR = Path(__file__).resolve().parent.parent.parent / "memory"
_DB_FILE = _MEMORY_DIR / "memory-lexical.sqlite"


def _fts_query(raw: str) -> str:
    """Requête FTS5-safe : tokens cités, joints en OR pour la recall (BM25 classe)."""
    tokens = re.findall(r"\w+", raw, flags=re.UNICODE)
    return " OR ".join(f'"{t}"' for t in tokens)


class LexicalBackend:
    """Backend sqlite FTS5 — recherche lexicale BM25 sans vecteur ni service."""

    def __init__(self, db_file: Path | None = None):
        self._file = db_file or _DB_FILE
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(self._file)
        self._fts = self._ensure_schema()

    def _ensure_schema(self) -> bool:
        try:
            self._con.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS mem USING fts5("
                "id UNINDEXED, user_id UNINDEXED, memory, metadata UNINDEXED, "
                "created_at UNINDEXED, tokenize='unicode61 remove_diacritics 2')"
            )
            self._con.commit()
            return True
        except sqlite3.OperationalError:
            # Build sqlite sans FTS5 : table simple + recherche LIKE en repli.
            self._con.execute(
                "CREATE TABLE IF NOT EXISTS mem ("
                "id TEXT, user_id TEXT, memory TEXT, metadata TEXT, created_at TEXT)"
            )
            self._con.commit()
            return False

    # --------------------------------------------------------------- CONTRAT
    def add(self, text: str, user_id: str = "", metadata: dict | None = None) -> dict:
        entry = {
            "id": str(uuid.uuid4()),
            "memory": text,
            "user_id": user_id or "global",
            "metadata": metadata or {},
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        self._con.execute(
            "INSERT INTO mem(id, user_id, memory, metadata, created_at) VALUES (?,?,?,?,?)",
            (
                entry["id"],
                entry["user_id"],
                text,
                json.dumps(entry["metadata"], ensure_ascii=False),
                entry["created_at"],
            ),
        )
        self._con.commit()
        return entry

    def search(self, query: str, user_id: str = "", limit: int = 5) -> list[dict]:
        if self._fts:
            sql = (
                "SELECT id, user_id, memory, metadata, created_at, bm25(mem) AS score "
                "FROM mem WHERE mem MATCH ?"
            )
            params: list = [_fts_query(query)]
            if user_id:
                sql += " AND user_id = ?"
                params.append(user_id)
            sql += " ORDER BY score LIMIT ?"
            params.append(limit)
            rows = self._con.execute(sql, params).fetchall()
        else:
            like = f"%{query}%"
            sql = "SELECT id, user_id, memory, metadata, created_at, 0 FROM mem WHERE memory LIKE ?"
            params = [like]
            if user_id:
                sql += " AND user_id = ?"
                params.append(user_id)
            sql += " LIMIT ?"
            params.append(limit)
            rows = self._con.execute(sql, params).fetchall()
        return [self._row(r, score=True) for r in rows]

    def get_all(self, user_id: str = "") -> list[dict]:
        sql = "SELECT id, user_id, memory, metadata, created_at FROM mem"
        params: list = []
        if user_id:
            sql += " WHERE user_id = ?"
            params.append(user_id)
        return [self._row(r) for r in self._con.execute(sql, params).fetchall()]

    def count(self) -> int:
        return self._con.execute("SELECT count(*) FROM mem").fetchone()[0]

    def status(self) -> dict:
        return {
            "backend": "lexical",
            "file": str(self._file),
            "entries": self.count(),
            "search": "fts5-bm25" if self._fts else "like-fallback",
            "vector_database": False,
        }

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _row(r: tuple, score: bool = False) -> dict:
        entry = {
            "id": r[0],
            "user_id": r[1],
            "memory": r[2],
            "metadata": json.loads(r[3]) if r[3] else {},
            "created_at": r[4],
        }
        if score:
            entry["score"] = r[5]
        return entry
