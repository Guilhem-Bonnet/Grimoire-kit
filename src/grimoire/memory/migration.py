"""Memory migration bundle utilities.

The target migration path is Qdrant -> Weaviate for vectors and Neo4j for
graph projections. The bundle format is portable JSON/JSONL so the source
backend can stay online until parity checks pass.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from urllib import error, parse, request

from grimoire.memory.backends.base import MemoryEntry

_DEFAULT_TARGET_VECTOR = "weaviate-server"
_DEFAULT_TARGET_GRAPH = "neo4j"
_DEFAULT_WEAVIATE_TIMEOUT = 30.0


@dataclass(frozen=True, slots=True)
class MigrationRecord:
    """One portable memory record preserved during vector-store migration."""

    id: str
    text: str
    user_id: str
    tags: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    vector: list[float] | dict[str, list[float]] | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    source_collection: str = ""
    source_point_id: str = ""

    @property
    def has_vector(self) -> bool:
        return self.vector is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "user_id": self.user_id,
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "vector": self.vector,
            "payload": dict(self.payload),
            "source_collection": self.source_collection,
            "source_point_id": self.source_point_id,
        }


def record_from_memory_entry(entry: MemoryEntry) -> MigrationRecord:
    """Create a migration record from the public MemoryEntry surface."""
    return MigrationRecord(
        id=entry.id,
        text=entry.text,
        user_id=entry.user_id,
        tags=entry.tags,
        metadata=dict(entry.metadata),
        created_at=entry.created_at,
        updated_at=entry.updated_at,
        vector=None,
        payload={
            "memory": entry.text,
            "user_id": entry.user_id,
            "tags": list(entry.tags),
            "created_at": entry.created_at,
            "updated_at": entry.updated_at,
            **entry.metadata,
        },
        source_collection=str(entry.metadata.get("source_collection", "")),
        source_point_id=str(entry.metadata.get("source_point_id", entry.id)),
    )


def records_from_memory_entries(entries: list[MemoryEntry]) -> list[MigrationRecord]:
    """Create migration records from backend-neutral entries."""
    return [record_from_memory_entry(entry) for entry in entries]


def records_from_qdrant_backend(backend: Any) -> list[MigrationRecord]:
    """Export records from a Qdrant-like backend while preserving vectors."""
    client = getattr(backend, "_client", None)
    collection = getattr(backend, "_collection", "")
    if client is None or not collection:
        raise TypeError("Backend does not expose Qdrant client internals")

    points: list[Any] = []
    next_offset = None
    first = True
    while first or next_offset is not None:
        first = False
        kwargs: dict[str, Any] = {
            "collection_name": collection,
            "limit": 256,
            "with_payload": True,
            "with_vectors": True,
        }
        if next_offset is not None:
            kwargs["offset"] = next_offset
        batch, next_offset = client.scroll(**kwargs)
        points.extend(batch)

    records: list[MigrationRecord] = []
    for point in points:
        payload = dict(point.payload or {})
        raw_tags = payload.get("tags") or []
        records.append(
            MigrationRecord(
                id=str(point.id),
                text=str(payload.get("memory", "")),
                user_id=str(payload.get("user_id", "global")),
                tags=tuple(str(tag) for tag in raw_tags),
                metadata={k: v for k, v in payload.items() if k not in {"memory", "user_id", "tags", "created_at", "updated_at"}},
                created_at=str(payload.get("created_at", "")),
                updated_at=str(payload.get("updated_at", "")),
                vector=_normalize_vector(point.vector),
                payload=payload,
                source_collection=collection,
                source_point_id=str(point.id),
            )
        )
    return records


def collections_from_qdrant_rest(qdrant_url: str) -> list[str]:
    """Return collection names from a Qdrant REST endpoint."""
    payload = _qdrant_request_json(qdrant_url, "GET", "/collections")
    collections = payload.get("result", {}).get("collections", [])
    return [str(item["name"]) for item in collections if isinstance(item, dict) and item.get("name")]


def records_from_qdrant_rest(qdrant_url: str, collection: str) -> list[MigrationRecord]:
    """Export records from Qdrant REST while preserving vectors and payloads."""
    records: list[MigrationRecord] = []
    offset: Any = None
    first = True
    while first or offset is not None:
        first = False
        body: dict[str, Any] = {
            "limit": 256,
            "with_payload": True,
            "with_vector": True,
        }
        if offset is not None:
            body["offset"] = offset
        payload = _qdrant_request_json(
            qdrant_url,
            "POST",
            f"/collections/{parse.quote(collection)}/points/scroll",
            body,
        )
        result = payload.get("result", {})
        points = result.get("points", [])
        for point in points:
            if not isinstance(point, dict):
                continue
            point_id = str(point.get("id", ""))
            point_payload = dict(point.get("payload") or {})
            text = _text_from_qdrant_payload(point_payload)
            tags = _tags_from_qdrant_payload(point_payload)
            records.append(
                MigrationRecord(
                    id=f"{collection}:{point_id}",
                    text=text,
                    user_id=str(point_payload.get("user_id", "global")),
                    tags=tags,
                    metadata={
                        k: v
                        for k, v in point_payload.items()
                        if k not in {"memory", "summary", "text", "content", "user_id", "tags", "topics", "created_at", "updated_at"}
                    },
                    created_at=str(point_payload.get("created_at") or point_payload.get("timestamp") or ""),
                    updated_at=str(point_payload.get("updated_at", "")),
                    vector=_normalize_vector(point.get("vector")),
                    payload=point_payload,
                    source_collection=collection,
                    source_point_id=point_id,
                )
            )
        offset = result.get("next_page_offset")
    return records


def _qdrant_request_json(
    qdrant_url: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = request.Request(  # noqa: S310 - Qdrant URL is an explicit migration endpoint.
        f"{qdrant_url.rstrip('/')}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with request.urlopen(req, timeout=30) as response:  # noqa: S310
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def _text_from_qdrant_payload(payload: dict[str, Any]) -> str:
    for key in ("memory", "summary", "text", "content"):
        value = payload.get(key)
        if value:
            return str(value)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _tags_from_qdrant_payload(payload: dict[str, Any]) -> tuple[str, ...]:
    raw = payload.get("tags") or payload.get("topics") or ()
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, (list, tuple)):
        return tuple(str(tag) for tag in raw)
    return ()


def _normalize_vector(raw: Any) -> list[float] | dict[str, list[float]] | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        normalized: dict[str, list[float]] = {}
        for key, value in raw.items():
            if isinstance(value, list):
                normalized[str(key)] = [float(v) for v in value]
        return normalized or None
    if isinstance(raw, list):
        return [float(v) for v in raw]
    return None


def weaviate_uuid(source_id: str) -> str:
    """Return a Weaviate UUID while preserving source_id as a property."""
    try:
        return str(uuid.UUID(source_id))
    except ValueError:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"grimoire-memory:{source_id}"))


def to_weaviate_object(record: MigrationRecord, *, collection: str) -> dict[str, Any]:
    """Convert a migration record to a Weaviate import object."""
    vector = record.vector
    if isinstance(vector, dict):
        vector = vector.get("default") or next(iter(vector.values()), None)
    object_id = weaviate_uuid(record.id)
    metadata = dict(record.metadata)
    metadata.setdefault("weaviate_id", object_id)
    metadata.setdefault("weaviate_collection", collection)
    metadata.setdefault("neo4j_memory_id", record.id)
    metadata.setdefault("vector_backend", "weaviate-server")
    return {
        "class": collection,
        "id": object_id,
        "properties": {
            "memory": record.text,
            "user_id": record.user_id,
            "tags": list(record.tags),
            "metadata_json": json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            "source_id": record.id,
            "source_collection": record.source_collection,
            "source_point_id": record.source_point_id,
            "weaviate_id": object_id,
            "neo4j_memory_id": record.id,
            "vector_backend": "weaviate-server",
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        },
        "vector": vector,
    }


def build_neo4j_cypher(records: list[MigrationRecord], *, weaviate_collection: str = "") -> str:
    """Build an idempotent Cypher projection for memories and tag edges."""
    lines = [
        "CREATE CONSTRAINT grimoire_memory_id IF NOT EXISTS FOR (m:GrimoireMemory) REQUIRE m.id IS UNIQUE;",
        "CREATE CONSTRAINT grimoire_tag_name IF NOT EXISTS FOR (t:GrimoireTag) REQUIRE t.name IS UNIQUE;",
        "CREATE CONSTRAINT weaviate_object_id IF NOT EXISTS FOR (w:WeaviateObject) REQUIRE w.id IS UNIQUE;",
    ]
    for record in records:
        weaviate_id = weaviate_uuid(record.id)
        metadata = dict(record.metadata)
        metadata.setdefault("weaviate_id", weaviate_id)
        if weaviate_collection:
            metadata.setdefault("weaviate_collection", weaviate_collection)
        metadata.setdefault("neo4j_memory_id", record.id)
        metadata.setdefault("vector_backend", "weaviate-server")
        lines.append(
            "MERGE (m:GrimoireMemory {id: "
            f"{json.dumps(record.id)}"
            "}) SET "
            f"m.text = {json.dumps(record.text)}, "
            f"m.user_id = {json.dumps(record.user_id)}, "
            f"m.source_collection = {json.dumps(record.source_collection)}, "
            f"m.source_point_id = {json.dumps(record.source_point_id)}, "
            f"m.metadata_json = {json.dumps(json.dumps(metadata, ensure_ascii=False, sort_keys=True))}, "
            f"m.weaviate_id = {json.dumps(weaviate_id)}, "
            "m.vector_backend = \"weaviate-server\", "
            f"m.created_at = {json.dumps(record.created_at)}, "
            f"m.updated_at = {json.dumps(record.updated_at)}, "
            f"m.has_vector = {str(record.has_vector).lower()};"
        )
        lines.append(
            f"MERGE (w:WeaviateObject {{id: {json.dumps(weaviate_id)}}}) "
            "SET w.backend = \"weaviate-server\", "
            f"w.source_id = {json.dumps(record.id)}, "
            f"w.collection = {json.dumps(weaviate_collection)}, "
            f"w.source_collection = {json.dumps(record.source_collection)}, "
            "w.updated_at = datetime() "
            f"WITH w MATCH (m:GrimoireMemory {{id: {json.dumps(record.id)}}}) "
            "MERGE (m)-[:VECTORIZED_AS]->(w) "
            "MERGE (w)-[:VECTOR_FOR]->(m);"
        )
        for tag in record.tags:
            lines.append(
                f"MERGE (t:GrimoireTag {{name: {json.dumps(tag)}}}) "
                f"WITH t MATCH (m:GrimoireMemory {{id: {json.dumps(record.id)}}}) "
                "MERGE (m)-[:TAGGED_WITH]->(t);"
            )
    return "\n".join(lines) + "\n"


def write_migration_bundle(
    bundle_dir: Path,
    records: list[MigrationRecord],
    *,
    source_backend: str,
    target_vector_backend: str = _DEFAULT_TARGET_VECTOR,
    target_graph_backend: str = _DEFAULT_TARGET_GRAPH,
    weaviate_collection: str = "GrimoireMemory",
) -> dict[str, Any]:
    """Write a portable migration bundle and return its manifest."""
    bundle_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": "grimoire.memory_migration.v1",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source_backend": source_backend,
        "target_vector_backend": target_vector_backend,
        "target_graph_backend": target_graph_backend,
        "record_count": len(records),
        "vector_count": sum(1 for record in records if record.has_vector),
        "vector_lossless": all(record.has_vector for record in records),
        "files": {
            "memories": "memories.jsonl",
            "weaviate_objects": "weaviate-objects.jsonl",
            "neo4j_cypher": "neo4j-import.cypher",
        },
    }
    (bundle_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_jsonl(bundle_dir / "memories.jsonl", [record.to_dict() for record in records])
    _write_jsonl(
        bundle_dir / "weaviate-objects.jsonl",
        [to_weaviate_object(record, collection=weaviate_collection) for record in records],
    )
    (bundle_dir / "neo4j-import.cypher").write_text(
        build_neo4j_cypher(records, weaviate_collection=weaviate_collection),
        encoding="utf-8",
    )
    return manifest


def read_migration_manifest(bundle_dir: Path) -> dict[str, Any]:
    """Read and validate the portable migration bundle manifest."""
    manifest_path = bundle_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Migration manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError(f"Invalid migration bundle manifest: {manifest_path}")
    if manifest.get("schema_version") != "grimoire.memory_migration.v1":
        raise ValueError(f"Unsupported migration bundle schema: {manifest.get('schema_version')}")
    return cast(dict[str, Any], manifest)


def load_weaviate_objects(bundle_dir: Path) -> list[dict[str, Any]]:
    """Load Weaviate objects from a portable migration bundle."""
    manifest = read_migration_manifest(bundle_dir)
    rel_path = manifest.get("files", {}).get("weaviate_objects", "weaviate-objects.jsonl")
    path = bundle_dir / str(rel_path)
    if not path.is_file():
        raise FileNotFoundError(f"Weaviate object file not found: {path}")
    objects: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            obj = json.loads(line)
            if not isinstance(obj, dict):
                raise ValueError(f"Invalid Weaviate object row in {path}")
            objects.append(obj)
    return objects


def load_migration_records(bundle_dir: Path) -> list[MigrationRecord]:
    """Load portable migration records from a bundle."""
    manifest = read_migration_manifest(bundle_dir)
    rel_path = manifest.get("files", {}).get("memories", "memories.jsonl")
    path = bundle_dir / str(rel_path)
    if not path.is_file():
        raise FileNotFoundError(f"Migration memory file not found: {path}")
    records: list[MigrationRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"Invalid migration memory row in {path}")
        raw_metadata = row.get("metadata")
        raw_payload = row.get("payload")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
        records.append(
            MigrationRecord(
                id=str(row.get("id", "")),
                text=str(row.get("text", "")),
                user_id=str(row.get("user_id", "global")),
                tags=_tuple_from_raw(row.get("tags")),
                metadata=dict(metadata),
                created_at=str(row.get("created_at", "")),
                updated_at=str(row.get("updated_at", "")),
                vector=_normalize_vector(row.get("vector")),
                payload=dict(payload),
                source_collection=str(row.get("source_collection", "")),
                source_point_id=str(row.get("source_point_id", "")),
            )
        )
    return records


def verify_migration_bundle(
    bundle_dir: Path,
    *,
    weaviate_url: str,
    collection: str,
    api_key: str = "",
    neo4j_uri: str = "",
    neo4j_user: str = "neo4j",
    neo4j_password: str = "",
    neo4j_database: str = "neo4j",
    require_neo4j: bool = True,
    timeout: float = _DEFAULT_WEAVIATE_TIMEOUT,
) -> dict[str, Any]:
    """Verify bundle parity against Weaviate and optionally Neo4j."""
    manifest = read_migration_manifest(bundle_dir)
    records = load_migration_records(bundle_dir)
    expected_ids = {record.id for record in records}
    expected_tag_edges = sum(len(record.tags) for record in records)
    issues: list[str] = []

    manifest_record_count = int(manifest.get("record_count", -1))
    manifest_vector_count = int(manifest.get("vector_count", -1))
    vector_count = sum(1 for record in records if record.has_vector)
    if manifest_record_count != len(records):
        issues.append(f"Manifest record_count={manifest_record_count} but bundle contains {len(records)} records")
    if manifest_vector_count != vector_count:
        issues.append(f"Manifest vector_count={manifest_vector_count} but bundle contains {vector_count} vectors")
    if vector_count != len(records):
        issues.append("Bundle is not vector-lossless")

    weaviate = _weaviate_bundle_stats(
        weaviate_url,
        collection,
        api_key=api_key,
        timeout=timeout,
    )
    weaviate_ids = set(weaviate["source_ids"])
    missing_weaviate = sorted(expected_ids - weaviate_ids)
    extra_weaviate = sorted(weaviate_ids - expected_ids)
    if weaviate["count"] != len(records):
        issues.append(f"Weaviate count={weaviate['count']} but bundle contains {len(records)} records")
    if missing_weaviate:
        issues.append(f"Weaviate is missing {len(missing_weaviate)} source ids")
    if extra_weaviate:
        issues.append(f"Weaviate has {len(extra_weaviate)} extra source ids")

    neo4j: dict[str, Any] = {"skipped": True}
    if require_neo4j:
        if not neo4j_uri:
            issues.append("Neo4j verification requires neo4j_uri")
        elif not neo4j_password:
            issues.append("Neo4j verification requires a password")
        else:
            neo4j = _neo4j_bundle_stats(
                uri=neo4j_uri,
                user=neo4j_user,
                password=neo4j_password,
                database=neo4j_database,
            )
            neo4j_ids = set(neo4j["source_ids"])
            missing_neo4j = sorted(expected_ids - neo4j_ids)
            extra_neo4j = sorted(neo4j_ids - expected_ids)
            if neo4j["count"] != len(records):
                issues.append(f"Neo4j count={neo4j['count']} but bundle contains {len(records)} records")
            if neo4j["tag_edges"] != expected_tag_edges:
                issues.append(f"Neo4j TAGGED_WITH edges={neo4j['tag_edges']} but bundle expects {expected_tag_edges}")
            if missing_neo4j:
                issues.append(f"Neo4j is missing {len(missing_neo4j)} source ids")
            if extra_neo4j:
                issues.append(f"Neo4j has {len(extra_neo4j)} extra source ids")

    source_collections: dict[str, int] = {}
    for record in records:
        key = record.source_collection or "unknown"
        source_collections[key] = source_collections.get(key, 0) + 1

    return {
        "ok": not issues,
        "issues": issues,
        "bundle": {
            "path": str(bundle_dir),
            "record_count": len(records),
            "vector_count": vector_count,
            "vector_lossless": vector_count == len(records),
            "source_collections": source_collections,
            "tag_edges": expected_tag_edges,
        },
        "weaviate": weaviate,
        "neo4j": neo4j,
    }


def import_weaviate_bundle(
    bundle_dir: Path,
    *,
    weaviate_url: str,
    collection: str,
    api_key: str = "",
    batch_size: int = 100,
    dry_run: bool = False,
    timeout: float = _DEFAULT_WEAVIATE_TIMEOUT,
) -> dict[str, Any]:
    """Import bundle objects into Weaviate using preserved custom vectors."""
    if not weaviate_url:
        raise ValueError("weaviate_url is required")
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")

    objects = load_weaviate_objects(bundle_dir)
    target_collection = collection or _first_weaviate_collection(objects)
    if not target_collection:
        raise ValueError("Weaviate collection is required for an empty bundle")

    import_objects = [_retarget_weaviate_object(obj, target_collection) for obj in objects]
    stats = {
        "bundle": str(bundle_dir),
        "target": weaviate_url.rstrip("/"),
        "collection": target_collection,
        "objects": len(import_objects),
        "batches": (len(import_objects) + batch_size - 1) // batch_size if import_objects else 0,
        "imported": 0,
        "dry_run": dry_run,
    }
    if dry_run:
        return stats

    ensure_weaviate_collection(
        weaviate_url,
        target_collection,
        api_key=api_key,
        timeout=timeout,
    )
    imported = 0
    for start in range(0, len(import_objects), batch_size):
        batch = import_objects[start : start + batch_size]
        response = _weaviate_request_json(
            weaviate_url,
            "POST",
            "/v1/batch/objects",
            {"objects": batch},
            api_key=api_key,
            timeout=timeout,
        )
        failures = _weaviate_batch_failures(response)
        if failures:
            raise RuntimeError(f"Weaviate import batch failed: {failures[:3]}")
        imported += len(batch)
    stats["imported"] = imported
    return stats


def ensure_weaviate_collection(
    weaviate_url: str,
    collection: str,
    *,
    api_key: str = "",
    timeout: float = _DEFAULT_WEAVIATE_TIMEOUT,
) -> None:
    """Ensure the target Weaviate collection can accept Grimoire memories."""
    schema = _weaviate_collection_schema(collection)
    try:
        existing = _weaviate_request_json(
            weaviate_url,
            "GET",
            f"/v1/schema/{parse.quote(collection)}",
            api_key=api_key,
            timeout=timeout,
        )
    except error.HTTPError as exc:
        if exc.code != 404:
            raise
        _weaviate_request_json(
            weaviate_url,
            "POST",
            "/v1/schema",
            schema,
            api_key=api_key,
            timeout=timeout,
        )
        return

    existing_names = {
        str(prop.get("name"))
        for prop in existing.get("properties", [])
        if isinstance(prop, dict) and prop.get("name")
    }
    for prop in schema["properties"]:
        if str(prop["name"]) in existing_names:
            continue
        _weaviate_request_json(
            weaviate_url,
            "POST",
            f"/v1/schema/{parse.quote(collection)}/properties",
            prop,
            api_key=api_key,
            timeout=timeout,
        )


def import_neo4j_cypher(
    cypher_path: Path,
    *,
    uri: str,
    user: str,
    password: str,
    database: str = "neo4j",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Execute the generated Neo4j Cypher projection."""
    if not cypher_path.is_file():
        raise FileNotFoundError(f"Neo4j Cypher file not found: {cypher_path}")
    statements = _read_cypher_statements(cypher_path)
    stats = {
        "cypher": str(cypher_path),
        "target": uri,
        "database": database,
        "statements": len(statements),
        "executed": 0,
        "dry_run": dry_run,
    }
    if dry_run:
        return stats
    if not uri:
        raise ValueError("neo4j uri is required")
    try:
        from neo4j import GraphDatabase
    except ImportError:
        raise ImportError("neo4j is not installed. Run:\n  pip install grimoire-kit[neo4j]") from None

    with GraphDatabase.driver(uri, auth=(user, password)) as driver:
        driver.verify_connectivity()
        for statement in statements:
            driver.execute_query(statement, database_=database)
    stats["executed"] = len(statements)
    return stats


def _weaviate_collection_schema(collection: str) -> dict[str, Any]:
    return {
        "class": collection,
        "description": "Grimoire Memory OS vector collection.",
        "vectorizer": "none",
        "properties": [
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
        ],
    }


def _first_weaviate_collection(objects: list[dict[str, Any]]) -> str:
    for obj in objects:
        collection = obj.get("class")
        if collection:
            return str(collection)
    return ""


def _retarget_weaviate_object(obj: dict[str, Any], collection: str) -> dict[str, Any]:
    retargeted = dict(obj)
    retargeted["class"] = collection
    return retargeted


def _weaviate_headers(api_key: str = "") -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _weaviate_request_json(
    weaviate_url: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    api_key: str = "",
    timeout: float = _DEFAULT_WEAVIATE_TIMEOUT,
) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = request.Request(  # noqa: S310 - Weaviate URL is an explicit migration endpoint.
        f"{weaviate_url.rstrip('/')}{path}",
        data=body,
        headers=_weaviate_headers(api_key),
        method=method,
    )
    with request.urlopen(req, timeout=timeout) as response:  # noqa: S310
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def _weaviate_bundle_stats(
    weaviate_url: str,
    collection: str,
    *,
    api_key: str = "",
    timeout: float = _DEFAULT_WEAVIATE_TIMEOUT,
) -> dict[str, Any]:
    source_ids: list[str] = []
    projection_ids: list[str] = []
    after = ""
    while True:
        params = {"class": collection, "limit": "100"}
        if after:
            params["after"] = after
        payload = _weaviate_request_json(
            weaviate_url,
            "GET",
            f"/v1/objects?{parse.urlencode(params)}",
            api_key=api_key,
            timeout=timeout,
        )
        objects = payload.get("objects", []) if isinstance(payload, dict) else []
        if not objects:
            break
        for raw in objects:
            if not isinstance(raw, dict):
                continue
            raw_props = raw.get("properties")
            props: dict[str, Any] = raw_props if isinstance(raw_props, dict) else {}
            source_id = props.get("source_id") or raw.get("id")
            if source_id:
                source_id_str = str(source_id)
                if _metadata_json_is_projection(props.get("metadata_json")):
                    projection_ids.append(source_id_str)
                else:
                    source_ids.append(source_id_str)
        after = str(objects[-1].get("id", ""))
        if len(objects) < 100:
            break
    return {
        "url": weaviate_url.rstrip("/"),
        "collection": collection,
        "count": len(source_ids),
        "projection_count": len(projection_ids),
        "source_ids": sorted(source_ids),
    }


def _metadata_json_is_projection(raw_metadata: Any) -> bool:
    try:
        metadata = json.loads(str(raw_metadata or "{}"))
    except json.JSONDecodeError:
        return False
    return bool(metadata.get("projection_group") or metadata.get("source_kind") == "projection")


def _neo4j_bundle_stats(
    *,
    uri: str,
    user: str,
    password: str,
    database: str,
) -> dict[str, Any]:
    try:
        from neo4j import GraphDatabase
    except ImportError:
        raise ImportError("neo4j is not installed. Run:\n  pip install grimoire-kit[neo4j]") from None

    with GraphDatabase.driver(uri, auth=(user, password)) as driver:
        driver.verify_connectivity()
        count_records, _, _ = driver.execute_query(
            """
            MATCH (m:GrimoireMemory)
            WHERE NOT coalesce(m.metadata_json, '') CONTAINS '"projection_group"'
            RETURN count(m) AS count
            """,
            database_=database,
        )
        id_records, _, _ = driver.execute_query(
            """
            MATCH (m:GrimoireMemory)
            WHERE NOT coalesce(m.metadata_json, '') CONTAINS '"projection_group"'
            RETURN m.id AS id ORDER BY id
            """,
            database_=database,
        )
        edge_records, _, _ = driver.execute_query(
            """
            MATCH (m:GrimoireMemory)-[r:TAGGED_WITH]->()
            WHERE NOT coalesce(m.metadata_json, '') CONTAINS '"projection_group"'
            RETURN count(r) AS count
            """,
            database_=database,
        )
        projection_records, _, _ = driver.execute_query(
            """
            MATCH (m:GrimoireMemory)
            WHERE coalesce(m.metadata_json, '') CONTAINS '"projection_group"'
            RETURN count(m) AS count
            """,
            database_=database,
        )

    return {
        "uri": uri,
        "database": database,
        "count": int(count_records[0]["count"]) if count_records else 0,
        "tag_edges": int(edge_records[0]["count"]) if edge_records else 0,
        "projection_count": int(projection_records[0]["count"]) if projection_records else 0,
        "source_ids": [str(record["id"]) for record in id_records if record.get("id")],
    }


def _weaviate_batch_failures(response: Any) -> list[Any]:
    rows: list[Any] = []
    if isinstance(response, list):
        rows.extend(response)
    elif isinstance(response, dict):
        for key in ("results", "objects"):
            value = response.get(key)
            if isinstance(value, list):
                rows.extend(value)

    failures: list[Any] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        result = row.get("result", row)
        if not isinstance(result, dict):
            continue
        status = str(result.get("status", "")).lower()
        if (status and status not in {"success", "successful"}) or result.get("errors"):
            failures.append(row)
    return failures


def _tuple_from_raw(raw: Any) -> tuple[str, ...]:
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, (list, tuple)):
        return tuple(str(item) for item in raw)
    return ()


def _read_cypher_statements(cypher_path: Path) -> list[str]:
    statements: list[str] = []
    for line in cypher_path.read_text(encoding="utf-8").splitlines():
        statement = line.strip()
        if not statement or statement.startswith("//"):
            continue
        if statement.endswith(";"):
            statement = statement[:-1].strip()
        if statement:
            statements.append(statement)
    return statements


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    payload = "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows)
    path.write_text(f"{payload}\n" if payload else "", encoding="utf-8")
