"""Tests for Memory OS architecture status reporting."""

from __future__ import annotations

from pathlib import Path

from grimoire.core.config import GrimoireConfig
from grimoire.memory.architecture import build_memory_architecture_status
from grimoire.memory.backends.base import BackendStatus


def _config(memory: dict[str, str]) -> GrimoireConfig:
    return GrimoireConfig.from_dict({
        "project": {"name": "test"},
        "memory": memory,
    })


def test_reports_qdrant_semantic_memory_ready(tmp_path: Path) -> None:
    cfg = _config({
        "backend": "qdrant-server",
        "qdrant_url": "http://localhost:6333",
    })
    status = build_memory_architecture_status(
        cfg,
        project_root=tmp_path,
        backend_status=BackendStatus(backend="qdrant-server", healthy=True, entries=3),
    )

    layers = {layer.id: layer for layer in status.layers}
    assert layers["semantic_memory"].state == "ready"
    assert layers["semantic_memory"].implemented is True
    assert layers["code_graph"].state == "planned"
    assert status.planned_count >= 2


def test_reports_weaviate_semantic_memory_ready_and_neo4j_runtime_layers_partial(tmp_path: Path) -> None:
    cfg = _config({
        "backend": "weaviate-server",
        "weaviate_url": "http://localhost:8080",
        "knowledge_graph": "neo4j",
        "memory_graph": "neo4j",
        "code_graph": "neo4j",
        "task_memory": "neo4j",
        "neo4j_uri": "bolt://localhost:7687",
    })
    status = build_memory_architecture_status(
        cfg,
        project_root=tmp_path,
        backend_status=BackendStatus(backend="weaviate-server", healthy=True, entries=3),
    )

    layers = {layer.id: layer for layer in status.layers}
    assert layers["semantic_memory"].state == "ready"
    assert layers["semantic_memory"].backend == "weaviate-server"
    assert layers["semantic_knowledge"].state == "partial"
    assert layers["semantic_knowledge"].implemented is True
    assert layers["memory_graph"].backend == "neo4j"
    assert layers["memory_graph"].state == "partial"
    assert layers["memory_graph"].implemented is True
    assert layers["code_graph"].state == "partial"
    assert layers["code_graph"].implemented is True
    assert layers["task_memory"].state == "partial"
    assert layers["task_memory"].implemented is True


def test_reports_neo4j_memory_graph_partial_when_migration_bundle_exists(tmp_path: Path) -> None:
    bundle = tmp_path / "_grimoire" / "_memory" / "migration" / "weaviate-neo4j"
    bundle.mkdir(parents=True)
    (bundle / "manifest.json").write_text('{"schema_version":"grimoire.memory_migration.v1"}', encoding="utf-8")
    cfg = _config({
        "backend": "weaviate-server",
        "weaviate_url": "http://localhost:8080",
        "memory_graph": "neo4j",
        "neo4j_uri": "bolt://localhost:7687",
        "migration_bundle_path": "_grimoire/_memory/migration/weaviate-neo4j",
    })

    status = build_memory_architecture_status(
        cfg,
        project_root=tmp_path,
        backend_status=BackendStatus(backend="weaviate-server", healthy=True, entries=3),
    )

    memory_graph = {layer.id: layer for layer in status.layers}["memory_graph"]
    assert memory_graph.state == "partial"
    assert memory_graph.implemented is True
    assert memory_graph.evidence["migration_bundle_manifest_exists"] is True
    assert memory_graph.evidence["runtime_adapter"] == "grimoire.memory.neo4j_graph.Neo4jMemoryGraph"
    assert "runtime writes are not synchronized" not in " ".join(memory_graph.gaps)


def test_reports_redis_short_term_as_partial_until_health_is_checked(tmp_path: Path) -> None:
    cfg = _config({
        "backend": "local",
        "short_term_backend": "redis",
        "redis_url": "redis://localhost:6379/0",
    })
    status = build_memory_architecture_status(cfg, project_root=tmp_path)

    short_term = {layer.id: layer for layer in status.layers}["short_term"]
    assert short_term.backend == "redis"
    assert short_term.state == "partial"
    assert short_term.implemented is True
    assert "runtime health has not been checked" in short_term.gaps[0]


def test_status_is_json_serializable(tmp_path: Path) -> None:
    cfg = _config({"backend": "local"})
    payload = build_memory_architecture_status(cfg, project_root=tmp_path).to_dict()

    assert payload["profile"] == "standard"
    assert isinstance(payload["layers"], list)
    assert payload["layers"][0]["id"] == "short_term"
