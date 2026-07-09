"""Unified memory API — routes to the right backend based on config.

Usage::

    from grimoire.memory.manager import MemoryManager
    from grimoire.core.config import GrimoireConfig

    cfg = GrimoireConfig.from_yaml(Path("project-context.yaml"))
    mgr = MemoryManager.from_config(cfg, project_root=Path("."))
    mgr.store("important fact")
    results = mgr.search("important")
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, ClassVar, cast

from grimoire.core.config import GrimoireConfig
from grimoire.core.exceptions import GrimoireMemoryError
from grimoire.memory.backends.base import BackendStatus, MemoryBackend, MemoryEntry
from grimoire.memory.hot import HotMemoryStatus, RedisHotMemory
from grimoire.memory.sidecar import DiaryRecord, KnowledgeFact, MemorySidecar
from grimoire.memory.taxonomy import build_taxonomy, entry_matches_filters, normalize_palace_metadata

logger = logging.getLogger(__name__)

#: Typed collections of the agent memory protocol (parity with mem0-bridge).
MEMORY_TYPES: tuple[str, ...] = (
    "shared-context",
    "decisions",
    "agent-learnings",
    "failures",
    "stories",
)

# Backend identifiers matching MemoryConfig.backend values
_BACKEND_LOCAL = "local"
_BACKEND_LEXICAL = "lexical"
_BACKEND_TANTIVY = "tantivy-local"
_BACKEND_QDRANT_LOCAL = "qdrant-local"
_BACKEND_QDRANT_SERVER = "qdrant-server"
_BACKEND_WEAVIATE_SERVER = "weaviate-server"
_BACKEND_MEMPALACE = "mempalace"
_BACKEND_OLLAMA = "ollama"
_BACKEND_AUTO = "auto"

# Server-backed vector backends that benefit from a lexical companion index.
_VECTOR_BACKENDS = frozenset({
    _BACKEND_QDRANT_LOCAL, _BACKEND_QDRANT_SERVER, _BACKEND_WEAVIATE_SERVER, _BACKEND_OLLAMA,
})


def _best_local_backend() -> str:
    """FTS5 BM25 when the bundled SQLite supports it, keyword JSON otherwise."""
    from grimoire.memory.backends.lexical import fts5_available

    return _BACKEND_LEXICAL if fts5_available() else _BACKEND_LOCAL


def _resolve_auto(config: GrimoireConfig) -> str:
    """Determine the best backend when ``backend: auto``."""
    mem = config.memory
    if mem.retrieval_mode == "lexical" or not mem.vector_database:
        return _best_local_backend()
    if mem.weaviate_url:
        return _BACKEND_WEAVIATE_SERVER
    if mem.ollama_url:
        return _BACKEND_OLLAMA
    if mem.qdrant_url:
        return _BACKEND_QDRANT_SERVER
    return _best_local_backend()


def _create_backend(config: GrimoireConfig, project_root: Path | None = None) -> MemoryBackend:
    """Instantiate the right backend from config.

    Parameters
    ----------
    config :
        Validated Grimoire configuration.
    project_root :
        Explicit project root. When ``None``, falls back to cwd (legacy
        behaviour) but this is discouraged — always pass a root.
    """
    mem = config.memory
    backend_id = mem.backend if mem.backend != _BACKEND_AUTO else _resolve_auto(config)
    root = (project_root or Path()).resolve()

    if backend_id == _BACKEND_LOCAL:
        from grimoire.memory.backends.local import LocalMemoryBackend

        memory_dir = root / "_grimoire" / "_memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        return LocalMemoryBackend(memory_dir / f"{mem.collection_prefix}.json")

    if backend_id == _BACKEND_LEXICAL:
        return _create_lexical_backend(mem.collection_prefix, root)

    if backend_id == _BACKEND_TANTIVY:
        from grimoire.memory.backends.tantivy_local import TantivyMemoryBackend

        memory_dir = root / "_grimoire" / "_memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        return TantivyMemoryBackend(memory_dir / f"{mem.collection_prefix}_tantivy")

    if backend_id == _BACKEND_QDRANT_LOCAL:
        from grimoire.memory.backends.qdrant import QdrantBackend

        kwargs: dict[str, Any] = {"collection": mem.collection_prefix}
        if mem.embedding_model:
            kwargs["embedding_model"] = mem.embedding_model
        return QdrantBackend(**kwargs)

    if backend_id == _BACKEND_QDRANT_SERVER:
        from grimoire.memory.backends.qdrant import QdrantBackend

        kwargs = {
            "collection": mem.collection_prefix,
            "qdrant_url": mem.qdrant_url,
        }
        if mem.embedding_model:
            kwargs["embedding_model"] = mem.embedding_model
        return QdrantBackend(**kwargs)

    if backend_id == _BACKEND_WEAVIATE_SERVER:
        from grimoire.memory.backends.weaviate import WeaviateBackend

        kwargs = {
            "collection": mem.weaviate_collection or mem.collection_prefix,
            "weaviate_url": mem.weaviate_url,
            "api_key_env": mem.weaviate_api_key_env,
        }
        if mem.embedding_model:
            kwargs["embedding_model"] = mem.embedding_model
        return WeaviateBackend(**kwargs)

    if backend_id == _BACKEND_MEMPALACE:
        from grimoire.memory.backends.mempalace import MemPalaceBackend

        palace_path = mem.mempalace_path or str(root / "_grimoire" / "_memory" / "mempalace")
        return MemPalaceBackend(palace_path=palace_path)

    if backend_id == _BACKEND_OLLAMA:
        from grimoire.memory.backends.ollama import OllamaBackend

        kwargs = {"collection": mem.collection_prefix}
        if mem.ollama_url:
            kwargs["ollama_url"] = mem.ollama_url
        if mem.qdrant_url:
            kwargs["qdrant_url"] = mem.qdrant_url
        if mem.embedding_model:
            kwargs["embedding_model"] = mem.embedding_model
        return OllamaBackend(**kwargs)

    raise GrimoireMemoryError(f"Unknown memory backend: {backend_id}")


def _create_lexical_backend(collection_prefix: str, root: Path) -> MemoryBackend:
    """FTS5 lexical backend at the standard path, migrating any legacy JSON store."""
    from grimoire.memory.backends.lexical import LexicalMemoryBackend

    memory_dir = root / "_grimoire" / "_memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    return LexicalMemoryBackend(
        memory_dir / f"{collection_prefix}_lexical.sqlite3",
        legacy_json=memory_dir / f"{collection_prefix}.json",
    )


def _create_lexical_companion(config: GrimoireConfig, backend_id: str, root: Path) -> MemoryBackend | None:
    """Lexical companion index for hybrid (vector + BM25) retrieval.

    Only created for vector backends when FTS5 is available and the project
    has not opted out of vector retrieval — the companion mirrors writes so
    :meth:`MemoryManager.hybrid_search` can fuse both rankings.
    """
    from grimoire.memory.backends.lexical import fts5_available

    if backend_id not in _VECTOR_BACKENDS or config.memory.retrieval_mode != "vector":
        return None
    if not fts5_available():
        return None
    try:
        return _create_lexical_backend(config.memory.collection_prefix, root)
    except Exception:
        return None


def _wants_neo4j_graph(config: GrimoireConfig) -> bool:
    mem = config.memory
    return any(
        layer == "neo4j"
        for layer in (
            mem.knowledge_graph,
            mem.memory_graph,
            mem.code_graph,
            mem.task_memory,
        )
    )


def _create_memory_graph(config: GrimoireConfig) -> tuple[Any | None, str]:
    """Create the optional Neo4j graph projection if configured and available."""
    if not _wants_neo4j_graph(config):
        return None, ""
    mem = config.memory
    if not mem.neo4j_uri:
        return None, "Neo4j graph sync disabled: neo4j_uri is empty"
    password = os.environ.get(mem.neo4j_password_env, "")
    if not password:
        return None, f"Neo4j graph sync disabled: {mem.neo4j_password_env} is not set"
    try:
        from grimoire.memory.neo4j_graph import Neo4jMemoryGraph

        return Neo4jMemoryGraph(
            uri=mem.neo4j_uri,
            user=mem.neo4j_user,
            password=password,
            database=mem.neo4j_database,
        ), ""
    except ImportError as exc:
        return None, f"Neo4j graph sync disabled: {exc}"
    except Exception as exc:
        return None, f"Neo4j graph sync unavailable: {exc}"


def _create_hot_memory(config: GrimoireConfig) -> tuple[RedisHotMemory | None, str]:
    """Create optional hot-memory adapter without making it a durable dependency."""
    mem = config.memory
    if mem.short_term_backend != "redis":
        return None, ""
    if not mem.redis_url:
        return None, "Redis hot memory disabled: memory.redis_url is empty"
    try:
        return RedisHotMemory(
            mem.redis_url,
            namespace=mem.collection_prefix,
        ), ""
    except (RuntimeError, ValueError) as exc:
        return None, f"Redis hot memory unavailable: {exc}"


class MemoryManager:
    """Unified API wrapping whichever backend the project configures.

    Create via :meth:`from_config` or :meth:`from_backend`.
    """

    def __init__(
        self,
        backend: MemoryBackend,
        *,
        project_name: str = "grimoire",
        auto_enrich: bool = False,
        sidecar: MemorySidecar | None = None,
        memory_graph: Any | None = None,
        graph_sync_issue: str = "",
        hot_memory: RedisHotMemory | None = None,
        hot_memory_issue: str = "",
        lexical_companion: MemoryBackend | None = None,
    ) -> None:
        self._backend = backend
        self._project_name = project_name
        self._auto_enrich = auto_enrich
        self._sidecar = sidecar
        self._memory_graph = memory_graph
        self._graph_sync_issue = graph_sync_issue
        self._hot_memory = hot_memory
        self._hot_memory_issue = hot_memory_issue
        self._lexical_companion = lexical_companion

    @classmethod
    def from_config(cls, config: GrimoireConfig, *, project_root: Path | None = None) -> MemoryManager:
        """Auto-create the right backend from project config.

        Parameters
        ----------
        config :
            Validated Grimoire configuration.
        project_root :
            Explicit project root directory.  Strongly recommended so
            that the local backend writes to the correct location.
        """
        try:
            backend = _create_backend(config, project_root)
        except ImportError as exc:
            raise GrimoireMemoryError(
                f"Missing dependency for memory backend '{config.memory.backend}': {exc}"
            ) from exc
        except Exception as exc:
            raise GrimoireMemoryError(f"Failed to initialise memory backend: {exc}") from exc
        root = (project_root or Path()).resolve()
        mem = config.memory
        backend_id = mem.backend if mem.backend != _BACKEND_AUTO else _resolve_auto(config)
        sidecar = MemorySidecar(root / "_grimoire" / "_memory" / "palace_sidecar.sqlite3")
        memory_graph, graph_sync_issue = _create_memory_graph(config)
        hot_memory, hot_memory_issue = _create_hot_memory(config)
        return cls(
            backend,
            project_name=config.project.name,
            auto_enrich=True,
            sidecar=sidecar,
            memory_graph=memory_graph,
            graph_sync_issue=graph_sync_issue,
            hot_memory=hot_memory,
            hot_memory_issue=hot_memory_issue,
            lexical_companion=_create_lexical_companion(config, backend_id, root),
        )

    @classmethod
    def from_backend(cls, backend: MemoryBackend) -> MemoryManager:
        """Wrap an already-instantiated backend."""
        return cls(backend)

    # ── Delegated API ─────────────────────────────────────────────────────

    @property
    def backend(self) -> MemoryBackend:
        """The underlying backend instance."""
        return self._backend

    @property
    def sidecar(self) -> MemorySidecar | None:
        """The optional structured-memory sidecar."""
        return self._sidecar

    @property
    def memory_graph(self) -> Any | None:
        """The optional Neo4j graph projection adapter."""
        return self._memory_graph

    @property
    def hot_memory(self) -> RedisHotMemory | None:
        """The optional Redis hot-memory adapter."""
        return self._hot_memory

    def _prepare_metadata(
        self,
        metadata: dict[str, Any] | None,
        *,
        user_id: str,
        tags: tuple[str, ...],
    ) -> dict[str, Any] | None:
        if not self._auto_enrich:
            return metadata
        return normalize_palace_metadata(
            metadata,
            project_name=self._project_name,
            user_id=user_id,
            tags=tags,
        )

    def store(self, text: str, *, user_id: str = "", tags: tuple[str, ...] = (), metadata: dict[str, Any] | None = None) -> MemoryEntry:
        normalized = self._prepare_metadata(metadata, user_id=user_id, tags=tags)
        entry = self._backend.store(text, user_id=user_id, tags=tags, metadata=normalized)
        self._sync_memory(entry)
        return entry

    def recall(self, entry_id: str) -> MemoryEntry | None:
        return self._backend.recall(entry_id)

    def search(self, query: str, *, user_id: str = "", limit: int = 5) -> list[MemoryEntry]:
        return self._backend.search(query, user_id=user_id, limit=limit)

    # ── Hybrid retrieval (vector + lexical BM25, RRF fusion) ─────────────────

    @property
    def lexical_companion(self) -> MemoryBackend | None:
        """The optional lexical companion index used for hybrid retrieval."""
        return self._lexical_companion

    def hybrid_search(self, query: str, *, user_id: str = "", limit: int = 5) -> list[MemoryEntry]:
        """Fuse vector and lexical rankings via RRF.

        Falls back to plain :meth:`search` when no lexical companion is
        configured (local/lexical primaries already are lexical).
        """
        if self._lexical_companion is None:
            return self.search(query, user_id=user_id, limit=limit)
        from grimoire.memory.retrieval import HybridRetriever

        retriever = HybridRetriever([
            ("vector", self._backend),
            ("lexical", self._lexical_companion),
        ])
        return retriever.search(query, user_id=user_id, limit=limit)

    def reindex_lexical_companion(self) -> int:
        """Backfill the lexical companion from the primary backend.

        Returns the number of entries mirrored; 0 when no companion is
        configured.  Use after enabling hybrid retrieval on a project with
        pre-existing memories.
        """
        if self._lexical_companion is None:
            return 0
        mirrored = 0
        for entry in self._backend.get_all():
            self._lexical_companion.upsert(
                entry.id, entry.text, user_id=entry.user_id, tags=entry.tags, metadata=dict(entry.metadata),
            )
            mirrored += 1
        return mirrored

    # ── Typed memory protocol (parity with legacy mem0-bridge, ADR-003) ──────

    def typed_entry_id(self, agent: str, text: str) -> str:
        """Deterministic UUID5 — idempotent upsert key on (project, agent, text[:150]).

        Mirrors the legacy mem0-bridge convention so both paths deduplicate
        the same way during the shell → SDK transition.
        """
        seed = f"grimoire-{self._project_name}:{agent}:{text[:150]}"
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, seed))

    def remember(
        self,
        type_: str,
        agent: str,
        text: str,
        *,
        tags: tuple[str, ...] = (),
    ) -> MemoryEntry:
        """Typed, idempotent memory write (agent protocol).

        Same text from the same agent always maps to the same entry id: backends
        with ``upsert`` replace in place; others fall back to a duplicate lookup
        before storing, so repeated writes never create duplicates.
        """
        if type_ not in MEMORY_TYPES:
            msg = f"Invalid memory type {type_!r}. Valid: {', '.join(MEMORY_TYPES)}"
            raise GrimoireMemoryError(msg)
        entry_id = self.typed_entry_id(agent, text)
        metadata = {"type": type_, "agent": agent, "dedup_key": entry_id}
        normalized = self._prepare_metadata(metadata, user_id=agent, tags=tags)
        try:
            entry = self._backend.upsert(entry_id, text, user_id=agent, tags=tags, metadata=normalized)
        except NotImplementedError:
            existing = self._find_typed_duplicate(entry_id, text, agent)
            if existing is not None:
                return existing
            entry = self._backend.store(text, user_id=agent, tags=tags, metadata=normalized)
        self._sync_memory(entry)
        return entry

    def _find_typed_duplicate(self, dedup_key: str, text: str, agent: str) -> MemoryEntry | None:
        """Best-effort dedup for backends without ``upsert``."""
        recalled = self._backend.recall(dedup_key)
        if recalled is not None:
            return recalled
        for candidate in self._backend.search(text[:150], user_id=agent, limit=10):
            if candidate.metadata.get("dedup_key") == dedup_key or candidate.text == text:
                return candidate
        return None

    def recall_typed(
        self,
        query: str,
        *,
        type_: str = "",
        agent: str = "",
        limit: int = 5,
    ) -> list[MemoryEntry]:
        """Search memories written via :meth:`remember`, filtered by type/agent."""
        if type_ and type_ not in MEMORY_TYPES:
            msg = f"Invalid memory type {type_!r}. Valid: {', '.join(MEMORY_TYPES)}"
            raise GrimoireMemoryError(msg)
        candidates = self._backend.search(query, user_id=agent, limit=max(limit * 4, limit))
        results: list[MemoryEntry] = []
        for entry in candidates:
            if type_ and entry.metadata.get("type") != type_ and entry.metadata.get("memory_type") != type_:
                continue
            results.append(entry)
            if len(results) >= limit:
                break
        return results

    def search_taxonomy(
        self,
        query: str,
        *,
        user_id: str = "",
        limit: int = 5,
        wing: str = "",
        hall: str = "",
        room: str = "",
    ) -> list[MemoryEntry]:
        filters = {"wing": wing, "hall": hall, "room": room}
        search_filtered = getattr(self._backend, "search_filtered", None)
        if callable(search_filtered):
            return cast(list[MemoryEntry], search_filtered(query, user_id=user_id, limit=limit, filters=filters))
        candidates = self._backend.search(query, user_id=user_id, limit=max(limit * 10, limit))
        return [
            entry
            for entry in candidates
            if entry_matches_filters(entry, wing=wing, hall=hall, room=room)
        ][:limit]

    def get_all(self, *, user_id: str = "", offset: int = 0, limit: int | None = None) -> list[MemoryEntry]:
        return self._backend.get_all(user_id=user_id, offset=offset, limit=limit)

    def get_all_filtered(
        self,
        *,
        user_id: str = "",
        offset: int = 0,
        limit: int | None = None,
        wing: str = "",
        hall: str = "",
        room: str = "",
    ) -> list[MemoryEntry]:
        filters = {"wing": wing, "hall": hall, "room": room}
        get_all_filtered = getattr(self._backend, "get_all_filtered", None)
        if callable(get_all_filtered):
            return cast(list[MemoryEntry], get_all_filtered(user_id=user_id, offset=offset, limit=limit, filters=filters))
        entries = self._backend.get_all(user_id=user_id, offset=0, limit=None)
        filtered = [
            entry
            for entry in entries
            if entry_matches_filters(entry, wing=wing, hall=hall, room=room)
        ]
        return filtered[offset:] if limit is None else filtered[offset:offset + limit]

    def taxonomy(
        self,
        *,
        user_id: str = "",
        wing: str = "",
        hall: str = "",
        room: str = "",
    ) -> dict[str, Any]:
        filters = {"wing": wing, "hall": hall, "room": room}
        taxonomy_fn = getattr(self._backend, "taxonomy", None)
        if callable(taxonomy_fn):
            return cast(dict[str, Any], taxonomy_fn(user_id=user_id, filters=filters))
        return build_taxonomy(self.get_all_filtered(user_id=user_id, wing=wing, hall=hall, room=room))

    def count(self) -> int:
        return self._backend.count()

    def health_check(self) -> BackendStatus:
        status = self._backend.health_check()
        detail = dict(status.detail)
        if self._hot_memory is not None:
            hot_status = self._hot_memory.health_check()
            detail["hot_memory"] = hot_status.to_dict()
        elif self._hot_memory_issue:
            detail["hot_memory"] = HotMemoryStatus(
                backend="redis",
                enabled=True,
                healthy=False,
                detail={"reason": self._hot_memory_issue},
            ).to_dict()
        if self._sidecar is not None:
            detail.update({
                "palace_sidecar": str(self._sidecar.db_path),
                **self._sidecar.facts_stats(),
                **self._sidecar.diary_stats(),
            })
        if self._memory_graph is not None:
            try:
                graph_status = self._memory_graph.health_check()
                detail["neo4j_graph_sync"] = "ready" if graph_status.healthy else "error"
                detail["neo4j_graph_sync_detail"] = graph_status.detail
            except Exception as exc:
                self._record_graph_sync_issue("health_check", exc)
                detail["neo4j_graph_sync"] = "error"
                detail["neo4j_graph_sync_detail"] = {"reason": self._graph_sync_issue}
        elif self._graph_sync_issue:
            detail["neo4j_graph_sync"] = "disabled"
            detail["neo4j_graph_sync_detail"] = {"reason": self._graph_sync_issue}
        return BackendStatus(
            backend=status.backend,
            healthy=status.healthy,
            entries=status.entries,
            detail=detail,
        )

    def consolidate(self) -> int:
        return self._backend.consolidate()

    def _require_hot_memory(self) -> RedisHotMemory:
        if self._hot_memory is None:
            reason = self._hot_memory_issue or "memory.short_term_backend is not 'redis'"
            raise GrimoireMemoryError(f"Redis hot memory is not available: {reason}")
        return self._hot_memory

    def hot_store(
        self,
        key: str,
        value: str,
        *,
        ttl_seconds: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._require_hot_memory().store(key, value, ttl_seconds=ttl_seconds, metadata=metadata)

    def hot_recall(self, key: str) -> dict[str, Any] | None:
        return self._require_hot_memory().recall(key)

    def hot_delete(self, key: str) -> bool:
        return self._require_hot_memory().delete(key)

    def hot_acquire_lease(self, key: str, *, ttl_seconds: int | None = None) -> str | None:
        return self._require_hot_memory().acquire_lease(key, ttl_seconds=ttl_seconds)

    def hot_release_lease(self, key: str, token: str) -> bool:
        return self._require_hot_memory().release_lease(key, token)

    def delete(self, entry_id: str) -> bool:
        deleted = self._backend.delete(entry_id)
        if deleted:
            self._sync_delete_memory(entry_id)
        return deleted

    def update(self, entry_id: str, *, text: str | None = None, tags: tuple[str, ...] | None = None, metadata: dict[str, Any] | None = None) -> MemoryEntry | None:
        normalized = metadata
        if self._auto_enrich and metadata is not None:
            existing = self._backend.recall(entry_id)
            merged = dict(existing.metadata) if existing is not None else {}
            merged.update(metadata)
            normalized = self._prepare_metadata(
                merged,
                user_id=existing.user_id if existing is not None else "",
                tags=tags or (existing.tags if existing is not None else ()),
            )
        updated = self._backend.update(entry_id, text=text, tags=tags, metadata=normalized)
        if updated is not None:
            self._sync_memory(updated)
        return updated

    def upsert(
        self,
        entry_id: str,
        text: str,
        *,
        user_id: str = "",
        tags: tuple[str, ...] = (),
        metadata: dict[str, Any] | None = None,
    ) -> MemoryEntry:
        normalized = self._prepare_metadata(metadata, user_id=user_id, tags=tags)
        try:
            entry = self._backend.upsert(entry_id, text, user_id=user_id, tags=tags, metadata=normalized)
        except NotImplementedError as exc:
            raise GrimoireMemoryError(str(exc)) from None
        self._sync_memory(entry)
        return entry

    def store_many(self, entries: list[dict[str, Any]]) -> list[MemoryEntry]:
        if not self._auto_enrich:
            results = self._backend.store_many(entries)
            self._sync_memories(results)
            return results

        normalized_entries: list[dict[str, Any]] = []
        for entry in entries:
            tags = tuple(entry.get("tags", ()))
            user_id = str(entry.get("user_id", ""))
            normalized_entries.append({
                **entry,
                "tags": list(tags),
                "metadata": self._prepare_metadata(entry.get("metadata"), user_id=user_id, tags=tags),
            })
        results = self._backend.store_many(normalized_entries)
        self._sync_memories(results)
        return results

    def _require_sidecar(self) -> MemorySidecar:
        if self._sidecar is None:
            raise GrimoireMemoryError("Structured memory sidecar is not available for this manager")
        return self._sidecar

    def add_fact(
        self,
        subject: str,
        predicate: str,
        object_: str,
        *,
        valid_from: str = "",
        confidence: float = 1.0,
        source_memory_id: str = "",
    ) -> KnowledgeFact:
        sidecar = self._require_sidecar()
        wing = hall = room = ""
        if source_memory_id:
            source_entry = self._backend.recall(source_memory_id)
            if source_entry is not None:
                wing = str(source_entry.metadata.get("wing", ""))
                hall = str(source_entry.metadata.get("hall", ""))
                room = str(source_entry.metadata.get("room", ""))
        fact = sidecar.add_fact(
            subject,
            predicate,
            object_,
            valid_from=valid_from,
            confidence=confidence,
            source_memory_id=source_memory_id,
            wing=wing,
            hall=hall,
            room=room,
        )
        self._sync_fact(fact)
        return fact

    def invalidate_fact(self, subject: str, predicate: str, object_: str, *, ended: str = "") -> int:
        ended_at = ended or time.strftime("%Y-%m-%d")
        count = self._require_sidecar().invalidate_fact(subject, predicate, object_, ended=ended_at)
        if count:
            self._sync_invalidate_fact(subject, predicate, object_, ended=ended_at)
        return count

    def query_facts(self, entity: str, *, as_of: str = "", direction: str = "both") -> list[KnowledgeFact]:
        return self._require_sidecar().query_facts(entity, as_of=as_of, direction=direction)

    def facts_timeline(self, entity: str = "") -> list[KnowledgeFact]:
        return self._require_sidecar().timeline(entity)

    def facts_stats(self) -> dict[str, Any]:
        return self._require_sidecar().facts_stats()

    def write_diary(
        self,
        agent_name: str,
        entry: str,
        *,
        topic: str = "general",
        entry_format: str = "markdown",
        related_memory_id: str = "",
    ) -> DiaryRecord:
        diary = self._require_sidecar().write_diary(
            agent_name,
            entry,
            topic=topic,
            entry_format=entry_format,
            related_memory_id=related_memory_id,
        )
        self._sync_diary(diary)
        return diary

    def read_diary(self, agent_name: str, *, last_n: int = 10) -> list[DiaryRecord]:
        return self._require_sidecar().read_diary(agent_name, last_n=last_n)

    def diary_stats(self) -> dict[str, Any]:
        return self._require_sidecar().diary_stats()

    # ── Optional Neo4j runtime graph projection ───────────────────────

    def _sync_memory(self, entry: MemoryEntry) -> None:
        self._mirror_to_companion(entry)
        if self._memory_graph is None:
            return
        try:
            self._memory_graph.upsert_memory(entry)
        except Exception as exc:
            self._record_graph_sync_issue("memory", exc)

    def _mirror_to_companion(self, entry: MemoryEntry) -> None:
        """Best-effort write mirror into the lexical companion index."""
        if self._lexical_companion is None:
            return
        # Companion is advisory: the primary write already succeeded.
        with contextlib.suppress(Exception):
            self._lexical_companion.upsert(
                entry.id, entry.text, user_id=entry.user_id, tags=entry.tags, metadata=dict(entry.metadata),
            )

    def _sync_memories(self, entries: list[MemoryEntry]) -> None:
        for entry in entries:
            self._sync_memory(entry)

    def _sync_delete_memory(self, entry_id: str) -> None:
        if self._lexical_companion is not None:
            with contextlib.suppress(Exception):
                self._lexical_companion.delete(entry_id)
        if self._memory_graph is None:
            return
        try:
            self._memory_graph.delete_memory(entry_id)
        except Exception as exc:
            self._record_graph_sync_issue("delete", exc)

    def _sync_fact(self, fact: KnowledgeFact) -> None:
        if self._memory_graph is None:
            return
        try:
            self._memory_graph.upsert_fact(fact)
        except Exception as exc:
            self._record_graph_sync_issue("fact", exc)

    def _sync_invalidate_fact(self, subject: str, predicate: str, object_: str, *, ended: str) -> None:
        if self._memory_graph is None:
            return
        try:
            self._memory_graph.invalidate_fact(subject, predicate, object_, ended=ended)
        except Exception as exc:
            self._record_graph_sync_issue("fact_invalidation", exc)

    def _sync_diary(self, record: DiaryRecord) -> None:
        if self._memory_graph is None:
            return
        try:
            self._memory_graph.upsert_diary(record)
        except Exception as exc:
            self._record_graph_sync_issue("diary", exc)

    def _record_graph_sync_issue(self, operation: str, exc: Exception) -> None:
        self._graph_sync_issue = f"Neo4j graph sync failed during {operation}: {exc}"
        logger.warning(self._graph_sync_issue)

    # ── Progressive Disclosure (3-layer search) ───────────────────────────
    #
    # Inspired by claude-mem's 3-layer MCP search architecture:
    #   L1 = ultra-compact summaries (~50 tokens) — for quick orientation
    #   L2 = standard detail (~200 tokens) — for working context
    #   L3 = full observations (~500+ tokens) — for deep dives
    #
    # This reduces token usage by ~10x on average: start with L1, only
    # drill into L2/L3 when the compact result is insufficient.

    _TOKEN_LIMITS: ClassVar[dict[str, int]] = {"L1": 50, "L2": 200, "L3": 2000}

    def progressive_search(
        self,
        query: str,
        *,
        layer: str = "L1",
        user_id: str = "",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Search with progressive disclosure — returns results trimmed to layer budget.

        Parameters
        ----------
        query :
            Search query.
        layer :
            Disclosure layer: ``"L1"`` (compact), ``"L2"`` (standard),
            ``"L3"`` (full).
        user_id :
            Optional user scope.
        limit :
            Max results.

        Returns
        -------
        list[dict[str, Any]]
            Each dict has ``id``, ``text``, ``tags``, ``score``, ``layer``.
        """
        if layer not in self._TOKEN_LIMITS:
            msg = f"Invalid layer '{layer}'. Valid: L1, L2, L3"
            raise ValueError(msg)

        token_budget = self._TOKEN_LIMITS[layer]
        raw_results = self.search(query, user_id=user_id, limit=limit)

        disclosed: list[dict[str, Any]] = []
        for entry in raw_results:
            text = entry.text
            trimmed = self._trim_to_budget(text, token_budget)
            disclosed.append({
                "id": entry.id,
                "text": trimmed,
                "tags": list(entry.tags),
                "score": getattr(entry, "score", 0.0),
                "layer": layer,
                "truncated": len(trimmed) < len(text),
            })

        return disclosed

    @staticmethod
    def _trim_to_budget(text: str, token_budget: int) -> str:
        """Trim text to approximate token budget (4 chars ≈ 1 token)."""
        char_budget = token_budget * 4
        if len(text) <= char_budget:
            return text
        return text[:char_budget].rsplit(" ", 1)[0] + "…"
