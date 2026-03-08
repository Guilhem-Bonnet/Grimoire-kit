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

import logging
from pathlib import Path
from typing import Any

from grimoire.core.config import GrimoireConfig
from grimoire.core.exceptions import GrimoireMemoryError
from grimoire.memory.backends.base import BackendStatus, MemoryBackend, MemoryEntry

logger = logging.getLogger(__name__)

# Backend identifiers matching MemoryConfig.backend values
_BACKEND_LOCAL = "local"
_BACKEND_QDRANT_LOCAL = "qdrant-local"
_BACKEND_QDRANT_SERVER = "qdrant-server"
_BACKEND_OLLAMA = "ollama"
_BACKEND_AUTO = "auto"


def _resolve_auto(config: GrimoireConfig) -> str:
    """Determine the best backend when ``backend: auto``."""
    mem = config.memory
    if mem.ollama_url:
        return _BACKEND_OLLAMA
    if mem.qdrant_url:
        return _BACKEND_QDRANT_SERVER
    return _BACKEND_LOCAL


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
    root = (project_root or Path(".")).resolve()

    if backend_id == _BACKEND_LOCAL:
        from grimoire.memory.backends.local import LocalMemoryBackend

        memory_dir = root / "_grimoire" / "_memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        return LocalMemoryBackend(memory_dir / f"{mem.collection_prefix}.json")

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


class MemoryManager:
    """Unified API wrapping whichever backend the project configures.

    Create via :meth:`from_config` or :meth:`from_backend`.
    """

    def __init__(self, backend: MemoryBackend) -> None:
        self._backend = backend

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
        return cls(backend)

    @classmethod
    def from_backend(cls, backend: MemoryBackend) -> MemoryManager:
        """Wrap an already-instantiated backend."""
        return cls(backend)

    # ── Delegated API ─────────────────────────────────────────────────────

    @property
    def backend(self) -> MemoryBackend:
        """The underlying backend instance."""
        return self._backend

    def store(self, text: str, *, user_id: str = "", metadata: dict[str, Any] | None = None) -> MemoryEntry:
        return self._backend.store(text, user_id=user_id, metadata=metadata)

    def recall(self, entry_id: str) -> MemoryEntry | None:
        return self._backend.recall(entry_id)

    def search(self, query: str, *, user_id: str = "", limit: int = 5) -> list[MemoryEntry]:
        return self._backend.search(query, user_id=user_id, limit=limit)

    def get_all(self, *, user_id: str = "") -> list[MemoryEntry]:
        return self._backend.get_all(user_id=user_id)

    def count(self) -> int:
        return self._backend.count()

    def health_check(self) -> BackendStatus:
        return self._backend.health_check()

    def consolidate(self) -> int:
        return self._backend.consolidate()
