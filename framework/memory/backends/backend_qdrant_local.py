"""
Backend Qdrant Local — Qdrant en processus + sentence-transformers.

Utilise Qdrant en mode fichier local (pas de serveur à lancer).
Embeddings via sentence-transformers (all-MiniLM-L6-v2 ou configurable).

Dépendances : qdrant-client sentence-transformers
  pip install qdrant-client sentence-transformers
"""

from __future__ import annotations
import time
import uuid
from pathlib import Path


_MEMORY_DIR = Path(__file__).resolve().parent.parent.parent / "memory"
_QDRANT_PATH = str(_MEMORY_DIR / "qdrant_data")


class QdrantLocalBackend:
    """Qdrant fichier local + sentence-transformers embeddings."""

    VECTOR_SIZE = {
        "all-MiniLM-L6-v2": 384,
        "all-mpnet-base-v2": 768,
        "nomic-embed-text": 768,
    }

    def __init__(
        self,
        embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2",
        collection: str = "grimoire",
        qdrant_path: str | None = None,
    ):
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.models import Distance, VectorParams
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
        vector_size = self.VECTOR_SIZE.get(model_short, 384)

        self._model = SentenceTransformer(embedding_model)
        self._collection = collection
        self._client = QdrantClient(path=qdrant_path or _QDRANT_PATH)

        # Créer la collection si nécessaire
        from qdrant_client.models import Distance, VectorParams
        existing = [c.name for c in self._client.get_collections().collections]
        if self._collection not in existing:
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
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
        )
        return [{"id": p.id, **p.payload} for p in points]

    def count(self) -> int:
        return self._client.count(collection_name=self._collection).count

    def status(self) -> dict:
        return {
            "backend": "qdrant-local",
            "path": _QDRANT_PATH,
            "collection": self._collection,
            "entries": self.count(),
            "search": "semantic (sentence-transformers)",
        }
