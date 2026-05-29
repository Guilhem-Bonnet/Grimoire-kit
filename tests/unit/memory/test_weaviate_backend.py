"""Tests for the Weaviate memory backend reference contract."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from grimoire.memory.backends import weaviate as weaviate_module
from grimoire.memory.backends.weaviate import WeaviateBackend


class _FakeVector:
    def tolist(self) -> list[float]:
        return [0.1, 0.2]


class _FakeModel:
    def encode(self, text: str) -> _FakeVector:
        assert text
        return _FakeVector()


def _patch_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(weaviate_module, "_require_sentence_transformers", lambda: lambda _: _FakeModel())


def test_store_adds_vector_graph_references_and_backfills_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_model(monkeypatch)
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    fixed_id = uuid.UUID("11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(weaviate_module.uuid, "uuid4", lambda: fixed_id)

    def fake_request(self: WeaviateBackend, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        calls.append((method, path, payload))
        if method == "GET" and path == "/v1/schema/GrimoireKitMemory":
            return {"properties": [{"name": "memory"}, {"name": "user_id"}]}
        return {}

    monkeypatch.setattr(WeaviateBackend, "_request_json", fake_request)

    backend = WeaviateBackend(weaviate_url="http://localhost:8080", collection="grimoire-kit-memory")
    entry = backend.store("projection contract", user_id="guilhem", tags=("memory",), metadata={"room": "architecture"})

    object_call = next(call for call in calls if call[0] == "POST" and call[1] == "/v1/objects")
    payload = object_call[2] or {}
    props = payload["properties"]
    assert payload["class"] == "GrimoireKitMemory"
    assert payload["id"] == str(fixed_id)
    assert props["source_id"] == str(fixed_id)
    assert props["weaviate_id"] == str(fixed_id)
    assert props["neo4j_memory_id"] == str(fixed_id)
    assert props["vector_backend"] == "weaviate-server"
    assert entry.metadata["weaviate_collection"] == "GrimoireKitMemory"
    assert entry.metadata["weaviate_id"] == str(fixed_id)
    assert entry.metadata["neo4j_memory_id"] == str(fixed_id)

    backfilled = {call[2]["name"] for call in calls if call[0] == "POST" and call[1].endswith("/properties") and call[2]}
    assert {"weaviate_id", "neo4j_memory_id", "vector_backend"}.issubset(backfilled)


def test_recall_prefers_weaviate_reference_properties(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_model(monkeypatch)

    def fake_request(self: WeaviateBackend, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if method == "GET" and path == "/v1/schema/GrimoireMemory":
            return {"properties": [{"name": "memory"}]}
        if method == "GET" and path.startswith("/v1/objects/GrimoireMemory/"):
            return {
                "id": "object-from-additional",
                "properties": {
                    "memory": "remember graph refs",
                    "user_id": "global",
                    "tags": ["memory"],
                    "metadata_json": "{\"room\":\"architecture\"}",
                    "source_id": "mem-1",
                    "source_collection": "qdrant-old",
                    "source_point_id": "point-1",
                    "weaviate_id": "weaviate-top-level",
                    "neo4j_memory_id": "mem-1",
                    "vector_backend": "weaviate-server",
                    "created_at": "2026-05-09T00:00:00",
                },
            }
        return {}

    monkeypatch.setattr(WeaviateBackend, "_request_json", fake_request)

    backend = WeaviateBackend(weaviate_url="http://localhost:8080", collection="GrimoireMemory")
    entry = backend.recall("mem-1")

    assert entry is not None
    assert entry.id == "mem-1"
    assert entry.metadata["weaviate_id"] == "weaviate-top-level"
    assert entry.metadata["neo4j_memory_id"] == "mem-1"
    assert entry.metadata["vector_backend"] == "weaviate-server"
    assert entry.metadata["source_collection"] == "qdrant-old"


def test_upsert_creates_stable_projection_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_model(monkeypatch)
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def fake_request(self: WeaviateBackend, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        calls.append((method, path, payload))
        if method == "GET" and path == "/v1/schema/GrimoireMemory":
            return {"properties": [{"name": "memory"}]}
        if method == "GET" and path.startswith("/v1/objects/GrimoireMemory/"):
            from urllib import error

            raise error.HTTPError(path, 404, "missing", hdrs=None, fp=None)
        return {}

    monkeypatch.setattr(WeaviateBackend, "_request_json", fake_request)

    backend = WeaviateBackend(weaviate_url="http://localhost:8080", collection="GrimoireMemory")
    entry = backend.upsert(
        "code:src/app.py",
        "code projection",
        user_id="system",
        tags=("projection",),
        metadata={"projection_kind": "code_chunk"},
    )

    object_call = next(call for call in calls if call[0] == "POST" and call[1] == "/v1/objects")
    payload = object_call[2] or {}
    props = payload["properties"]
    assert entry.id == "code:src/app.py"
    assert props["source_id"] == "code:src/app.py"
    assert props["neo4j_memory_id"] == "code:src/app.py"
    assert entry.metadata["projection_kind"] == "code_chunk"


def test_update_replaces_existing_object_with_stable_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_model(monkeypatch)
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def fake_request(self: WeaviateBackend, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        calls.append((method, path, payload))
        if method == "GET" and path == "/v1/schema/GrimoireMemory":
            return {"properties": [{"name": "memory"}]}
        if method == "GET" and path.startswith("/v1/objects/GrimoireMemory/"):
            return {
                "id": "6d0e5d81-45d4-5cc2-a190-e0b69978b0d7",
                "properties": {
                    "memory": "old",
                    "user_id": "system",
                    "tags": ["projection"],
                    "metadata_json": "{\"projection_kind\":\"code_chunk\"}",
                    "source_id": "code:src/app.py",
                    "created_at": "2026-05-09T00:00:00",
                },
            }
        return {}

    monkeypatch.setattr(WeaviateBackend, "_request_json", fake_request)

    backend = WeaviateBackend(weaviate_url="http://localhost:8080", collection="GrimoireMemory")
    updated = backend.update("code:src/app.py", text="new", tags=("projection",), metadata={"projection_kind": "code_chunk"})

    assert updated is not None
    assert updated.id == "code:src/app.py"
    assert any(call[0] == "DELETE" and call[1].startswith("/v1/objects/GrimoireMemory/") for call in calls)
    post_call = next(call for call in calls if call[0] == "POST" and call[1] == "/v1/objects")
    assert (post_call[2] or {})["properties"]["source_id"] == "code:src/app.py"
