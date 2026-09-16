"""Tests for Memory OS migration bundles."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grimoire.memory.backends.base import MemoryEntry
from grimoire.memory.migration import (
    _metadata_json_is_projection,
    build_neo4j_cypher,
    entries_for_import,
    import_neo4j_cypher,
    import_weaviate_bundle,
    load_migration_records,
    load_weaviate_objects,
    migrate_backend_to_backend,
    read_previous_backend_breadcrumb,
    records_from_memory_entries,
    to_weaviate_object,
    verify_migration_bundle,
    weaviate_uuid,
    write_migration_bundle,
    write_previous_backend_breadcrumb,
)


def test_records_from_memory_entries_preserve_public_payload() -> None:
    entry = MemoryEntry(
        id="mem-1",
        text="Mission Ledger is source of truth",
        user_id="guilhem",
        tags=("decision", "ledger"),
        metadata={"wing": "architecture"},
        created_at="2026-05-08T00:00:00",
    )

    records = records_from_memory_entries([entry])

    assert records[0].id == "mem-1"
    assert records[0].text == entry.text
    assert records[0].metadata["wing"] == "architecture"
    assert records[0].has_vector is False


def test_weaviate_uuid_is_deterministic_for_non_uuid_source_id() -> None:
    assert weaviate_uuid("mem-1") == weaviate_uuid("mem-1")
    assert weaviate_uuid("mem-1") != weaviate_uuid("mem-2")


def test_projection_metadata_is_excluded_from_migration_parity_counts() -> None:
    assert _metadata_json_is_projection('{"projection_group":"code"}') is True
    assert _metadata_json_is_projection('{"source_kind":"projection"}') is True
    assert _metadata_json_is_projection('{"source_kind":"memory"}') is False


def test_to_weaviate_object_preserves_source_id_and_vector() -> None:
    record = records_from_memory_entries([
        MemoryEntry(id="mem-1", text="text", user_id="global", tags=("tag",), metadata={})
    ])[0]
    record = type(record)(
        id=record.id,
        text=record.text,
        user_id=record.user_id,
        tags=record.tags,
        metadata=record.metadata,
        created_at=record.created_at,
        updated_at=record.updated_at,
        vector=[0.1, 0.2],
        payload=record.payload,
    )

    obj = to_weaviate_object(record, collection="GrimoireMemory")

    assert obj["class"] == "GrimoireMemory"
    assert obj["properties"]["source_id"] == "mem-1"
    assert obj["properties"]["neo4j_memory_id"] == "mem-1"
    assert obj["properties"]["weaviate_id"] == weaviate_uuid("mem-1")
    assert obj["vector"] == [0.1, 0.2]


def test_build_neo4j_cypher_is_idempotent() -> None:
    record = records_from_memory_entries([
        MemoryEntry(id="mem-1", text="text", user_id="global", tags=("tag",), metadata={})
    ])[0]

    cypher = build_neo4j_cypher([record])

    assert "CREATE CONSTRAINT grimoire_memory_id IF NOT EXISTS" in cypher
    assert "CREATE CONSTRAINT weaviate_object_id IF NOT EXISTS" in cypher
    assert "MERGE (m:GrimoireMemory" in cypher
    assert "MERGE (w:WeaviateObject" in cypher
    assert "VECTORIZED_AS" in cypher
    assert "MERGE (m)-[:TAGGED_WITH]->(t)" in cypher


def test_write_migration_bundle(tmp_path: Path) -> None:
    records = records_from_memory_entries([
        MemoryEntry(id="mem-1", text="text", user_id="global", tags=("tag",), metadata={})
    ])

    manifest = write_migration_bundle(
        tmp_path,
        records,
        source_backend="qdrant-server",
        weaviate_collection="GrimoireMemory",
    )

    assert manifest["record_count"] == 1
    assert manifest["vector_lossless"] is False
    assert (tmp_path / "manifest.json").is_file()
    assert (tmp_path / "memories.jsonl").is_file()
    assert (tmp_path / "weaviate-objects.jsonl").is_file()
    assert (tmp_path / "neo4j-import.cypher").is_file()
    loaded = json.loads((tmp_path / "manifest.json").read_text())
    assert loaded["target_vector_backend"] == "weaviate-server"


def test_load_weaviate_objects_from_bundle(tmp_path: Path) -> None:
    records = records_from_memory_entries([
        MemoryEntry(id="mem-1", text="text", user_id="global", tags=("tag",), metadata={})
    ])
    write_migration_bundle(tmp_path, records, source_backend="qdrant-server", weaviate_collection="GrimoireMemory")

    objects = load_weaviate_objects(tmp_path)

    assert len(objects) == 1
    assert objects[0]["class"] == "GrimoireMemory"
    assert objects[0]["properties"]["source_id"] == "mem-1"


def test_load_migration_records_from_bundle(tmp_path: Path) -> None:
    records = records_from_memory_entries([
        MemoryEntry(id="mem-1", text="text", user_id="global", tags=("tag",), metadata={})
    ])
    write_migration_bundle(tmp_path, records, source_backend="qdrant-server", weaviate_collection="GrimoireMemory")

    loaded = load_migration_records(tmp_path)

    assert len(loaded) == 1
    assert loaded[0].id == "mem-1"
    assert loaded[0].tags == ("tag",)


def test_import_weaviate_bundle_dry_run(tmp_path: Path) -> None:
    records = records_from_memory_entries([
        MemoryEntry(id="mem-1", text="text", user_id="global", tags=("tag",), metadata={})
    ])
    write_migration_bundle(tmp_path, records, source_backend="qdrant-server", weaviate_collection="GrimoireMemory")

    stats = import_weaviate_bundle(
        tmp_path,
        weaviate_url="http://localhost:8080",
        collection="GrimoireMemory",
        dry_run=True,
    )

    assert stats["objects"] == 1
    assert stats["imported"] == 0
    assert stats["dry_run"] is True


def test_import_weaviate_bundle_posts_batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    records = records_from_memory_entries([
        MemoryEntry(id="mem-1", text="text", user_id="global", tags=("tag",), metadata={})
    ])
    records[0] = type(records[0])(
        id=records[0].id,
        text=records[0].text,
        user_id=records[0].user_id,
        tags=records[0].tags,
        metadata=records[0].metadata,
        created_at=records[0].created_at,
        updated_at=records[0].updated_at,
        vector=[0.1, 0.2],
        payload=records[0].payload,
    )
    write_migration_bundle(tmp_path, records, source_backend="qdrant-server", weaviate_collection="GrimoireMemory")
    calls: list[tuple[str, str, dict | None]] = []

    def fake_request(
        weaviate_url: str,
        method: str,
        path: str,
        payload: dict | None = None,
        **_: object,
    ) -> dict:
        calls.append((method, path, payload))
        if method == "GET":
            return {
                "properties": [
                    {"name": "memory"},
                    {"name": "user_id"},
                    {"name": "tags"},
                    {"name": "metadata_json"},
                    {"name": "source_id"},
                    {"name": "source_collection"},
                    {"name": "source_point_id"},
                    {"name": "created_at"},
                    {"name": "updated_at"},
                ]
            }
        return [{"result": {"status": "SUCCESS"}}]

    monkeypatch.setattr("grimoire.memory.migration._weaviate_request_json", fake_request)

    stats = import_weaviate_bundle(
        tmp_path,
        weaviate_url="http://localhost:8080",
        collection="GrimoireMemory",
        batch_size=10,
    )

    assert stats["imported"] == 1
    assert any(method == "POST" and path == "/v1/batch/objects" for method, path, _ in calls)


def test_verify_migration_bundle_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    records = records_from_memory_entries([
        MemoryEntry(id="mem-1", text="text", user_id="global", tags=("tag",), metadata={})
    ])
    records[0] = type(records[0])(
        id=records[0].id,
        text=records[0].text,
        user_id=records[0].user_id,
        tags=records[0].tags,
        metadata=records[0].metadata,
        created_at=records[0].created_at,
        updated_at=records[0].updated_at,
        vector=[0.1, 0.2],
        payload=records[0].payload,
    )
    write_migration_bundle(tmp_path, records, source_backend="qdrant-server", weaviate_collection="GrimoireMemory")

    monkeypatch.setattr(
        "grimoire.memory.migration._weaviate_bundle_stats",
        lambda *args, **kwargs: {
            "url": "http://localhost:8080",
            "collection": "GrimoireMemory",
            "count": 1,
            "source_ids": ["mem-1"],
        },
    )
    monkeypatch.setattr(
        "grimoire.memory.migration._neo4j_bundle_stats",
        lambda **kwargs: {
            "uri": "neo4j://localhost:7687",
            "database": "neo4j",
            "count": 1,
            "tag_edges": 1,
            "source_ids": ["mem-1"],
        },
    )

    stats = verify_migration_bundle(
        tmp_path,
        weaviate_url="http://localhost:8080",
        collection="GrimoireMemory",
        neo4j_uri="neo4j://localhost:7687",
        neo4j_password="secret",
    )

    assert stats["ok"] is True
    assert stats["issues"] == []


def test_verify_migration_bundle_reports_missing_weaviate_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    records = records_from_memory_entries([
        MemoryEntry(id="mem-1", text="text", user_id="global", tags=(), metadata={})
    ])
    write_migration_bundle(tmp_path, records, source_backend="qdrant-server", weaviate_collection="GrimoireMemory")

    monkeypatch.setattr(
        "grimoire.memory.migration._weaviate_bundle_stats",
        lambda *args, **kwargs: {
            "url": "http://localhost:8080",
            "collection": "GrimoireMemory",
            "count": 0,
            "source_ids": [],
        },
    )

    stats = verify_migration_bundle(
        tmp_path,
        weaviate_url="http://localhost:8080",
        collection="GrimoireMemory",
        require_neo4j=False,
    )

    assert stats["ok"] is False
    assert any("Weaviate is missing" in issue for issue in stats["issues"])


def test_import_neo4j_cypher_dry_run(tmp_path: Path) -> None:
    cypher = tmp_path / "neo4j-import.cypher"
    cypher.write_text(
        "CREATE CONSTRAINT grimoire_memory_id IF NOT EXISTS FOR (m:GrimoireMemory) REQUIRE m.id IS UNIQUE;\n"
        "MERGE (m:GrimoireMemory {id: \"mem-1\"});\n",
        encoding="utf-8",
    )

    stats = import_neo4j_cypher(
        cypher,
        uri="neo4j://localhost:7687",
        user="neo4j",
        password="secret",
        dry_run=True,
    )

    assert stats["statements"] == 2
    assert stats["executed"] == 0


# ── #527 — lexical -> vector migration without a manual export/import ────────


def _entry(entry_id: str, text: str) -> MemoryEntry:
    return MemoryEntry(id=entry_id, text=text, user_id="guilhem", tags=(), metadata={})


class TestEntriesForImport:
    def test_never_carries_a_vector(self) -> None:
        payload = entries_for_import([_entry("mem-1", "hello")])

        assert payload == [{"text": "hello", "user_id": "guilhem", "tags": [], "metadata": {}}]
        assert "vector" not in payload[0]


class TestMigrateBackendToBackend:
    def test_dry_run_counts_without_calling_store_many(self) -> None:
        entries = [_entry("mem-1", "a"), _entry("mem-2", "b")]

        result = migrate_backend_to_backend(
            entries, source_backend="lexical", target_backend="weaviate-server", store_many=None, dry_run=True,
        )

        assert result.entries == 2
        assert result.imported == 0
        assert result.dry_run is True

    def test_apply_writes_every_entry_through_store_many(self) -> None:
        entries = [_entry("mem-1", "a"), _entry("mem-2", "b")]
        written: list[list[dict]] = []

        def fake_store_many(payload: list[dict]) -> list[str]:
            written.append(payload)
            return ["written"] * len(payload)

        result = migrate_backend_to_backend(
            entries, source_backend="lexical", target_backend="weaviate-server",
            store_many=fake_store_many, dry_run=False,
        )

        assert result.imported == 2
        assert written == [[
            {"text": "a", "user_id": "guilhem", "tags": [], "metadata": {}},
            {"text": "b", "user_id": "guilhem", "tags": [], "metadata": {}},
        ]]

    def test_apply_without_store_many_is_a_programming_error(self) -> None:
        with pytest.raises(ValueError, match="store_many"):
            migrate_backend_to_backend(
                [_entry("mem-1", "a")], source_backend="lexical", target_backend="weaviate-server",
                store_many=None, dry_run=False,
            )

    def test_empty_source_never_calls_store_many(self) -> None:
        calls = []
        result = migrate_backend_to_backend(
            [], source_backend="lexical", target_backend="weaviate-server",
            store_many=lambda payload: calls.append(payload) or [],
            dry_run=False,
        )

        assert result.entries == 0
        assert result.imported == 0
        assert calls == []


class TestPreviousBackendBreadcrumb:
    def test_round_trip(self, tmp_path: Path) -> None:
        write_previous_backend_breadcrumb(tmp_path, "lexical")

        assert read_previous_backend_breadcrumb(tmp_path) == "lexical"

    def test_missing_breadcrumb_reads_as_empty(self, tmp_path: Path) -> None:
        assert read_previous_backend_breadcrumb(tmp_path) == ""

    def test_corrupt_breadcrumb_reads_as_empty_not_a_crash(self, tmp_path: Path) -> None:
        path = tmp_path / "_grimoire" / "_memory" / "migration" / "previous-backend.json"
        path.parent.mkdir(parents=True)
        path.write_text("not json", encoding="utf-8")

        assert read_previous_backend_breadcrumb(tmp_path) == ""

    def test_write_is_best_effort_on_an_unwritable_root(self, tmp_path: Path) -> None:
        readonly_root = tmp_path / "readonly"
        readonly_root.mkdir(mode=0o500)
        try:
            write_previous_backend_breadcrumb(readonly_root, "lexical")  # must not raise
        finally:
            readonly_root.chmod(0o700)
