#!/usr/bin/env python3
"""
semantic-cache.py — Cache sémantique des réponses LLM BMAD (BM-41 Story 3.2).
============================================================

Cache sémantique basé sur Qdrant : si une requête similaire (cosine > 0.9)
a déjà été traitée, retourne la réponse cached au lieu de refaire l'appel LLM.

Modes :
  query      — Cherche dans le cache (hit ou miss)
  store      — Stocke une paire prompt→response dans le cache
  stats      — Statistiques du cache (hit rate, taille, TTL)
  clear      — Vide le cache (total ou par type)
  invalidate — Invalide les entrées quand les fichiers source changent

Usage :
  python3 semantic-cache.py --project-root . query --prompt "Review this auth module"
  python3 semantic-cache.py --project-root . store --prompt "..." --response "..." --type code-review
  python3 semantic-cache.py --project-root . stats
  python3 semantic-cache.py --project-root . clear --type formatting
  python3 semantic-cache.py --project-root . invalidate --files src/auth.py

Dépendances optionnelles :
  pip install qdrant-client sentence-transformers

Références :
  - GPTCache: https://github.com/zilliztech/GPTCache
  - Redis semantic cache: https://redis.io/docs/latest/develop/interact/search-and-query/advanced-concepts/vectors/
  - Anthropic Prompt Caching: https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ── Version ──────────────────────────────────────────────────────────────────

SEMANTIC_CACHE_VERSION = "1.0.0"

# ── Constants ────────────────────────────────────────────────────────────────

CHARS_PER_TOKEN = 4
DEFAULT_SIMILARITY_THRESHOLD = 0.90
CACHE_COLLECTION = "cache"
STATS_FILE = "_bmad-output/.semantic-cache-stats.json"

# TTL par type de requête (secondes)
DEFAULT_TTLS: dict[str, int] = {
    "formatting":     7 * 86400,     # 7 jours
    "summarization":  7 * 86400,     # 7 jours
    "code-review":    1 * 3600,      # 1 heure
    "architecture":   24 * 3600,     # 24 heures
    "coding":         2 * 3600,      # 2 heures
    "reasoning":      4 * 3600,      # 4 heures
    "embedding":      30 * 86400,    # 30 jours (embeddings stables)
    "default":        4 * 3600,      # 4 heures
}

# ── Data Classes ─────────────────────────────────────────────────────────────


@dataclass
class CacheEntry:
    """Une entrée du cache sémantique."""
    prompt_hash: str
    prompt_summary: str
    response: str
    query_type: str
    agent: str
    created_at: float
    ttl_seconds: int
    source_files: list[str] = field(default_factory=list)
    hit_count: int = 0
    tokens_saved: int = 0

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > self.ttl_seconds

    @property
    def age_hours(self) -> float:
        return round((time.time() - self.created_at) / 3600, 1)


@dataclass
class CacheResult:
    """Résultat d'une requête au cache."""
    hit: bool = False
    response: str = ""
    similarity: float = 0.0
    query_type: str = ""
    agent: str = ""
    age_hours: float = 0.0
    tokens_saved: int = 0
    cache_size: int = 0


@dataclass
class CacheStats:
    """Statistiques globales du cache."""
    total_entries: int = 0
    total_hits: int = 0
    total_misses: int = 0
    hit_rate: float = 0.0
    total_tokens_saved: int = 0
    entries_by_type: dict[str, int] = field(default_factory=dict)
    oldest_entry_hours: float = 0.0
    expired_count: int = 0
    qdrant_available: bool = False


# ── Stats Manager ───────────────────────────────────────────────────────────

class CacheStatsManager:
    """Gère les statistiques persistées du cache."""

    def __init__(self, stats_file: Path):
        self.stats_file = stats_file
        self._data = self._load()

    def _load(self) -> dict:
        if self.stats_file.exists():
            try:
                with open(self.stats_file, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {"hits": 0, "misses": 0, "tokens_saved": 0, "stores": 0}

    def _save(self) -> None:
        self.stats_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.stats_file, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)

    def record_hit(self, tokens_saved: int = 0) -> None:
        self._data["hits"] = self._data.get("hits", 0) + 1
        self._data["tokens_saved"] = self._data.get("tokens_saved", 0) + tokens_saved
        self._save()

    def record_miss(self) -> None:
        self._data["misses"] = self._data.get("misses", 0) + 1
        self._save()

    def record_store(self) -> None:
        self._data["stores"] = self._data.get("stores", 0) + 1
        self._save()

    @property
    def hits(self) -> int:
        return self._data.get("hits", 0)

    @property
    def misses(self) -> int:
        return self._data.get("misses", 0)

    @property
    def tokens_saved(self) -> int:
        return self._data.get("tokens_saved", 0)

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return round(self.hits / total, 3) if total > 0 else 0.0


# ── Semantic Cache ──────────────────────────────────────────────────────────

class SemanticCache:
    """
    Cache sémantique Qdrant pour les réponses LLM.

    Fonctionnement :
    1. Embed le prompt
    2. Cherche dans Qdrant les prompts similaires (cosine > threshold)
    3. Si hit + pas expiré : retourne la réponse cached
    4. Si miss : retourne None, le caller doit stocker la réponse après

    Dégradation gracieuse : si Qdrant pas disponible, toujours miss.
    """

    def __init__(
        self,
        project_root: Path,
        qdrant_url: str = "",
        qdrant_path: str = "",
        embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2",
        ollama_url: str = "",
        project_name: str = "bmad",
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
        ttls: dict[str, int] | None = None,
    ):
        self.project_root = project_root
        self.project_name = project_name
        self.threshold = similarity_threshold
        self.ttls = ttls or DEFAULT_TTLS
        self._qdrant_url = qdrant_url
        self._qdrant_path = qdrant_path or str(project_root / "_bmad-output" / ".qdrant_data")
        self._embedding_model = embedding_model
        self._ollama_url = ollama_url
        self._client = None
        self._embedder = None
        self._stats = CacheStatsManager(project_root / STATS_FILE)

    @property
    def collection_name(self) -> str:
        return f"{self.project_name}-{CACHE_COLLECTION}"

    def _init_qdrant(self) -> bool:
        if self._client is not None:
            return True
        try:
            from qdrant_client import QdrantClient
            if self._qdrant_url:
                self._client = QdrantClient(url=self._qdrant_url, timeout=5)
            else:
                if not Path(self._qdrant_path).parent.exists():
                    return False
                self._client = QdrantClient(path=self._qdrant_path)
            return True
        except Exception:
            return False

    def _init_embedder(self) -> bool:
        if self._embedder is not None:
            return True
        try:
            import importlib.util
            indexer_path = Path(__file__).parent / "rag-indexer.py"
            if indexer_path.exists():
                spec = importlib.util.spec_from_file_location("rag_indexer_cache", indexer_path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                self._embedder = mod.EmbeddingProvider(
                    model=self._embedding_model,
                    ollama_url=self._ollama_url,
                )
                return True
        except Exception:
            pass
        return False

    def _embed(self, texts: list[str]) -> list[list[float]]:
        if self._embedder:
            return self._embedder.embed(texts)
        return []

    def _ensure_collection(self) -> bool:
        if not self._init_qdrant():
            return False
        try:
            existing = [c.name for c in self._client.get_collections().collections]
            if self.collection_name not in existing:
                from qdrant_client.models import Distance, VectorParams
                self._client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=VectorParams(size=384, distance=Distance.COSINE),
                )
            return True
        except Exception:
            return False

    def _get_ttl(self, query_type: str) -> int:
        return self.ttls.get(query_type, self.ttls.get("default", 4 * 3600))

    def query(self, prompt: str, query_type: str = "default", agent: str = "") -> CacheResult:
        """Cherche une réponse dans le cache sémantique."""
        result = CacheResult()

        if not self._init_qdrant() or not self._init_embedder():
            self._stats.record_miss()
            return result

        if not self._ensure_collection():
            self._stats.record_miss()
            return result

        try:
            vectors = self._embed([prompt])
            if not vectors:
                self._stats.record_miss()
                return result

            hits = self._client.search(
                collection_name=self.collection_name,
                query_vector=vectors[0],
                limit=3,
            )

            now = time.time()
            for hit in hits:
                if hit.score < self.threshold:
                    continue

                created = hit.payload.get("created_at", 0)
                ttl = hit.payload.get("ttl_seconds", self._get_ttl(query_type))

                # Vérifier expiration
                if (now - created) > ttl:
                    continue

                # Hit!
                result.hit = True
                result.response = hit.payload.get("response", "")
                result.similarity = round(hit.score, 4)
                result.query_type = hit.payload.get("query_type", "")
                result.agent = hit.payload.get("agent", "")
                result.age_hours = round((now - created) / 3600, 1)
                result.tokens_saved = len(result.response) // CHARS_PER_TOKEN

                self._stats.record_hit(result.tokens_saved)
                return result

        except Exception:
            pass

        self._stats.record_miss()
        return result

    def store(
        self,
        prompt: str,
        response: str,
        query_type: str = "default",
        agent: str = "",
        source_files: list[str] | None = None,
    ) -> bool:
        """Stocke une paire prompt→response dans le cache."""
        if not self._init_qdrant() or not self._init_embedder():
            return False

        if not self._ensure_collection():
            return False

        try:
            vectors = self._embed([prompt])
            if not vectors:
                return False

            import uuid

            from qdrant_client.models import PointStruct

            point_id = str(uuid.uuid4())
            prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()[:16]

            self._client.upsert(
                collection_name=self.collection_name,
                points=[PointStruct(
                    id=point_id,
                    vector=vectors[0],
                    payload={
                        "prompt_hash": prompt_hash,
                        "prompt_summary": prompt[:200],
                        "response": response,
                        "query_type": query_type,
                        "agent": agent,
                        "created_at": time.time(),
                        "ttl_seconds": self._get_ttl(query_type),
                        "source_files": source_files or [],
                        "tokens_saved": len(response) // CHARS_PER_TOKEN,
                    },
                )],
            )
            self._stats.record_store()
            return True
        except Exception:
            return False

    def invalidate(self, source_files: list[str]) -> int:
        """Invalide les entrées liées à des fichiers modifiés."""
        if not self._init_qdrant():
            return 0

        try:
            from qdrant_client.models import FieldCondition, Filter, MatchAny
            count = 0

            # Chercher les points avec ces source_files
            points, _ = self._client.scroll(
                collection_name=self.collection_name,
                scroll_filter=Filter(
                    must=[FieldCondition(
                        key="source_files",
                        match=MatchAny(any=source_files),
                    )],
                ),
                limit=1000,
            )

            if points:
                from qdrant_client.models import PointIdsList
                ids = [str(p.id) for p in points]
                self._client.delete(
                    collection_name=self.collection_name,
                    points_selector=PointIdsList(points=ids),
                )
                count = len(ids)

            return count
        except Exception:
            return 0

    def clear(self, query_type: str = "") -> int:
        """Vide le cache (total ou filtré par type)."""
        if not self._init_qdrant():
            return 0

        try:
            if not query_type:
                # Clear all
                self._client.delete_collection(self.collection_name)
                self._ensure_collection()
                return -1  # All cleared signal

            from qdrant_client.models import FieldCondition, Filter, MatchValue
            points, _ = self._client.scroll(
                collection_name=self.collection_name,
                scroll_filter=Filter(
                    must=[FieldCondition(
                        key="query_type",
                        match=MatchValue(value=query_type),
                    )],
                ),
                limit=10000,
            )

            if points:
                from qdrant_client.models import PointIdsList
                ids = [str(p.id) for p in points]
                self._client.delete(
                    collection_name=self.collection_name,
                    points_selector=PointIdsList(points=ids),
                )
                return len(ids)
            return 0
        except Exception:
            return 0

    def get_stats(self) -> CacheStats:
        """Statistiques complètes du cache."""
        stats = CacheStats(
            total_hits=self._stats.hits,
            total_misses=self._stats.misses,
            hit_rate=self._stats.hit_rate,
            total_tokens_saved=self._stats.tokens_saved,
        )

        if not self._init_qdrant():
            return stats

        stats.qdrant_available = True

        try:
            existing = [c.name for c in self._client.get_collections().collections]
            if self.collection_name not in existing:
                return stats

            info = self._client.get_collection(self.collection_name)
            stats.total_entries = info.points_count

            # Scroll all to compute per-type stats and expiration
            points, _ = self._client.scroll(
                collection_name=self.collection_name,
                limit=10000,
            )

            now = time.time()
            oldest = now
            for p in points:
                qt = p.payload.get("query_type", "unknown")
                stats.entries_by_type[qt] = stats.entries_by_type.get(qt, 0) + 1

                created = p.payload.get("created_at", now)
                ttl = p.payload.get("ttl_seconds", 0)
                if created < oldest:
                    oldest = created
                if (now - created) > ttl:
                    stats.expired_count += 1

            stats.oldest_entry_hours = round((now - oldest) / 3600, 1) if points else 0

        except Exception:
            pass

        return stats


# ── Config Loading ──────────────────────────────────────────────────────────

def load_cache_config(project_root: Path) -> dict:
    """Charge la config depuis project-context.yaml."""
    try:
        import yaml
    except ImportError:
        return {}

    for candidate in [project_root / "project-context.yaml", project_root / "bmad.yaml"]:
        if candidate.exists():
            with open(candidate, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return data.get("semantic_cache", {})
    return {}


def build_cache_from_config(project_root: Path) -> SemanticCache:
    """Construit un SemanticCache depuis la config."""
    config = load_cache_config(project_root)
    return SemanticCache(
        project_root=project_root,
        qdrant_url=os.environ.get("BMAD_QDRANT_URL", config.get("qdrant_url", "")),
        embedding_model=config.get("embedding_model", "sentence-transformers/all-MiniLM-L6-v2"),
        ollama_url=os.environ.get("BMAD_OLLAMA_URL", config.get("ollama_url", "")),
        project_name=config.get("collection_prefix", "bmad"),
        similarity_threshold=config.get("similarity_threshold", DEFAULT_SIMILARITY_THRESHOLD),
    )


# ── CLI ─────────────────────────────────────────────────────────────────────

def _print_stats(stats: CacheStats) -> None:
    print("\n  📊 Semantic Cache — Stats")
    print(f"  {'─' * 50}")
    print(f"  Qdrant        : {'✅' if stats.qdrant_available else '❌'}")
    print(f"  Entrées       : {stats.total_entries}")
    print(f"  Hits / Misses : {stats.total_hits} / {stats.total_misses}")
    print(f"  Hit Rate      : {stats.hit_rate:.1%}")
    print(f"  Tokens Sauvés : {stats.total_tokens_saved:,}")
    print(f"  Expirés       : {stats.expired_count}")
    if stats.oldest_entry_hours:
        print(f"  Plus ancien   : {stats.oldest_entry_hours}h")

    if stats.entries_by_type:
        print("\n  Par type :")
        for qt, count in sorted(stats.entries_by_type.items(), key=lambda x: -x[1]):
            print(f"    {qt:20s} │ {count:>4d} entrées")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Semantic Cache — Cache sémantique Qdrant pour les réponses LLM BMAD",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project-root", type=Path, default=Path("."),
                        help="Racine du projet (défaut: .)")
    parser.add_argument("--version", action="version",
                        version=f"semantic-cache {SEMANTIC_CACHE_VERSION}")

    sub = parser.add_subparsers(dest="command", help="Commande à exécuter")

    # query
    q_p = sub.add_parser("query", help="Chercher dans le cache")
    q_p.add_argument("--prompt", required=True, help="Prompt à chercher")
    q_p.add_argument("--type", default="default", help="Type de requête")
    q_p.add_argument("--agent", default="", help="Agent ID")
    q_p.add_argument("--json", action="store_true", help="Output JSON")

    # store
    s_p = sub.add_parser("store", help="Stocker prompt→response")
    s_p.add_argument("--prompt", required=True, help="Prompt")
    s_p.add_argument("--response", required=True, help="Response")
    s_p.add_argument("--type", default="default", help="Type de requête")
    s_p.add_argument("--agent", default="", help="Agent ID")
    s_p.add_argument("--files", nargs="*", default=[], help="Fichiers source liés")

    # stats
    sub.add_parser("stats", help="Statistiques du cache")

    # clear
    c_p = sub.add_parser("clear", help="Vider le cache")
    c_p.add_argument("--type", default="", help="Type à vider (vide = tout)")

    # invalidate
    inv_p = sub.add_parser("invalidate", help="Invalider par fichiers source")
    inv_p.add_argument("--files", nargs="+", required=True, help="Fichiers modifiés")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    project_root = args.project_root.resolve()

    if args.command == "query":
        cache = build_cache_from_config(project_root)
        result = cache.query(args.prompt, args.type, args.agent)
        if getattr(args, "json", False):
            print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        else:
            if result.hit:
                print(f"\n  ✅ CACHE HIT — similarity={result.similarity} "
                      f"({result.age_hours}h ago)")
                print(f"  Tokens sauvés: {result.tokens_saved:,}")
                print(f"\n{result.response}\n")
            else:
                print("\n  ⚪ CACHE MISS\n")

    elif args.command == "store":
        cache = build_cache_from_config(project_root)
        ok = cache.store(args.prompt, args.response, args.type, args.agent, args.files)
        print(f"\n  {'✅ Stocké' if ok else '❌ Échec'}\n")

    elif args.command == "stats":
        cache = build_cache_from_config(project_root)
        stats = cache.get_stats()
        _print_stats(stats)

    elif args.command == "clear":
        cache = build_cache_from_config(project_root)
        count = cache.clear(args.type)
        if count == -1:
            print("\n  🗑️  Cache vidé intégralement\n")
        else:
            print(f"\n  🗑️  {count} entrées supprimées\n")

    elif args.command == "invalidate":
        cache = build_cache_from_config(project_root)
        count = cache.invalidate(args.files)
        print(f"\n  🔄 {count} entrées invalidées\n")


if __name__ == "__main__":
    main()
