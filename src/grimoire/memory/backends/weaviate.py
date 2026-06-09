"""Weaviate memory backend.

This backend uses Weaviate as the durable vector store for Grimoire memories.
It keeps embeddings under Grimoire control so Qdrant migration bundles can
preserve vectors without re-embedding.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib import error, parse, request

from grimoire.memory.backends.base import BackendStatus, MemoryBackend, MemoryEntry

_DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
_DEFAULT_COLLECTION = "GrimoireMemory"
_BATCH_SIZE = 100
_COLLECTION_PROPERTIES = (
    {"name": "memory", "dataType": ["text"]},
    {"name": "user_id", "dataType": ["text"]},
    {"name": "tags", "dataType": ["text[]"]},
    {"name": "metadata_json", "dataType": ["text"]},
    {"name": "source_id", "dataType": ["text"]},
    {"name": "source_collection", "dataType": ["text"]},
    {"name": "source_point_id", "dataType": ["text"]},
    {"name": "weaviate_id", "dataType": ["text"]},
    {"name": "neo4j_memory_id", "dataType": ["text"]},
    {"name": "vector_backend", "dataType": ["text"]},
    {"name": "created_at", "dataType": ["text"]},
    {"name": "updated_at", "dataType": ["text"]},
)


def _require_sentence_transformers() -> Any:
    """Import SentenceTransformer, raising a clear error if missing."""
    try:
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer
    except ImportError:
        raise ImportError(
            "sentence-transformers is not installed. Run:\n  pip install grimoire-kit[weaviate]"
        ) from None


def normalize_weaviate_collection(raw: str) -> str:
    """Return a Weaviate-safe collection name from a Grimoire collection prefix."""
    if not raw.strip():
        return _DEFAULT_COLLECTION
    parts = re.split(r"[^A-Za-z0-9]+", raw)
    name = "".join(part[:1].upper() + part[1:] for part in parts if part)
    if not name:
        return _DEFAULT_COLLECTION
    if not name[0].isalpha():
        name = f"Grimoire{name}"
    return name


@dataclass(frozen=True, slots=True)
class _WeaviateObject:
    id: str
    properties: dict[str, Any]
    vector: list[float] | None = None


def _properties_to_entry(obj: _WeaviateObject, *, score: float = 0.0) -> MemoryEntry:
    props = obj.properties
    raw_tags = props.get("tags") or []
    metadata_json = props.get("metadata_json") or "{}"
    try:
        metadata = json.loads(str(metadata_json))
    except json.JSONDecodeError:
        metadata = {"metadata_json": metadata_json}
    if props.get("weaviate_id"):
        metadata.setdefault("weaviate_id", props["weaviate_id"])
    else:
        metadata.setdefault("weaviate_id", obj.id)
    metadata.setdefault("neo4j_memory_id", props.get("neo4j_memory_id") or props.get("source_id") or obj.id)
    if props.get("vector_backend"):
        metadata.setdefault("vector_backend", props["vector_backend"])
    if props.get("source_collection"):
        metadata.setdefault("source_collection", props["source_collection"])
    if props.get("source_point_id"):
        metadata.setdefault("source_point_id", props["source_point_id"])
    return MemoryEntry(
        id=str(props.get("source_id") or obj.id),
        text=str(props.get("memory", "")),
        user_id=str(props.get("user_id", "global")),
        tags=tuple(str(tag) for tag in raw_tags),
        metadata=metadata,
        created_at=str(props.get("created_at", "")),
        updated_at=str(props.get("updated_at", "")),
        score=score,
    )


class WeaviateBackend(MemoryBackend):
    """Weaviate vector backend using the REST and GraphQL APIs."""

    def __init__(
        self,
        *,
        embedding_model: str = _DEFAULT_MODEL,
        collection: str = _DEFAULT_COLLECTION,
        weaviate_url: str,
        api_key_env: str = "GRIMOIRE_WEAVIATE_API_KEY",
        timeout: float = 10.0,
    ) -> None:
        if not weaviate_url:
            raise ValueError("weaviate_url is required for WeaviateBackend")
        sentence_transformer_cls = _require_sentence_transformers()
        self._model: Any = sentence_transformer_cls(embedding_model)
        self._embedding_model_name = embedding_model
        self._collection = normalize_weaviate_collection(collection)
        self._base_url = weaviate_url.rstrip("/")
        self._timeout = timeout
        self._api_key = os.environ.get(api_key_env, "")
        self._ensure_collection()

    def _embed(self, text: str) -> list[float]:
        vec: Any = self._model.encode(text)
        return vec.tolist()  # type: ignore[no-any-return]

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _request_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        req = request.Request(  # noqa: S310 - base URL comes from MemoryConfig and is restricted by validation.
            f"{self._base_url}{path}",
            data=body,
            headers=self._headers(),
            method=method,
        )
        with request.urlopen(req, timeout=self._timeout) as response:  # noqa: S310
            raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}

    def _ensure_collection(self) -> None:
        try:
            existing = self._request_json("GET", f"/v1/schema/{parse.quote(self._collection)}")
            self._ensure_collection_properties(existing)
            return
        except error.HTTPError as exc:
            if exc.code != 404:
                raise
        schema = {
            "class": self._collection,
            "description": "Grimoire Memory OS vector collection.",
            "vectorizer": "none",
            "properties": list(_COLLECTION_PROPERTIES),
        }
        self._request_json("POST", "/v1/schema", schema)

    def _ensure_collection_properties(self, existing_schema: dict[str, Any]) -> None:
        existing = {
            str(prop.get("name"))
            for prop in existing_schema.get("properties", [])
            if isinstance(prop, dict) and prop.get("name")
        }
        for prop in _COLLECTION_PROPERTIES:
            if prop["name"] not in existing:
                self._request_json("POST", f"/v1/schema/{parse.quote(self._collection)}/properties", dict(prop))

    def _object_id(self, entry_id: str) -> str:
        try:
            return str(uuid.UUID(entry_id))
        except ValueError:
            return str(uuid.uuid5(uuid.NAMESPACE_URL, f"grimoire-memory:{entry_id}"))

    def _entry_to_properties(
        self,
        text: str,
        *,
        entry_id: str,
        user_id: str,
        tags: tuple[str, ...],
        metadata: dict[str, Any] | None,
        created_at: str,
        updated_at: str = "",
    ) -> dict[str, Any]:
        object_id = self._object_id(entry_id)
        metadata_payload = dict(metadata or {})
        metadata_payload.setdefault("weaviate_id", object_id)
        metadata_payload.setdefault("weaviate_collection", self._collection)
        metadata_payload.setdefault("neo4j_memory_id", entry_id)
        metadata_payload.setdefault("vector_backend", "weaviate-server")
        return {
            "memory": text,
            "user_id": user_id or "global",
            "tags": list(tags),
            "metadata_json": json.dumps(metadata_payload, ensure_ascii=False, sort_keys=True),
            "source_id": entry_id,
            "weaviate_id": object_id,
            "neo4j_memory_id": entry_id,
            "vector_backend": "weaviate-server",
            "created_at": created_at,
            "updated_at": updated_at,
        }

    def store(
        self,
        text: str,
        *,
        user_id: str = "",
        tags: tuple[str, ...] = (),
        metadata: dict[str, Any] | None = None,
    ) -> MemoryEntry:
        entry_id = str(uuid.uuid4())
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        props = self._entry_to_properties(
            text,
            entry_id=entry_id,
            user_id=user_id or "global",
            tags=tags,
            metadata=metadata,
            created_at=ts,
        )
        self._request_json(
            "POST",
            "/v1/objects",
            {
                "class": self._collection,
                "id": self._object_id(entry_id),
                "properties": props,
                "vector": self._embed(text),
            },
        )
        return MemoryEntry(
            id=entry_id,
            text=text,
            user_id=user_id or "global",
            tags=tags,
            metadata=json.loads(props["metadata_json"]),
            created_at=ts,
        )

    def upsert(
        self,
        entry_id: str,
        text: str,
        *,
        user_id: str = "",
        tags: tuple[str, ...] = (),
        metadata: dict[str, Any] | None = None,
    ) -> MemoryEntry:
        existing = self.recall(entry_id)
        if existing is not None:
            updated = self.update(entry_id, text=text, tags=tags, metadata=metadata)
            if updated is None:
                raise RuntimeError(f"Weaviate upsert failed for existing entry: {entry_id}")
            return updated

        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        props = self._entry_to_properties(
            text,
            entry_id=entry_id,
            user_id=user_id or "global",
            tags=tags,
            metadata=metadata,
            created_at=ts,
        )
        self._request_json(
            "POST",
            "/v1/objects",
            {
                "class": self._collection,
                "id": self._object_id(entry_id),
                "properties": props,
                "vector": self._embed(text),
            },
        )
        return MemoryEntry(
            id=entry_id,
            text=text,
            user_id=user_id or "global",
            tags=tags,
            metadata=json.loads(props["metadata_json"]),
            created_at=ts,
        )

    def recall(self, entry_id: str) -> MemoryEntry | None:
        try:
            payload = self._request_json("GET", f"/v1/objects/{parse.quote(self._collection)}/{self._object_id(entry_id)}")
        except error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise
        obj = _WeaviateObject(id=str(payload.get("id", "")), properties=dict(payload.get("properties") or {}))
        return _properties_to_entry(obj)

    def search(self, query: str, *, user_id: str = "", limit: int = 5) -> list[MemoryEntry]:
        vector = self._embed(query)
        where = ""
        if user_id:
            where = f', where: {{path: ["user_id"], operator: Equal, valueText: {json.dumps(user_id)}}}'
        graphql = f"""
        {{
          Get {{
            {self._collection}(nearVector: {{vector: {json.dumps(vector)}}}, limit: {limit}{where}) {{
              memory
              user_id
              tags
              metadata_json
              source_id
              source_collection
              source_point_id
              weaviate_id
              neo4j_memory_id
              vector_backend
              created_at
              updated_at
              _additional {{ id distance certainty }}
            }}
          }}
        }}
        """
        payload = self._request_json("POST", "/v1/graphql", {"query": graphql})
        rows = payload.get("data", {}).get("Get", {}).get(self._collection, [])
        results: list[MemoryEntry] = []
        for row in rows:
            additional = row.pop("_additional", {}) if isinstance(row, dict) else {}
            certainty = additional.get("certainty", 0.0) if isinstance(additional, dict) else 0.0
            object_id = str(additional.get("id", "")) if isinstance(additional, dict) else ""
            results.append(_properties_to_entry(_WeaviateObject(id=object_id, properties=row), score=float(certainty or 0.0)))
        return results

    def get_all(self, *, user_id: str = "", offset: int = 0, limit: int | None = None) -> list[MemoryEntry]:
        entries: list[MemoryEntry] = []
        after = ""
        while True:
            params = {"class": self._collection, "limit": str(_BATCH_SIZE)}
            if after:
                params["after"] = after
            qs = parse.urlencode(params)
            payload = self._request_json("GET", f"/v1/objects?{qs}")
            objects = payload.get("objects") or []
            if not objects:
                break
            for raw in objects:
                obj = _WeaviateObject(id=str(raw.get("id", "")), properties=dict(raw.get("properties") or {}))
                entry = _properties_to_entry(obj)
                if not user_id or entry.user_id == user_id:
                    entries.append(entry)
            after = str(objects[-1].get("id", ""))
            if len(objects) < _BATCH_SIZE:
                break
        return entries[offset:] if limit is None else entries[offset:offset + limit]

    def count(self) -> int:
        return len(self.get_all())

    def health_check(self) -> BackendStatus:
        try:
            self._request_json("GET", "/v1/meta")
            return BackendStatus(
                backend="weaviate-server",
                healthy=True,
                entries=self.count(),
                detail={
                    "url": self._base_url,
                    "collection": self._collection,
                    "embedding_model": self._embedding_model_name,
                    "search": "semantic vector search with Grimoire-controlled embeddings",
                },
            )
        except Exception as exc:
            return BackendStatus(
                backend="weaviate-server",
                healthy=False,
                entries=0,
                detail={"error": str(exc), "url": self._base_url, "collection": self._collection},
            )

    def consolidate(self) -> int:
        return 0

    def delete(self, entry_id: str) -> bool:
        existing = self.recall(entry_id)
        if existing is None:
            return False
        self._request_json("DELETE", f"/v1/objects/{parse.quote(self._collection)}/{self._object_id(entry_id)}")
        return True

    def update(
        self,
        entry_id: str,
        *,
        text: str | None = None,
        tags: tuple[str, ...] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryEntry | None:
        existing = self.recall(entry_id)
        if existing is None:
            return None
        new_text = text if text is not None else existing.text
        new_tags = tags if tags is not None else existing.tags
        new_metadata = metadata if metadata is not None else existing.metadata
        updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        props = self._entry_to_properties(
            new_text,
            entry_id=entry_id,
            user_id=existing.user_id,
            tags=new_tags,
            metadata=new_metadata,
            created_at=existing.created_at,
            updated_at=updated_at,
        )
        object_id = self._object_id(entry_id)
        self._request_json("DELETE", f"/v1/objects/{parse.quote(self._collection)}/{object_id}")
        self._request_json(
            "POST",
            "/v1/objects",
            {"class": self._collection, "id": object_id, "properties": props, "vector": self._embed(new_text)},
        )
        return MemoryEntry(
            id=entry_id,
            text=new_text,
            user_id=existing.user_id,
            tags=new_tags,
            metadata=json.loads(props["metadata_json"]),
            created_at=existing.created_at,
            updated_at=updated_at,
        )
