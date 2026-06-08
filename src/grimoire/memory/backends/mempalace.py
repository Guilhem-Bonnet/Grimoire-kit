"""Experimental MemPalace-compatible backend.

This backend stores verbatim memories in a ChromaDB collection using a metadata
layout compatible with the MemPalace wing / hall / room taxonomy. It does not
replace the rest of the MemPalace stack (hooks, instructions, MCP server); it
only provides a local backend and import/export bridge for Grimoire.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from grimoire.memory.backends.base import BackendStatus, MemoryBackend, MemoryEntry
from grimoire.memory.taxonomy import build_taxonomy, entry_matches_filters

_DEFAULT_COLLECTION = "mempalace_drawers"


def _require_chromadb() -> Any:
    try:
        import chromadb

        return chromadb
    except ImportError:
        raise ImportError(
            "chromadb is not installed. Run:\n  pip install grimoire-kit[mempalace]"
        ) from None


def _build_where(user_id: str = "", filters: dict[str, str] | None = None) -> dict[str, Any] | None:
    clauses: list[dict[str, Any]] = []
    if user_id:
        clauses.append({"user_id": user_id})
    for key, value in (filters or {}).items():
        if value:
            clauses.append({key: value})
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def _decode_metadata(meta: dict[str, Any]) -> tuple[tuple[str, ...], dict[str, Any]]:
    raw_tags = str(meta.get("tags_json", "[]"))
    raw_metadata = str(meta.get("metadata_json", "{}"))
    try:
        tags = tuple(json.loads(raw_tags))
    except json.JSONDecodeError:
        tags = ()
    try:
        metadata = json.loads(raw_metadata)
    except json.JSONDecodeError:
        metadata = {}
    return tags, metadata


def _make_entry(entry_id: str, document: str, metadata: dict[str, Any], *, score: float = 0.0) -> MemoryEntry:
    tags, decoded = _decode_metadata(metadata)
    return MemoryEntry(
        id=entry_id,
        text=document,
        user_id=str(metadata.get("user_id", "global")),
        tags=tags,
        metadata=decoded,
        created_at=str(metadata.get("created_at", "")),
        updated_at=str(metadata.get("updated_at", "")),
        score=score,
    )


class MemPalaceBackend(MemoryBackend):
    """ChromaDB-backed experimental backend using MemPalace-compatible metadata."""

    def __init__(
        self,
        *,
        palace_path: str,
        collection_name: str = _DEFAULT_COLLECTION,
    ) -> None:
        chromadb = _require_chromadb()
        path = (
            palace_path
            or os.environ.get("GRIMOIRE_MEMPALACE_PATH")
            or os.environ.get("MEMPALACE_PALACE_PATH")
        )
        if not path:
            raise ImportError("MemPalace path is required")
        self._palace_path = str(Path(path).expanduser().resolve())
        self._collection_name = collection_name
        self._client: Any = chromadb.PersistentClient(path=self._palace_path)
        self._collection: Any = self._client.get_or_create_collection(name=collection_name)

    @property
    def palace_path(self) -> str:
        return self._palace_path

    def _entry_id(self, text: str, metadata: dict[str, Any]) -> str:
        wing = str(metadata.get("wing", "unknown"))
        room = str(metadata.get("room", "general"))
        hall = str(metadata.get("hall", "hall_discoveries"))
        digest = hashlib.sha256(f"{wing}|{hall}|{room}|{text[:100]}".encode()).hexdigest()[:24]
        return f"drawer_{wing}_{room}_{digest}"

    @staticmethod
    def _encode_metadata(
        metadata: dict[str, Any],
        *,
        user_id: str,
        tags: tuple[str, ...],
        created_at: str,
        updated_at: str,
    ) -> dict[str, Any]:
        return {
            "user_id": user_id,
            "created_at": created_at,
            "updated_at": updated_at,
            "wing": str(metadata.get("wing", "")),
            "hall": str(metadata.get("hall", "")),
            "room": str(metadata.get("room", "")),
            "palace_key": str(metadata.get("palace_key", "")),
            "memory_type": str(metadata.get("memory_type", "")),
            "source_kind": str(metadata.get("source_kind", "memory")),
            "project_name": str(metadata.get("project_name", "")),
            "tags_json": json.dumps(list(tags), ensure_ascii=False),
            "metadata_json": json.dumps(metadata, ensure_ascii=False, sort_keys=True),
        }

    def store(
        self,
        text: str,
        *,
        user_id: str = "",
        tags: tuple[str, ...] = (),
        metadata: dict[str, Any] | None = None,
    ) -> MemoryEntry:
        uid = user_id or "global"
        meta = dict(metadata or {})
        created_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        encoded = self._encode_metadata(meta, user_id=uid, tags=tags, created_at=created_at, updated_at="")
        entry_id = self._entry_id(text, meta)
        self._collection.upsert(ids=[entry_id], documents=[text], metadatas=[encoded])
        return MemoryEntry(id=entry_id, text=text, user_id=uid, tags=tags, metadata=meta, created_at=created_at)

    def recall(self, entry_id: str) -> MemoryEntry | None:
        result = self._collection.get(ids=[entry_id], include=["documents", "metadatas"])
        if not result.get("ids"):
            return None
        return _make_entry(
            str(result["ids"][0]),
            str(result["documents"][0]),
            dict(result["metadatas"][0]),
        )

    def search(self, query: str, *, user_id: str = "", limit: int = 5) -> list[MemoryEntry]:
        return self.search_filtered(query, user_id=user_id, limit=limit, filters=None)

    def search_filtered(
        self,
        query: str,
        *,
        user_id: str = "",
        limit: int = 5,
        filters: dict[str, str] | None = None,
    ) -> list[MemoryEntry]:
        kwargs: dict[str, Any] = {
            "query_texts": [query],
            "n_results": limit,
            "include": ["documents", "metadatas", "distances"],
        }
        where = _build_where(user_id=user_id, filters=filters)
        if where:
            kwargs["where"] = where
        result = self._collection.query(**kwargs)
        docs = result.get("documents", [[]])[0]
        metas = result.get("metadatas", [[]])[0]
        ids = result.get("ids", [[]])[0]
        distances = result.get("distances", [[]])[0]

        entries: list[MemoryEntry] = []
        for entry_id, document, meta, distance in zip(ids, docs, metas, distances, strict=False):
            score = 1.0 - float(distance) if distance is not None else 0.0
            entries.append(_make_entry(str(entry_id), str(document), dict(meta), score=score))
        return entries

    def get_all(self, *, user_id: str = "", offset: int = 0, limit: int | None = None) -> list[MemoryEntry]:
        return self.get_all_filtered(user_id=user_id, offset=offset, limit=limit, filters=None)

    def get_all_filtered(
        self,
        *,
        user_id: str = "",
        offset: int = 0,
        limit: int | None = None,
        filters: dict[str, str] | None = None,
    ) -> list[MemoryEntry]:
        batch = limit or 256
        next_offset = offset
        remaining = limit
        entries: list[MemoryEntry] = []
        where = _build_where(user_id=user_id, filters=filters)

        while True:
            kwargs: dict[str, Any] = {
                "include": ["documents", "metadatas"],
                "limit": batch,
                "offset": next_offset,
            }
            if where:
                kwargs["where"] = where
            result = self._collection.get(**kwargs)
            ids = result.get("ids", [])
            if not ids:
                break
            docs = result.get("documents", [])
            metas = result.get("metadatas", [])
            for entry_id, document, meta in zip(ids, docs, metas, strict=False):
                entries.append(_make_entry(str(entry_id), str(document), dict(meta)))
            if len(ids) < batch:
                break
            next_offset += len(ids)
            if remaining is not None:
                remaining -= len(ids)
                if remaining <= 0:
                    break
                batch = remaining
        return entries[:limit] if limit is not None else entries

    def count(self) -> int:
        return int(self._collection.count())

    def health_check(self) -> BackendStatus:
        try:
            count = self.count()
            return BackendStatus(
                backend="mempalace",
                healthy=True,
                entries=count,
                detail={
                    "palace_path": self._palace_path,
                    "collection": self._collection_name,
                    "search": "semantic (chromadb)",
                },
            )
        except Exception as exc:
            return BackendStatus(
                backend="mempalace",
                healthy=False,
                entries=0,
                detail={"error": str(exc), "palace_path": self._palace_path},
            )

    def consolidate(self) -> int:
        return 0

    def delete(self, entry_id: str) -> bool:
        existing = self._collection.get(ids=[entry_id])
        if not existing.get("ids"):
            return False
        self._collection.delete(ids=[entry_id])
        return True

    def update(
        self,
        entry_id: str,
        *,
        text: str | None = None,
        tags: tuple[str, ...] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryEntry | None:
        current = self.recall(entry_id)
        if current is None:
            return None

        next_text = text if text is not None else current.text
        next_tags = tags if tags is not None else current.tags
        next_metadata = dict(current.metadata)
        if metadata is not None:
            next_metadata.update(metadata)
        updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        encoded = self._encode_metadata(
            next_metadata,
            user_id=current.user_id,
            tags=next_tags,
            created_at=current.created_at,
            updated_at=updated_at,
        )
        self._collection.upsert(ids=[entry_id], documents=[next_text], metadatas=[encoded])
        return MemoryEntry(
            id=entry_id,
            text=next_text,
            user_id=current.user_id,
            tags=next_tags,
            metadata=next_metadata,
            created_at=current.created_at,
            updated_at=updated_at,
        )

    def taxonomy(
        self,
        *,
        user_id: str = "",
        filters: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return build_taxonomy(self.get_all_filtered(user_id=user_id, filters=filters))

    def search_preview(
        self,
        query: str,
        *,
        wing: str = "",
        hall: str = "",
        room: str = "",
        user_id: str = "",
        limit: int = 5,
    ) -> list[MemoryEntry]:
        """Convenience method used by higher layers during experiments."""
        filters = {"wing": wing, "hall": hall, "room": room}
        return [
            entry
            for entry in self.search_filtered(query, user_id=user_id, limit=limit, filters=filters)
            if entry_matches_filters(entry, wing=wing, hall=hall, room=room)
        ]
