"""Ollama-based memory backend — HTTP embeddings + Qdrant storage.

Uses Ollama's ``/api/embeddings`` endpoint for vector generation.
No heavy ML dependencies needed — the model runs on the Ollama server.
Storage via Qdrant (local file or remote server).

Requires ``qdrant-client``::

    pip install grimoire-kit[qdrant]

Environment variables:
    Grimoire_OLLAMA_URL   — Ollama server URL (default: http://localhost:11434)
    Grimoire_QDRANT_URL   — Remote Qdrant URL (if absent → local file mode)
    Grimoire_QDRANT_API_KEY — API key for remote Qdrant
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from grimoire.memory.backends._qdrant_mixin import QdrantStorageMixin
from grimoire.memory.backends.base import BackendStatus, MemoryBackend

_OLLAMA_VECTOR_SIZES: dict[str, int] = {
    "nomic-embed-text": 768,
    "mxbai-embed-large": 1024,
    "all-minilm": 384,
}

_DEFAULT_OLLAMA_URL = "http://localhost:11434"
_DEFAULT_MODEL = "nomic-embed-text"
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


def ollama_embed(text: str, model: str, base_url: str, *, timeout: float = 10.0) -> list[float]:
    """Call Ollama /api/embeddings to get a vector for *text*."""
    url = f"{base_url.rstrip('/')}/api/embeddings"
    payload = json.dumps({"model": model, "prompt": text}).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310 — URL constructed from validated base_url
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("embedding") or data.get("embeddings", [[]])[0]  # type: ignore[no-any-return]
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"Ollama API error {exc.code} for model '{model}'.\n"
            f"  → Check model availability: ollama pull {model}"
        ) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(
            f"Ollama unreachable at {base_url}.\n"
            f"  → Check that Ollama is running: ollama serve\n"
            f"  → Or set Grimoire_OLLAMA_URL"
        ) from exc


class OllamaBackend(QdrantStorageMixin, MemoryBackend):
    """Ollama HTTP embeddings + Qdrant vector storage.

    Usage::

        backend = OllamaBackend()  # defaults: localhost, nomic-embed-text
        backend = OllamaBackend(ollama_url="http://gpu-box:11434")
    """

    def __init__(
        self,
        *,
        ollama_url: str = _DEFAULT_OLLAMA_URL,
        embedding_model: str = _DEFAULT_MODEL,
        collection: str = _DEFAULT_COLLECTION,
        qdrant_path: str | None = None,
        qdrant_url: str | None = None,
        qdrant_api_key: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        qdrant_client = _require_qdrant()
        from qdrant_client.models import Distance, VectorParams

        self._ollama_url = os.environ.get("GRIMOIRE_OLLAMA_URL", ollama_url)
        self._model = embedding_model
        self._timeout = timeout
        self._collection = collection
        self._vector_size = _OLLAMA_VECTOR_SIZES.get(embedding_model, 768)

        # Qdrant client: server or local
        url = qdrant_url or os.environ.get("GRIMOIRE_QDRANT_URL", "")
        api_key = qdrant_api_key or os.environ.get("GRIMOIRE_QDRANT_API_KEY", "")

        if url:
            self._client: Any = qdrant_client.QdrantClient(url=url, api_key=api_key or None, timeout=2.0)
            self._qdrant_mode = "server"
        else:
            path = qdrant_path or str(os.environ.get("GRIMOIRE_QDRANT_PATH", "./qdrant_data"))
            self._client = qdrant_client.QdrantClient(path=path)
            self._qdrant_mode = "local"

        # Verify Qdrant connectivity
        self._client.get_collections()

        # Probe actual vector size from Ollama
        test_vec = ollama_embed("test", self._model, self._ollama_url, timeout=self._timeout)
        if test_vec:
            self._vector_size = len(test_vec)

        # Create collection if needed
        existing = [c.name for c in self._client.get_collections().collections]
        if self._collection not in existing:
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config=VectorParams(size=self._vector_size, distance=Distance.COSINE),
            )

    def _embed(self, text: str) -> list[float]:
        return ollama_embed(text, self._model, self._ollama_url, timeout=self._timeout)

    # ── health_check (backend-specific detail) ────────────────────────────

    def health_check(self) -> BackendStatus:
        try:
            n = self.count()
            return BackendStatus(
                backend="ollama",
                healthy=True,
                entries=n,
                detail={
                    "qdrant_mode": self._qdrant_mode,
                    "ollama_url": self._ollama_url,
                    "embedding_model": self._model,
                    "vector_size": self._vector_size,
                    "collection": self._collection,
                    "search": "semantic (Ollama embeddings)",
                },
            )
        except Exception as exc:
            return BackendStatus(
                backend="ollama",
                healthy=False,
                entries=0,
                detail={"error": str(exc)},
            )
