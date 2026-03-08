"""Local JSON-file memory backend — zero external dependencies.

Stores memories as JSON in a file.  Search uses keyword matching.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from bmad.memory.backends.base import BackendStatus, MemoryBackend, MemoryEntry


class LocalMemoryBackend(MemoryBackend):
    """JSON-file based memory backend.

    Usage::

        backend = LocalMemoryBackend(Path("/tmp/memory.json"))
        entry = backend.store("important fact")
    """

    def __init__(self, memory_file: Path) -> None:
        self._file = memory_file
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._data: list[dict[str, Any]] = []
        self._load()

    # ── I/O ───────────────────────────────────────────────────────────────

    def _load(self) -> None:
        if self._file.exists():
            try:
                with open(self._file, encoding="utf-8") as fh:
                    raw = json.load(fh)
                if isinstance(raw, list):
                    self._data = raw
            except (json.JSONDecodeError, OSError):
                self._data = []

    def _save(self) -> None:
        # Atomic write: write to temp file then rename to avoid corruption.
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self._file.parent), suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._file)
        except BaseException:
            # Clean up temp file on any failure.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # ── Contract ──────────────────────────────────────────────────────────

    def store(self, text: str, *, user_id: str = "", metadata: dict[str, Any] | None = None) -> MemoryEntry:
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        entry_id = str(uuid.uuid4())
        uid = user_id or "global"
        record: dict[str, Any] = {
            "id": entry_id,
            "text": text,
            "user_id": uid,
            "metadata": metadata or {},
            "created_at": ts,
        }
        self._data.append(record)
        self._save()
        return MemoryEntry(id=entry_id, text=text, user_id=uid, metadata=metadata or {}, created_at=ts)

    def recall(self, entry_id: str) -> MemoryEntry | None:
        for rec in self._data:
            if rec.get("id") == entry_id:
                return self._to_entry(rec)
        return None

    def search(self, query: str, *, user_id: str = "", limit: int = 5) -> list[MemoryEntry]:
        keywords = set(re.findall(r"\w+", query.lower()))
        if not keywords:
            return []
        scored: list[tuple[float, dict[str, Any]]] = []
        for rec in self._data:
            if user_id and rec.get("user_id") != user_id:
                continue
            text_lower = rec.get("text", "").lower()
            words = set(re.findall(r"\w+", text_lower))
            overlap = len(keywords & words)
            if overlap > 0:
                score = overlap / len(keywords)
                scored.append((score, rec))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [self._to_entry(rec, score=sc) for sc, rec in scored[:limit]]

    def get_all(self, *, user_id: str = "") -> list[MemoryEntry]:
        if not user_id:
            return [self._to_entry(r) for r in self._data]
        return [self._to_entry(r) for r in self._data if r.get("user_id") == user_id]

    def count(self) -> int:
        return len(self._data)

    def health_check(self) -> BackendStatus:
        return BackendStatus(
            backend="local",
            healthy=True,
            entries=len(self._data),
            detail={"file": str(self._file), "search": "keyword"},
        )

    def consolidate(self) -> int:
        """Remove exact duplicates (same text + user_id)."""
        seen: set[tuple[str, str]] = set()
        unique: list[dict[str, Any]] = []
        removed = 0
        for rec in self._data:
            key = (rec.get("text", ""), rec.get("user_id", ""))
            if key in seen:
                removed += 1
            else:
                seen.add(key)
                unique.append(rec)
        if removed > 0:
            self._data = unique
            self._save()
        return removed

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _to_entry(rec: dict[str, Any], score: float = 0.0) -> MemoryEntry:
        return MemoryEntry(
            id=str(rec.get("id", "")),
            text=str(rec.get("text", "")),
            user_id=str(rec.get("user_id", "global")),
            metadata=rec.get("metadata") or {},
            created_at=str(rec.get("created_at", "")),
            score=score,
        )
