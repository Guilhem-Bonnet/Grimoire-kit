"""Ollama-based memory backend — HTTP embeddings + Qdrant storage.

Uses Ollama's ``/api/embeddings`` endpoint for vector generation.
No heavy ML dependencies needed — the model runs on the Ollama server.
Storage via Qdrant (local file or remote server).

Requires ``qdrant-client``::

    pip install bmad-kit[qdrant]

Environment variables:
    BMAD_OLLAMA_URL   — Ollama server URL (default: http://localhost:11434)
    BMAD_QDRANT_URL   — Remote Qdrant URL (if absent → local file mode)
    BMAD_QDRANT_API_KEY — API key for remote Qdrant
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

from bmad.memory.backends.base import BackendStatus, MemoryBackend, MemoryEntry

_OLLAMA_VECTOR_SIZES: dict[str, int] = {
    "nomic-embed-text": 768,
    "mxbai-embed-large": 1024,
    "all-minilm": 384,
}

_DEFAULT_OLLAMA_URL = "http://localhost:11434"
_DEFAULT_MODEL = "nomic-embed-text"
_DEFAULT_COLLECTION = "bmad"


def _require_qdrant() -> Any:
    """Import and return qdrant_client, raising a clear error if missing."""
    try:
        import qdrant_client

        return qdrant_client
    except ImportError:
        raise ImportError(
            "qdrant-client is not installed. Run:\n  pip install bmad-kit[qdrant]"
        ) from None


def ollama_embed(text: str, model: str, base_url: str, *, timeout: float = 10.0) -> list[float]:
    """Call Ollama /api/embeddings to get a vector for *text*."""
    url = f"{base_url.rstrip('/')}/api/embeddings"
    payload = json.dumps({"model": model, "prompt": text}).encode("utf-8")
    req = urllib.request.Request(
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
            f"  → Or set BMAD_OLLAMA_URL"
        ) from exc


class OllamaBackend(MemoryBackend):
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

        self._ollama_url = os.environ.get("BMAD_OLLAMA_URL", ollama_url)
        self._model = embedding_model
        self._timeout = timeout
        self._collection = collection
        self._vector_size = _OLLAMA_VECTOR_SIZES.get(embedding_model, 768)

        # Qdrant client: server or local
        url = qdrant_url or os.environ.get("BMAD_QDRANT_URL", "")
        api_key = qdrant_api_key or os.environ.get("BMAD_QDRANT_API_KEY", "")

        if url:
            self._client: Any = qdrant_client.QdrantClient(url=url, api_key=api_key or None, timeout=2.0)
            self._qdrant_mode = "server"
        else:
            path = qdrant_path or str(os.environ.get("BMAD_QDRANT_PATH", "./qdrant_data"))
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

    # ── Contract ──────────────────────────────────────────────────────────

    def store(self, text: str, *, user_id: str = "", metadata: dict[str, Any] | None = None) -> MemoryEntry:
        from qdrant_client.models import PointStruct

        vector = self._embed(text)
        entry_id = str(uuid.uuid4())
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        uid = user_id or "global"
        payload: dict[str, Any] = {
            "memory": text,
            "user_id": uid,
            "created_at": ts,
            **(metadata or {}),
        }
        self._client.upsert(
            collection_name=self._collection,
            points=[PointStruct(id=entry_id, vector=vector, payload=payload)],
        )
        return MemoryEntry(id=entry_id, text=text, user_id=uid, metadata=metadata or {}, created_at=ts)

    def recall(self, entry_id: str) -> MemoryEntry | None:
        results = self._client.retrieve(
            collection_name=self._collection,
            ids=[entry_id],
            with_payload=True,
        )
        if not results:
            return None
        pt = results[0]
        return MemoryEntry(
            id=str(pt.id),
            text=str(pt.payload.get("memory", "")),
            user_id=str(pt.payload.get("user_id", "global")),
            metadata={k: v for k, v in pt.payload.items() if k not in ("memory", "user_id", "created_at")},
            created_at=str(pt.payload.get("created_at", "")),
        )

    def search(self, query: str, *, user_id: str = "", limit: int = 5) -> list[MemoryEntry]:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        vector = self._embed(query)
        flt = None
        if user_id:
            flt = Filter(must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))])
        response = self._client.query_points(
            collection_name=self._collection,
            query=vector,
            limit=limit,
            query_filter=flt,
        )
        return [
            MemoryEntry(
                id=str(r.id),
                text=str(r.payload.get("memory", "")),
                user_id=str(r.payload.get("user_id", "global")),
                metadata={k: v for k, v in r.payload.items() if k not in ("memory", "user_id", "created_at")},
                created_at=str(r.payload.get("created_at", "")),
                score=float(r.score),
            )
            for r in response.points
        ]

    def get_all(self, *, user_id: str = "") -> list[MemoryEntry]:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        flt = None
        if user_id:
            flt = Filter(must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))])
        points, _ = self._client.scroll(
            collection_name=self._collection,
            scroll_filter=flt,
            limit=1000,
            with_payload=True,
        )
        return [
            MemoryEntry(
                id=str(p.id),
                text=str(p.payload.get("memory", "")),
                user_id=str(p.payload.get("user_id", "global")),
                metadata={k: v for k, v in p.payload.items() if k not in ("memory", "user_id", "created_at")},
                created_at=str(p.payload.get("created_at", "")),
            )
            for p in points
        ]

    def count(self) -> int:
        return int(self._client.count(collection_name=self._collection).count)

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

    def consolidate(self) -> int:
        """Qdrant has no built-in dedup — returns 0."""
        return 0
