"""Memory OS layer contract and status reporting.

This module describes the intended Grimoire memory stack without pretending
that every layer is fully implemented. It gives the CLI, MCP server, and future
visualisation surfaces a stable machine-readable contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grimoire.core.config import GrimoireConfig
from grimoire.memory.backends.base import BackendStatus

MemoryLayerState = str

_STATE_READY = "ready"
_STATE_PARTIAL = "partial"
_STATE_PLANNED = "planned"
_STATE_DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class MemoryLayerStatus:
    """Status for one layer in the Grimoire Memory OS."""

    id: str
    label: str
    state: MemoryLayerState
    backend: str
    purpose: str
    implemented: bool
    gaps: tuple[str, ...] = ()
    next_actions: tuple[str, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "state": self.state,
            "backend": self.backend,
            "purpose": self.purpose,
            "implemented": self.implemented,
            "gaps": list(self.gaps),
            "next_actions": list(self.next_actions),
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class MemoryArchitectureStatus:
    """Machine-readable status of the complete memory architecture."""

    profile: str
    layers: tuple[MemoryLayerStatus, ...]
    recommendations: tuple[str, ...] = ()

    @property
    def ready_count(self) -> int:
        return sum(1 for layer in self.layers if layer.state == _STATE_READY)

    @property
    def partial_count(self) -> int:
        return sum(1 for layer in self.layers if layer.state == _STATE_PARTIAL)

    @property
    def planned_count(self) -> int:
        return sum(1 for layer in self.layers if layer.state == _STATE_PLANNED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "ready_count": self.ready_count,
            "partial_count": self.partial_count,
            "planned_count": self.planned_count,
            "layers": [layer.to_dict() for layer in self.layers],
            "recommendations": list(self.recommendations),
        }


def build_memory_architecture_status(
    config: GrimoireConfig,
    *,
    project_root: Path | None = None,
    backend_status: BackendStatus | None = None,
) -> MemoryArchitectureStatus:
    """Return the current Memory OS layer status for a project."""
    mem = config.memory
    root = (project_root or Path()).resolve()
    sidecar_path = root / "_grimoire" / "_memory" / "palace_sidecar.sqlite3"

    layers = (
        _short_term_layer(config),
        _semantic_memory_layer(config, backend_status=backend_status),
        _semantic_knowledge_layer(config, sidecar_path=sidecar_path),
        _memory_graph_layer(config, sidecar_path=sidecar_path, root=root, backend_status=backend_status),
        _code_graph_layer(config, root=root, backend_status=backend_status),
        _task_memory_layer(config, backend_status=backend_status),
        _visualization_layer(config),
    )

    recommendations = _recommendations(layers)
    return MemoryArchitectureStatus(
        profile=mem.layer_profile or "standard",
        layers=layers,
        recommendations=recommendations,
    )


def _short_term_layer(config: GrimoireConfig) -> MemoryLayerStatus:
    backend = config.memory.short_term_backend
    if backend == "none":
        return MemoryLayerStatus(
            id="short_term",
            label="Short-term working memory",
            state=_STATE_DISABLED,
            backend="none",
            purpose="Hold transient session state before durable promotion.",
            implemented=False,
        )
    if backend == "redis":
        return MemoryLayerStatus(
            id="short_term",
            label="Short-term working memory",
            state=_STATE_PLANNED,
            backend="redis",
            purpose="Replace prompt-context pressure with a hot TTL memory and pub/sub stream.",
            implemented=False,
            gaps=(
                "Redis adapter is not implemented yet.",
                "Promotion policy from hot memory to semantic memory is not implemented yet.",
            ),
            next_actions=(
                "Add a Redis short-term backend with TTL, namespaces, and session leases.",
                "Add promotion rules that persist selected hot entries into Qdrant and the graph sidecar.",
            ),
            evidence={"redis_url": config.memory.redis_url},
        )
    return MemoryLayerStatus(
        id="short_term",
        label="Short-term working memory",
        state=_STATE_PARTIAL,
        backend="sqlite/local",
        purpose="Hold transient session state before durable promotion.",
        implemented=True,
        gaps=("Current implementation is durable-local, not a low-latency distributed context cache.",),
        next_actions=("Add Redis as optional hot cache for multi-agent and multi-host sessions.",),
    )


def _semantic_memory_layer(
    config: GrimoireConfig,
    *,
    backend_status: BackendStatus | None,
) -> MemoryLayerStatus:
    backend = config.memory.backend
    semantic_backends = {"qdrant-local", "qdrant-server", "weaviate-server", "mempalace", "ollama"}
    if backend in semantic_backends:
        healthy = backend_status.healthy if backend_status is not None else None
        state = _STATE_READY if healthy is not False else _STATE_PARTIAL
        target_note = _semantic_next_action(config)
        return MemoryLayerStatus(
            id="semantic_memory",
            label="Semantic memory",
            state=state,
            backend=backend,
            purpose="Store and search durable memories by vector similarity.",
            implemented=True,
            gaps=() if state == _STATE_READY else ("Backend health has not been proven or is failing.",),
            next_actions=(target_note,),
            evidence={
                "collection_prefix": config.memory.collection_prefix,
                "embedding_model": config.memory.embedding_model,
                "qdrant_url": config.memory.qdrant_url,
                "weaviate_url": config.memory.weaviate_url,
                "weaviate_collection": config.memory.weaviate_collection or config.memory.collection_prefix,
                "migration_source_backend": config.memory.migration_source_backend,
                "migration_target_backend": config.memory.migration_target_backend,
                "migration_bundle_path": config.memory.migration_bundle_path,
            },
        )
    return MemoryLayerStatus(
        id="semantic_memory",
        label="Semantic memory",
        state=_STATE_PARTIAL,
        backend=backend,
        purpose="Store and search durable memories by vector similarity.",
        implemented=backend == "local",
        gaps=("Local memory is keyword/file oriented and does not provide semantic vector recall.",),
        next_actions=("Use weaviate-server for the target vector store, with Qdrant kept only as migration source.",),
    )


def _semantic_next_action(config: GrimoireConfig) -> str:
    if config.memory.backend == "weaviate-server":
        return "Keep Qdrant as rollback until recurring Weaviate and Neo4j verify gates are green."
    if config.memory.migration_target_backend == "weaviate-server":
        return "Export Qdrant vectors and payloads into a migration bundle before switching backend to Weaviate."
    if config.memory.backend.startswith("qdrant"):
        return "Keep Qdrant online as migration source until Weaviate parity and bundle verification pass."
    return "Validate semantic recall quality before promoting this backend."


def _semantic_knowledge_layer(config: GrimoireConfig, *, sidecar_path: Path) -> MemoryLayerStatus:
    if config.memory.knowledge_graph == "neo4j":
        has_neo4j_config = bool(config.memory.neo4j_uri)
        return MemoryLayerStatus(
            id="semantic_knowledge",
            label="Semantic knowledge",
            state=_STATE_PARTIAL if has_neo4j_config else _STATE_PLANNED,
            backend="neo4j",
            purpose="Represent extracted facts, decisions, validity windows, and contradictions in Neo4j.",
            implemented=True,
            gaps=(
                "Neo4j fact and diary write-through exists, but automatic fact extraction is not promoted yet.",
                "Fact extraction and contradiction detection still need promotion gates.",
                "Neo4j URI is not configured for this project.",
            ) if not has_neo4j_config else (
                "Neo4j fact and diary write-through exists, but automatic fact extraction is not promoted yet.",
                "Fact extraction and contradiction detection still need promotion gates.",
            ),
            next_actions=(
                "Keep SQLite sidecar as fallback while Neo4j fact/diary parity is verified.",
                "Add extraction and contradiction promotion gates before replacing sidecar-first flows.",
            ),
            evidence={
                "neo4j_uri": config.memory.neo4j_uri,
                "neo4j_database": config.memory.neo4j_database,
                "password_env": config.memory.neo4j_password_env,
                "runtime_adapter": "grimoire.memory.neo4j_graph.Neo4jMemoryGraph",
                "sidecar_path": str(sidecar_path),
                "sidecar_exists": sidecar_path.is_file(),
            },
        )
    if config.memory.knowledge_graph == "disabled":
        state = _STATE_DISABLED
        implemented = False
    else:
        state = _STATE_PARTIAL
        implemented = True
    return MemoryLayerStatus(
        id="semantic_knowledge",
        label="Semantic knowledge",
        state=state,
        backend=config.memory.knowledge_graph,
        purpose="Represent extracted facts, decisions, validity windows, and contradictions.",
        implemented=implemented,
        gaps=(
            "Fact graph exists, but fact extraction and embeddings are not automated yet.",
            "Contradiction detection is not wired into promotion or recall yet.",
        ) if implemented else (),
        next_actions=(
            "Add automatic extraction from decisions, incidents, docs, tasks, and agent diaries.",
            "Embed fact triples into Weaviate for semantic fact search.",
        ) if implemented else (),
        evidence={"sidecar_path": str(sidecar_path), "sidecar_exists": sidecar_path.is_file()},
    )


def _memory_graph_layer(
    config: GrimoireConfig,
    *,
    sidecar_path: Path,
    root: Path,
    backend_status: BackendStatus | None,
) -> MemoryLayerStatus:
    if config.memory.memory_graph == "neo4j":
        bundle_manifest = _migration_bundle_manifest(config, root=root)
        has_neo4j_config = bool(config.memory.neo4j_uri)
        has_bundle = bundle_manifest.is_file()
        graph_detail = _neo4j_graph_detail(backend_status)
        if has_neo4j_config:
            gaps = [
                "Vector object references and task/evidence/code decisions exist, but memory read paths are not fully attached yet.",
                "Diff/git/runtime extraction must still expand task links to all touched files and impacted symbols.",
            ]
            if not has_bundle:
                gaps.append("Existing memories still need a verified migration bundle backfill into Neo4j.")
            return MemoryLayerStatus(
                id="memory_graph",
                label="Semantic memory graph",
                state=_STATE_PARTIAL,
                backend="neo4j",
                purpose="Connect memories, agents, tasks, files, events, facts, and evidence in Neo4j.",
                implemented=True,
                gaps=tuple(gaps),
                next_actions=(
                    "Keep grimoire-memory-gate enforced and green after graph/vector projection changes.",
                    "Attach memory reads and richer runtime code references to graph paths.",
                    "Keep grimoire memory gate green across local task-flow and CI.",
                ),
                evidence={
                    "neo4j_uri": config.memory.neo4j_uri,
                    "neo4j_database": config.memory.neo4j_database,
                    "password_env": config.memory.neo4j_password_env,
                    "migration_bundle_path": str(bundle_manifest.parent),
                    "migration_bundle_manifest_exists": has_bundle,
                    "runtime_adapter": "grimoire.memory.neo4j_graph.Neo4jMemoryGraph",
                    "gate_command": "grimoire memory gate",
                    **graph_detail,
                },
            )
        return MemoryLayerStatus(
            id="memory_graph",
            label="Semantic memory graph",
            state=_STATE_PLANNED,
            backend="neo4j",
            purpose="Connect memories, agents, tasks, files, events, facts, and evidence in Neo4j.",
            implemented=True,
            gaps=(
                "Neo4j runtime adapter exists, but neo4j_uri is not configured for this project.",
                "Vector objects in Weaviate are not synchronized with Neo4j nodes yet.",
            ),
            next_actions=(
                "Configure Neo4j connection settings and password environment.",
                "Run the Qdrant to Weaviate/Neo4j bundle import before promotion.",
            ),
            evidence={
                "neo4j_uri": config.memory.neo4j_uri,
                "neo4j_database": config.memory.neo4j_database,
                "password_env": config.memory.neo4j_password_env,
                "migration_bundle_path": str(bundle_manifest.parent),
                "migration_bundle_manifest_exists": bundle_manifest.is_file(),
            },
        )
    if config.memory.memory_graph == "disabled":
        return MemoryLayerStatus(
            id="memory_graph",
            label="Semantic memory graph",
            state=_STATE_DISABLED,
            backend="disabled",
            purpose="Connect memories, agents, tasks, files, events, and facts.",
            implemented=False,
        )
    return MemoryLayerStatus(
        id="memory_graph",
        label="Semantic memory graph",
        state=_STATE_PARTIAL,
        backend=config.memory.memory_graph,
        purpose="Connect memories, agents, tasks, files, events, and facts.",
        implemented=True,
        gaps=(
            "Graph edges are stored as facts, but no typed edge taxonomy or graph traversal API exists yet.",
            "Graph nodes are not synchronized with semantic vectors yet.",
        ),
        next_actions=(
            "Add typed nodes and edges for memory, task, agent, file, symbol, evidence, and decision.",
            "Expose graph traversal and semantic expansion in the memory manager.",
        ),
        evidence={"sidecar_path": str(sidecar_path), "sidecar_exists": sidecar_path.is_file()},
    )


def _migration_bundle_manifest(config: GrimoireConfig, *, root: Path) -> Path:
    raw_path = config.memory.migration_bundle_path or "_grimoire/_memory/migration/weaviate-neo4j"
    bundle = Path(raw_path)
    if not bundle.is_absolute():
        bundle = root / bundle
    return bundle / "manifest.json"


def _code_graph_layer(
    config: GrimoireConfig,
    *,
    root: Path,
    backend_status: BackendStatus | None,
) -> MemoryLayerStatus:
    if config.memory.code_graph == "disabled":
        return MemoryLayerStatus(
            id="code_graph",
            label="Semantic code graph",
            state=_STATE_DISABLED,
            backend="disabled",
            purpose="Represent files, symbols, tests, dependencies, ownership, and semantic code chunks.",
            implemented=False,
        )
    graph_path = root / "_grimoire" / "_memory" / "code_graph.sqlite3"
    if config.memory.code_graph == "neo4j":
        has_neo4j_config = bool(config.memory.neo4j_uri)
        graph_detail = _neo4j_graph_detail(backend_status)
        return MemoryLayerStatus(
            id="code_graph",
            label="Semantic code graph",
            state=_STATE_PARTIAL if has_neo4j_config else _STATE_PLANNED,
            backend="neo4j",
            purpose="Represent files, symbols, tests, dependencies, ownership, and semantic code chunks.",
            implemented=True,
            gaps=(
                "AST parser and Neo4j sync command exist, but Tree-sitter coverage is not added yet.",
                "Granular vector coverage must stay verified after code changes.",
                "Task/evidence code links exist, but diff/git/runtime coverage is still shallow.",
            ) if has_neo4j_config else (
                "Code graph producer exists, but neo4j_uri is not configured for this project.",
                "Code vectors require Weaviate plus Neo4j configuration before promotion.",
            ),
            next_actions=(
                "Keep grimoire memory gate green after code graph changes.",
                "Keep file, symbol, method, test, and contract vector projections green.",
                "Extend parsing beyond Python AST with Tree-sitter where available.",
            ),
            evidence={
                "planned_path": str(graph_path),
                "neo4j_uri": config.memory.neo4j_uri,
                "sync_command": "grimoire memory graph sync-code",
                "vector_sync_command": "grimoire memory vector sync-code --granularity file,symbol,method,test,contract",
                "verify_command": "grimoire memory graph verify",
                "vector_verify_command": "grimoire memory vector verify --granularity file,symbol,method,test,contract",
                "gate_command": "grimoire memory gate",
                **graph_detail,
            },
        )
    return MemoryLayerStatus(
        id="code_graph",
        label="Semantic code graph",
        state=_STATE_PLANNED,
        backend=config.memory.code_graph,
        purpose="Represent files, symbols, tests, dependencies, ownership, and semantic code chunks.",
        implemented=False,
        gaps=(
            "No code graph indexer is wired into Grimoire Kit yet.",
            "No symbol/chunk embeddings are linked to Weaviate yet.",
        ),
        next_actions=(
            "Build a parser-backed code indexer using AST/tree-sitter where available.",
            "Store file/symbol/test nodes in Neo4j and semantic code chunks in Weaviate.",
        ),
        evidence={
            "planned_path": str(graph_path),
            "neo4j_uri": config.memory.neo4j_uri if config.memory.code_graph == "neo4j" else "",
        },
    )


def _task_memory_layer(
    config: GrimoireConfig,
    *,
    backend_status: BackendStatus | None,
) -> MemoryLayerStatus:
    if config.memory.task_memory == "disabled":
        return MemoryLayerStatus(
            id="task_memory",
            label="Kanban task memory",
            state=_STATE_DISABLED,
            backend="disabled",
            purpose="Make tasks first-class memory nodes with evidence, decisions, and recall history.",
            implemented=False,
        )
    if config.memory.task_memory == "neo4j":
        has_neo4j_config = bool(config.memory.neo4j_uri)
        graph_detail = _neo4j_graph_detail(backend_status)
        return MemoryLayerStatus(
            id="task_memory",
            label="Kanban task memory",
            state=_STATE_PARTIAL if has_neo4j_config else _STATE_PLANNED,
            backend="neo4j",
            purpose="Make tasks first-class memory nodes with evidence, decisions, and recall history.",
            implemented=True,
            gaps=(
                "MissionLedger, tasks, incidents, evidence, verdicts, decisions, and code links sync to Neo4j.",
                "Memory reads and recall history are not fully attached to task nodes yet.",
            ) if has_neo4j_config else (
                "Task memory producer exists, but neo4j_uri is not configured for this project.",
                "Task vectors require Weaviate plus Neo4j configuration before promotion.",
            ),
            next_actions=(
                "Keep grimoire memory gate green after task-flow changes.",
                "Keep Weaviate task documents synchronized from MissionLedger.",
                "Attach memory reads and richer changed-file/symbol coverage to task nodes.",
            ),
            evidence={
                "neo4j_uri": config.memory.neo4j_uri,
                "sync_command": "grimoire memory graph sync-tasks",
                "verify_command": "grimoire memory graph verify",
                "gate_command": "grimoire memory gate",
                **graph_detail,
            },
        )
    return MemoryLayerStatus(
        id="task_memory",
        label="Kanban task memory",
        state=_STATE_PLANNED,
        backend=config.memory.task_memory,
        purpose="Make tasks first-class memory nodes with evidence, decisions, and recall history.",
        implemented=False,
        gaps=(
            "Kanban state exists in the runtime game app, but it is not indexed as memory.",
            "Task transitions do not yet promote learnings or evidence into long-term memory.",
        ),
        next_actions=(
            "Index each task as a Neo4j graph node and Weaviate vector document.",
            "Link task nodes to evidence, files, agents, decisions, incidents, and memory reads.",
        ),
    )


def _visualization_layer(config: GrimoireConfig) -> MemoryLayerStatus:
    if config.memory.visualization == "disabled":
        return MemoryLayerStatus(
            id="visualization",
            label="Memory visualization",
            state=_STATE_DISABLED,
            backend="disabled",
            purpose="Expose memory layers through an explorable visual cockpit.",
            implemented=False,
        )
    return MemoryLayerStatus(
        id="visualization",
        label="Memory visualization",
        state=_STATE_PARTIAL,
        backend=config.memory.visualization,
        purpose="Expose memory layers through an explorable visual cockpit.",
        implemented=True,
        gaps=(
            "Runtime views exist for library memory and recall, but not for the full Memory OS graph.",
            "No live graph explorer is connected to the Python memory API yet.",
        ),
        next_actions=(
            "Create a Memory OS cockpit with graph, vector neighborhoods, freshness, and task overlays.",
            "Stream memory architecture status and graph deltas into the Grimoire game app.",
        ),
    )


def _recommendations(layers: tuple[MemoryLayerStatus, ...]) -> tuple[str, ...]:
    missing = {layer.id for layer in layers if layer.state in {_STATE_PARTIAL, _STATE_PLANNED}}
    recommendations: list[str] = []
    if "short_term" in missing:
        recommendations.append("Add Redis as a hot, TTL-bound memory cache, but keep ledger, Weaviate, and Neo4j as durable stores.")
    if "memory_graph" in missing:
        recommendations.append("Keep grimoire-memory-gate enforced and attach memory reads plus richer runtime code references to graph paths.")
    if "code_graph" in missing:
        recommendations.append("Keep granular code vector projections green and extend parsing beyond Python AST where useful.")
    if "task_memory" in missing:
        recommendations.append("Extend task memory with recall history, memory-read provenance, and diff/git-derived code references.")
    if "visualization" in missing:
        recommendations.append("Build the visualization from this layer contract so the UI cannot drift from runtime capabilities.")
    return tuple(recommendations)


def _neo4j_graph_detail(backend_status: BackendStatus | None) -> dict[str, Any]:
    if backend_status is None:
        return {}
    detail = backend_status.detail.get("neo4j_graph_sync_detail", {})
    return detail if isinstance(detail, dict) else {}
