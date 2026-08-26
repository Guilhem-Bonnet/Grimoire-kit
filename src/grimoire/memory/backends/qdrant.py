"""Qdrant-based memory backend — local file or remote server.

Requires ``qdrant-client`` plus an embedding engine::

    pip install grimoire-kit[qdrant]

Supports local (file-based) and remote (URL-based) Qdrant instances.
Embeddings come from :mod:`grimoire.memory.embedding` — fastembed by default,
sentence-transformers when it is the only engine installed.
"""

from __future__ import annotations

import os
from typing import Any

from grimoire.memory.backends._qdrant_mixin import QdrantStorageMixin
from grimoire.memory.backends.base import BackendStatus, MemoryBackend
from grimoire.memory.embedding import DEFAULT_MODEL, Embedder, EmbeddingEngineError, build_embedder

_DEFAULT_COLLECTION = "grimoire"


def _require_qdrant() -> Any:
    """Import and return qdrant_client, raising a clear error if missing."""
    try:
        import qdrant_client

        return qdrant_client
    except ImportError:
        raise ImportError(
            "qdrant-client is not installed. Run:\n  pip install grimoire-kit[qdrant]"
        ) from None


class QdrantBackend(QdrantStorageMixin, MemoryBackend):
    """Qdrant vector backend with dense embeddings.

    Works in two modes:
    - **local**: file-based Qdrant (no server needed)
    - **server**: connects to a remote Qdrant URL

    Usage::

        # Local mode
        backend = QdrantBackend(qdrant_path="/tmp/qdrant_data")

        # Server mode
        backend = QdrantBackend(qdrant_url="http://localhost:6333")
    """

    def __init__(
        self,
        *,
        embedding_model: str = DEFAULT_MODEL,
        collection: str = _DEFAULT_COLLECTION,
        qdrant_path: str | None = None,
        qdrant_url: str | None = None,
        qdrant_api_key: str | None = None,
        timeout: float = 5.0,
        embedding_cache_dir: str = "",
        embedding_model_path: str = "",
        embedding_offline: bool = False,
    ) -> None:
        qdrant_client = _require_qdrant()

        from qdrant_client.models import Distance, VectorParams

        self._collection = collection
        self._embedding_model_name = embedding_model

        self._embedder: Embedder = build_embedder(
            embedding_model,
            cache_dir=embedding_cache_dir,
            model_path=embedding_model_path,
            offline=embedding_offline,
        )
        self._vector_size = self._embedder.dim

        # Resolve mode: explicit params → env vars → local default
        url = qdrant_url or os.environ.get("GRIMOIRE_QDRANT_URL", "")
        api_key = qdrant_api_key or os.environ.get("GRIMOIRE_QDRANT_API_KEY", "")

        if url:
            self._client: Any = qdrant_client.QdrantClient(
                url=url,
                api_key=api_key or None,
                timeout=timeout,
            )
            self._mode = "server"
        else:
            path = qdrant_path or str(os.environ.get("GRIMOIRE_QDRANT_PATH", "./qdrant_data"))
            self._client = qdrant_client.QdrantClient(path=path)
            self._mode = "local"

        # Create collection if it doesn't exist, otherwise refuse a geometry clash.
        existing = [c.name for c in self._client.get_collections().collections]
        if self._collection not in existing:
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config=VectorParams(size=self._vector_size, distance=Distance.COSINE),
            )
        else:
            self._assert_dimension_matches()

    def _assert_dimension_matches(self) -> None:
        """Fail loudly when the store was written with a different model.

        Writing 384-dim vectors into a 768-dim collection is rejected by Qdrant,
        but the reverse — a model swap at equal dimension — degrades silently.
        The dimension is the part we can check, and it catches every model change
        that would otherwise corrupt the store.
        """
        try:
            info = self._client.get_collection(collection_name=self._collection)
            params = info.config.params.vectors
            stored = int(params.size) if hasattr(params, "size") else 0
        except Exception:  # pragma: no cover - shape varies across server versions
            return
        if stored and stored != self._vector_size:
            msg = (
                f"Collection '{self._collection}' holds {stored}-dimension vectors but "
                f"'{self._embedding_model_name}' produces {self._vector_size}.\n"
                "  → keep the original model, or re-index with:\n"
                "      grimoire memory export … && grimoire memory import …"
            )
            raise EmbeddingEngineError(msg)

    def _embed(self, text: str) -> list[float]:
        return self._embedder.encode(text)

    # ── health_check (backend-specific detail) ────────────────────────────

    def health_check(self) -> BackendStatus:
        try:
            n = self.count()
            return BackendStatus(
                backend=f"qdrant-{self._mode}",
                healthy=True,
                entries=n,
                detail={
                    "mode": self._mode,
                    "collection": self._collection,
                    "embedding_model": self._embedding_model_name,
                    "embedding_engine": self._embedder.engine,
                    "vector_size": self._vector_size,
                    "search": f"semantic ({self._embedder.engine})",
                },
            )
        except Exception as exc:
            return BackendStatus(
                backend=f"qdrant-{self._mode}",
                healthy=False,
                entries=0,
                detail={"error": str(exc)},
            )
