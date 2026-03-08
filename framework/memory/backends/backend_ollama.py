"""
Backend Ollama — Ollama embeddings (nomic-embed-text) + Qdrant.

Utilise Ollama pour générer les embeddings (aucune dépendance ML locale
lourde — le modèle tourne sur le serveur Ollama).
Stocke dans Qdrant (local fichier OU serveur distant).

Variables d'environnement :
  Grimoire_OLLAMA_URL   — URL Ollama (défaut: http://localhost:11434)
  Grimoire_QDRANT_URL   — URL Qdrant serveur (si absent → qdrant local fichier)

Dépendances : qdrant-client
  pip install qdrant-client
  (sentence-transformers N'EST PAS nécessaire avec ce backend)
"""

from __future__ import annotations
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


_MEMORY_DIR = Path(__file__).resolve().parent.parent.parent / "memory"
_QDRANT_PATH = str(_MEMORY_DIR / "qdrant_data")
_OLLAMA_VECTOR_SIZES = {
    "nomic-embed-text": 768,
    "mxbai-embed-large": 1024,
    "all-minilm": 384,
}


def _ollama_embed(text: str, model: str, base_url: str, timeout: float = 10.0) -> list[float]:
    """Appel HTTP à l'API Ollama /api/embeddings."""
    url = f"{base_url.rstrip('/')}/api/embeddings"
    payload = json.dumps({"model": model, "prompt": text}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            # Compat API v1 (embedding) et v2 (embeddings list)
            return data.get("embedding") or data.get("embeddings", [[]])[0]
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"Ollama API erreur {e.code} pour le modèle '{model}'.\n"
            f"  → Vérifier que le modèle est disponible : ollama pull {model}"
        ) from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise RuntimeError(
            f"Ollama inaccessible à {base_url}.\n"
            f"  → Vérifier que Ollama tourne : ollama serve\n"
            f"  → Ou configurer Grimoire_OLLAMA_URL"
        ) from e


class OllamaBackend:
    """Ollama HTTP embeddings + Qdrant (local ou serveur)."""

    def __init__(
        self,
        ollama_url: str = "http://localhost:11434",
        qdrant_url: str = "",
        embedding_model: str = "nomic-embed-text",
        collection: str = "grimoire",
        timeout: float = 10.0,
    ):
        try:
            from qdrant_client import QdrantClient
        except ImportError:
            raise ImportError(
                "qdrant-client non installé. Exécuter :\n"
                "  pip install qdrant-client"
            )
        from qdrant_client.models import Distance, VectorParams

        self._ollama_url = os.environ.get("Grimoire_OLLAMA_URL", ollama_url)
        self._model = embedding_model
        self._timeout = timeout
        self._collection = collection
        self._vector_size = _OLLAMA_VECTOR_SIZES.get(embedding_model, 768)

        # Tester la connexion + récupérer la taille réelle du vecteur
        # (en encodant un texte test)
        _qdrant_url = os.environ.get("Grimoire_QDRANT_URL", qdrant_url)
        if _qdrant_url:
            self._client = QdrantClient(url=_qdrant_url, timeout=2.0)
        else:
            self._client = QdrantClient(path=_QDRANT_PATH)

        # Vérifier connexion Qdrant
        self._client.get_collections()

        # Test embed pour obtenir la vraie taille
        test_vec = _ollama_embed("test", self._model, self._ollama_url, self._timeout)
        if test_vec:
            self._vector_size = len(test_vec)

        # Créer la collection si nécessaire
        existing = [c.name for c in self._client.get_collections().collections]
        if self._collection not in existing:
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config=VectorParams(
                    size=self._vector_size, distance=Distance.COSINE
                ),
            )

    # --------------------------------------------------------------- CONTRAT
    def add(self, text: str, user_id: str = "", metadata: dict | None = None) -> dict:
        from qdrant_client.models import PointStruct

        vector = _ollama_embed(text, self._model, self._ollama_url, self._timeout)
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

        vector = _ollama_embed(query, self._model, self._ollama_url, self._timeout)
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
        return {
            "backend": "ollama",
            "ollama_url": self._ollama_url,
            "embedding_model": self._model,
            "vector_size": self._vector_size,
            "collection": self._collection,
            "entries": self.count(),
            "search": "semantic (Ollama embeddings)",
        }
