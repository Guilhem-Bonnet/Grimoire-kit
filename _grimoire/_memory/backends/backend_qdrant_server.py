"""
Backend Qdrant Server — Qdrant distant + sentence-transformers.

Se connecte à un serveur Qdrant existant (local ou réseau).
Circuit breaker : timeout 500ms sur connect, 2s sur opérations.

Variables d'environnement :
  Grimoire_QDRANT_URL  — URL du serveur (ex: http://localhost:6333)
  Grimoire_QDRANT_API_KEY — Clé API optionnelle (Qdrant Cloud)

Dépendances : qdrant-client sentence-transformers
  pip install qdrant-client sentence-transformers
"""

from __future__ import annotations
import os
import time
import uuid
from pathlib import Path


class QdrantServerBackend:
    """Qdrant serveur distant + sentence-transformers embeddings. Circuit breaker intégré."""

    VECTOR_SIZE = {
        "all-MiniLM-L6-v2": 384,
        "all-mpnet-base-v2": 768,
        "nomic-embed-text": 768,
    }

    def __init__(
        self,
        qdrant_url: str = "http://localhost:6333",
        embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2",
        collection: str = "grimoire",
        api_key: str | None = None,
        timeout: float = 2.0,
    ):
        try:
            from qdrant_client import QdrantClient
        except ImportError:
            raise ImportError(
                "qdrant-client non installé. Exécuter :\n"
                "  pip install qdrant-client sentence-transformers"
            )
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError(
                "sentence-transformers non installé. Exécuter :\n"
                "  pip install sentence-transformers"
            )

        model_short = embedding_model.split("/")[-1]
        self._vector_size = self.VECTOR_SIZE.get(model_short, 384)
        self._collection = collection
        self._timeout = timeout

        _api_key = api_key or os.environ.get("Grimoire_QDRANT_API_KEY")
        _url = os.environ.get("Grimoire_QDRANT_URL", qdrant_url)

        # Connexion avec circuit breaker (timeout courte)
        self._client = QdrantClient(
            url=_url,
            api_key=_api_key,
            timeout=self._timeout,
        )
        # Test connexion (lève une exception si serveur absent → interceptée par factory)
        self._client.get_collections()

        self._model = SentenceTransformer(embedding_model)

        # Créer la collection si nécessaire
        from qdrant_client.models import Distance, VectorParams
        existing = [c.name for c in self._client.get_collections().collections]
        if self._collection not in existing:
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config=VectorParams(size=self._vector_size, distance=Distance.COSINE),
            )

    # --------------------------------------------------------------- CONTRAT
    def add(self, text: str, user_id: str = "", metadata: dict | None = None) -> dict:
        from qdrant_client.models import PointStruct

        vector = self._model.encode(text).tolist()
        point_id = str(uuid.uuid4())
        payload = {
            "memory": text,
            "user_id": user_id or "global",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            **(metadata or {}),
        }
        self._client.upsert(
            collection_name=self._collection,
            points=[PointStruct(id=point_id, vector=vector, payload=payload)],
        )
        return {"id": point_id, "memory": text, **payload}

    def search(self, query: str, user_id: str = "", limit: int = 5) -> list[dict]:
        from qdrant_client.models import Filter, FieldCondition, MatchValue

        vector = self._model.encode(query).tolist()
        flt = None
        if user_id:
            flt = Filter(
                must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]
            )
        response = self._client.query_points(
            collection_name=self._collection,
            query=vector,
            limit=limit,
            query_filter=flt,
        )
        return [{"id": r.id, "score": r.score, **r.payload} for r in response.points]

    def get_all(self, user_id: str = "") -> list[dict]:
        from qdrant_client.models import Filter, FieldCondition, MatchValue

        flt = None
        if user_id:
            flt = Filter(
                must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]
            )
        points, _ = self._client.scroll(
            collection_name=self._collection,
            scroll_filter=flt,
            limit=1000,
            with_payload=True,
        )
        return [{"id": p.id, **p.payload} for p in points]

    def count(self) -> int:
        return self._client.count(collection_name=self._collection).count

    def status(self) -> dict:
        info = self._client.get_collection(self._collection)
        return {
            "backend": "qdrant-server",
            "url": os.environ.get("Grimoire_QDRANT_URL", "configured"),
            "collection": self._collection,
            "entries": self.count(),
            "vectors_config": str(info.config.params.vectors),
            "search": "semantic (sentence-transformers)",
        }
