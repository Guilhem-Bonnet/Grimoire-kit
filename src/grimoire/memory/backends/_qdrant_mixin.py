"""Shared Qdrant-storage logic used by both QdrantBackend and OllamaBackend.

The mixin provides the full CRUD surface (store, recall, search, get_all,
count, consolidate, delete, update) — subclasses only need to:

1. Set ``self._client`` (qdrant_client.QdrantClient)
2. Set ``self._collection`` (str)
3. Implement ``_embed(text) -> list[float]``
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from grimoire.memory.backends.base import MemoryEntry

# Keys stored *inside* the Qdrant payload that are NOT user metadata.
_RESERVED_KEYS: frozenset[str] = frozenset({"memory", "user_id", "tags", "created_at", "updated_at"})


def _payload_to_entry(point: Any, *, score: float = 0.0) -> MemoryEntry:
    """Convert a Qdrant point (or retrieve result) to a MemoryEntry."""
    p = point.payload
    raw_tags = p.get("tags") or []
    return MemoryEntry(
        id=str(point.id),
        text=str(p.get("memory", "")),
        user_id=str(p.get("user_id", "global")),
        tags=tuple(raw_tags),
        metadata={k: v for k, v in p.items() if k not in _RESERVED_KEYS},
        created_at=str(p.get("created_at", "")),
        updated_at=str(p.get("updated_at", "")),
        score=score,
    )


class QdrantStorageMixin:
    """Mixin providing Qdrant-backed store/recall/search/get_all/count/delete/update.

    Requires subclass to have ``self._client``, ``self._collection``, and
    an ``_embed(text: str) -> list[float]`` method.
    """

    _client: Any
    _collection: str

    def _embed(self, text: str) -> list[float]:
        raise NotImplementedError

    # ── core ──────────────────────────────────────────────────────────────

    def store(self, text: str, *, user_id: str = "", tags: tuple[str, ...] = (), metadata: dict[str, Any] | None = None) -> MemoryEntry:
        from qdrant_client.models import PointStruct

        vector = self._embed(text)
        entry_id = str(uuid.uuid4())
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        uid = user_id or "global"
        payload: dict[str, Any] = {
            "memory": text,
            "user_id": uid,
            "tags": list(tags),
            "created_at": ts,
            "updated_at": "",
            **(metadata or {}),
        }
        self._client.upsert(
            collection_name=self._collection,
            points=[PointStruct(id=entry_id, vector=vector, payload=payload)],
        )
        return MemoryEntry(id=entry_id, text=text, user_id=uid, tags=tags, metadata=metadata or {}, created_at=ts)

    def recall(self, entry_id: str) -> MemoryEntry | None:
        results = self._client.retrieve(
            collection_name=self._collection,
            ids=[entry_id],
            with_payload=True,
        )
        if not results:
            return None
        return _payload_to_entry(results[0])

    def search(self, query: str, *, user_id: str = "", limit: int = 5) -> list[MemoryEntry]:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        vector = self._embed(query)
        flt = None
        if user_id:
            flt = Filter(must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))])
        response = self._client.query_points(
            collection_name=self._collection,
            query=vector,
            limit=limit,
            query_filter=flt,
        )
        return [_payload_to_entry(r, score=float(r.score)) for r in response.points]

    def get_all(self, *, user_id: str = "", offset: int = 0, limit: int | None = None) -> list[MemoryEntry]:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        flt = None
        if user_id:
            flt = Filter(must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))])

        # Scroll all pages to avoid silent truncation
        all_points: list[Any] = []
        next_offset = None
        batch_size = 256
        first = True
        while first or next_offset is not None:
            first = False
            kwargs: dict[str, Any] = {
                "collection_name": self._collection,
                "scroll_filter": flt,
                "limit": batch_size,
                "with_payload": True,
            }
            if next_offset is not None:
                kwargs["offset"] = next_offset
            points, next_offset = self._client.scroll(**kwargs)
            all_points.extend(points)

        # Apply caller-level offset/limit
        sliced = all_points[offset:] if limit is None else all_points[offset:offset + limit]
        return [_payload_to_entry(p) for p in sliced]

    def count(self) -> int:
        return int(self._client.count(collection_name=self._collection).count)

    def consolidate(self) -> int:
        """Qdrant has no built-in dedup — returns 0."""
        return 0

    # ── CRUD extensions ───────────────────────────────────────────────────

    def delete(self, entry_id: str) -> bool:
        from qdrant_client.models import PointIdsList

        existing = self._client.retrieve(
            collection_name=self._collection,
            ids=[entry_id],
        )
        if not existing:
            return False
        self._client.delete(
            collection_name=self._collection,
            points_selector=PointIdsList(points=[entry_id]),
        )
        return True

    def update(self, entry_id: str, *, text: str | None = None, tags: tuple[str, ...] | None = None, metadata: dict[str, Any] | None = None) -> MemoryEntry | None:
        existing = self._client.retrieve(
            collection_name=self._collection,
            ids=[entry_id],
            with_payload=True,
            with_vectors=True,
        )
        if not existing:
            return None

        pt = existing[0]
        payload = dict(pt.payload)
        vector = pt.vector

        if text is not None:
            payload["memory"] = text
            vector = self._embed(text)
        if tags is not None:
            payload["tags"] = list(tags)
        if metadata is not None:
            # Merge: remove old non-reserved keys, add new ones
            for k in list(payload):
                if k not in _RESERVED_KEYS:
                    del payload[k]
            payload.update(metadata)
        payload["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

        from qdrant_client.models import PointStruct

        self._client.upsert(
            collection_name=self._collection,
            points=[PointStruct(id=entry_id, vector=vector, payload=payload)],
        )
        return _payload_to_entry(type("P", (), {"id": entry_id, "payload": payload})())
