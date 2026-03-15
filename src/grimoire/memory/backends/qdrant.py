"""Qdrant-based memory backend — local file or remote server.

Requires ``qdrant-client`` and ``sentence-transformers`` optional deps::

    pip install grimoire-kit[qdrant]

Supports local (file-based) and remote (URL-based) Qdrant instances.
Embeddings are generated via sentence-transformers.
"""

from __future__ import annotations

import os
from typing import Any

from grimoire.memory.backends._qdrant_mixin import QdrantStorageMixin
from grimoire.memory.backends.base import BackendStatus, MemoryBackend

# Model → vector size mapping
_VECTOR_SIZES: dict[str, int] = {
    "all-MiniLM-L6-v2": 384,
    "all-mpnet-base-v2": 768,
    "nomic-embed-text": 768,
}

_DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
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


def _require_sentence_transformers() -> Any:
    """Import and return SentenceTransformer, raising a clear error if missing."""
    try:
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer
    except ImportError:
        raise ImportError(
            "sentence-transformers is not installed. Run:\n  pip install sentence-transformers"
        ) from None


class QdrantBackend(QdrantStorageMixin, MemoryBackend):
    """Qdrant vector backend with sentence-transformers embeddings.

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
        embedding_model: str = _DEFAULT_MODEL,
        collection: str = _DEFAULT_COLLECTION,
        qdrant_path: str | None = None,
        qdrant_url: str | None = None,
        qdrant_api_key: str | None = None,
        timeout: float = 5.0,
    ) -> None:
        qdrant_client = _require_qdrant()
        sentence_transformer_cls = _require_sentence_transformers()

        from qdrant_client.models import Distance, VectorParams

        self._collection = collection
        self._embedding_model_name = embedding_model

        # Resolve model short name for vector size lookup
        model_short = embedding_model.split("/")[-1]
        vector_size = _VECTOR_SIZES.get(model_short, 384)

        self._model: Any = sentence_transformer_cls(embedding_model)
        self._vector_size = vector_size

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

        # Create collection if it doesn't exist
        existing = [c.name for c in self._client.get_collections().collections]
        if self._collection not in existing:
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
            )

    def _embed(self, text: str) -> list[float]:
        vec: Any = self._model.encode(text)
        return vec.tolist()  # type: ignore[no-any-return]

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
                    "vector_size": self._vector_size,
                    "search": "semantic (sentence-transformers)",
                },
            )
        except Exception as exc:
            return BackendStatus(
                backend=f"qdrant-{self._mode}",
                healthy=False,
                entries=0,
                detail={"error": str(exc)},
            )
