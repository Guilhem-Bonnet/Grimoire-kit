#!/usr/bin/env python3
"""
rag-indexer.py — Pipeline RAG d'indexation Grimoire → Qdrant (BM-42).
============================================================

Indexe tous les artifacts Grimoire (agents, mémoire, docs, PRDs, ADRs, code)
dans Qdrant avec metadata typée pour retrieval sémantique au runtime agent.

Modes :
  full        — Recrée les collections et indexe tout
  incremental — N'indexe que les fichiers modifiés (hash SHA256)
  status      — Affiche l'état des collections et stats
  search      — Recherche sémantique dans l'index
  export      — Exporte une collection en Markdown

Usage :
  python3 rag-indexer.py --project-root . index --full
  python3 rag-indexer.py --project-root . index --incremental
  python3 rag-indexer.py --project-root . status
  python3 rag-indexer.py --project-root . search --query "architecture authentification"
  python3 rag-indexer.py --project-root . search --query "terraform" --collection memory
  python3 rag-indexer.py --project-root . export --collection memory --output export.md

Dépendances optionnelles :
  pip install qdrant-client sentence-transformers  (mode local)
  pip install qdrant-client ollama                 (mode ollama)

Références :
  - Qdrant docs             : https://qdrant.tech/documentation/
  - nomic-embed-text        : https://huggingface.co/nomic-ai/nomic-embed-text-v1.5
  - LlamaIndex chunking     : https://docs.llamaindex.ai/en/stable/module_guides/loading/
  - RAPTOR (Stanford)        : https://arxiv.org/abs/2401.18059
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

_log = logging.getLogger("grimoire.rag_indexer")

# ── Version ──────────────────────────────────────────────────────────────────

RAG_INDEXER_VERSION = "1.0.0"

# ── Constants ────────────────────────────────────────────────────────────────

# SSRF-safe URL schemes and blocked IP ranges
_ALLOWED_SCHEMES = frozenset({"http", "https"})
_BLOCKED_IP_PREFIXES = (
    "169.254.",    # Link-local / cloud metadata
    "127.",        # Loopback
    "10.",         # Private Class A
    "172.16.", "172.17.", "172.18.", "172.19.",
    "172.20.", "172.21.", "172.22.", "172.23.",
    "172.24.", "172.25.", "172.26.", "172.27.",
    "172.28.", "172.29.", "172.30.", "172.31.",  # Private Class B
    "192.168.",    # Private Class C
    "0.",          # Unspecified
    "[::1]",       # IPv6 loopback
    "[fe80:",      # IPv6 link-local
)


def _validate_url(url: str, *, allow_localhost: bool = True) -> str:
    """Validate a URL against SSRF attacks.

    Blocks cloud metadata endpoints and optionally private IPs.
    Raises ValueError on invalid/dangerous URLs.
    """
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"URL scheme '{parsed.scheme}' non autorisé (http/https uniquement)")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("URL sans hostname")
    # Always block cloud metadata endpoints
    if host in ("169.254.169.254", "metadata.google.internal"):
        raise ValueError(f"URL bloquée (cloud metadata): {host}")
    if not allow_localhost and any(host.startswith(p) for p in _BLOCKED_IP_PREFIXES):
        raise ValueError(f"URL vers IP privée bloquée: {host}")
    return url

DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_VECTOR_SIZE = 384
DEFAULT_QDRANT_PATH = "_grimoire-output/.qdrant_data"
DEFAULT_MAX_CHUNK_TOKENS = 512
CHARS_PER_TOKEN = 4
HASH_INDEX_FILE = "_grimoire-output/.rag-index-hashes.json"

# Collection names (prefixed by project name at runtime)
COLLECTION_AGENTS = "agents"
COLLECTION_MEMORY = "memory"
COLLECTION_DOCS = "docs"
COLLECTION_CODE = "code"
COLLECTION_CACHE = "cache"

ALL_COLLECTIONS = [COLLECTION_AGENTS, COLLECTION_MEMORY, COLLECTION_DOCS, COLLECTION_CODE]

# File discovery patterns per collection
DISCOVERY_PATTERNS: dict[str, list[str]] = {
    COLLECTION_AGENTS: [
        "_grimoire/*/agents/*.md",
        "_grimoire/*/agents/**/*.md",
        "_grimoire/_config/custom/*.md",
    ],
    COLLECTION_MEMORY: [
        "_grimoire/_memory/*.md",
        "_grimoire/_memory/**/*.md",
        "_grimoire/_memory/*.json",
        "_grimoire/_memory/*.jsonl",
    ],
    COLLECTION_DOCS: [
        "docs/**/*.md",
        "_grimoire-output/planning-artifacts/*.md",
        "_grimoire-output/implementation-artifacts/*.md",
        "framework/*.md",
        "framework/**/*.md",
        "*.md",
    ],
    COLLECTION_CODE: [
        "framework/tools/*.py",
        "framework/memory/*.py",
        "framework/memory/backends/*.py",
        "tests/*.py",
    ],
}

# Excluded patterns (always)
EXCLUDE_PATTERNS: list[str] = [
    "node_modules", ".git", "__pycache__", ".pytest_cache",
    ".ruff_cache", ".venv", "venv", ".grimoire-rnd",
]

# Vector sizes for known models
VECTOR_SIZES: dict[str, int] = {
    "all-MiniLM-L6-v2": 384,
    "all-mpnet-base-v2": 768,
    "nomic-embed-text": 768,
    "nomic-embed-text-v1.5": 768,
    "mxbai-embed-large": 1024,
}


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class Chunk:
    """Un chunk de texte avec metadata."""
    text: str
    source_file: str
    chunk_type: str  # header | function | block | paragraph | full
    chunk_index: int = 0
    heading: str = ""
    estimated_tokens: int = 0
    metadata: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        """UUID5 déterministe basé sur source + texte tronqué."""
        key = f"{self.source_file}:{self.text[:150]}"
        return str(uuid.uuid5(uuid.NAMESPACE_OID, key))


@dataclass
class IndexReport:
    """Rapport d'indexation."""
    collection: str
    files_processed: int = 0
    files_skipped: int = 0
    chunks_created: int = 0
    chunks_upserted: int = 0
    errors: list[str] = field(default_factory=list)
    duration_ms: int = 0


@dataclass
class SearchResult:
    """Résultat de recherche sémantique."""
    text: str
    source_file: str
    chunk_type: str
    score: float
    heading: str = ""
    collection: str = ""


# ── Chunking Strategies ─────────────────────────────────────────────────────

class ChunkingStrategy:
    """Stratégies de chunking adaptatives par type de fichier."""

    @staticmethod
    def chunk_markdown(content: str, source_file: str, max_tokens: int = DEFAULT_MAX_CHUNK_TOKENS) -> list[Chunk]:
        """
        Découpe un fichier Markdown par header (## et ###).
        Conserve le contexte du header parent pour chaque chunk.
        """
        max_chars = max_tokens * CHARS_PER_TOKEN
        chunks: list[Chunk] = []
        current_heading = ""
        current_text_lines: list[str] = []
        chunk_idx = 0

        lines = content.split("\n")

        for line in lines:
            # Détecter les headers
            header_match = re.match(r"^(#{1,3})\s+(.+)", line)
            if header_match:
                # Flush le chunk courant
                if current_text_lines:
                    text = "\n".join(current_text_lines).strip()
                    if text and len(text) > 20:  # Ignorer les chunks trop courts
                        chunks.append(Chunk(
                            text=text,
                            source_file=source_file,
                            chunk_type="header",
                            chunk_index=chunk_idx,
                            heading=current_heading,
                            estimated_tokens=len(text) // CHARS_PER_TOKEN,
                        ))
                        chunk_idx += 1
                    current_text_lines = []

                current_heading = header_match.group(2).strip()
                current_text_lines.append(line)
            else:
                current_text_lines.append(line)

                # Si le chunk dépasse max_chars, couper
                joined = "\n".join(current_text_lines)
                if len(joined) > max_chars:
                    text = joined.strip()
                    if text:
                        chunks.append(Chunk(
                            text=text,
                            source_file=source_file,
                            chunk_type="header",
                            chunk_index=chunk_idx,
                            heading=current_heading,
                            estimated_tokens=len(text) // CHARS_PER_TOKEN,
                        ))
                        chunk_idx += 1
                    current_text_lines = []

        # Flush final
        if current_text_lines:
            text = "\n".join(current_text_lines).strip()
            if text and len(text) > 20:
                chunks.append(Chunk(
                    text=text,
                    source_file=source_file,
                    chunk_type="header",
                    chunk_index=chunk_idx,
                    heading=current_heading,
                    estimated_tokens=len(text) // CHARS_PER_TOKEN,
                ))

        # Si aucun chunk créé (pas de headers), traiter comme un seul bloc
        if not chunks and content.strip():
            chunks.append(Chunk(
                text=content.strip()[:max_chars],
                source_file=source_file,
                chunk_type="full",
                chunk_index=0,
                heading=source_file,
                estimated_tokens=min(len(content) // CHARS_PER_TOKEN, max_tokens),
            ))

        return chunks

    @staticmethod
    def chunk_python(content: str, source_file: str, max_tokens: int = DEFAULT_MAX_CHUNK_TOKENS) -> list[Chunk]:
        """
        Découpe un fichier Python par fonction et classe.
        Conserve la docstring comme contexte du chunk.
        """
        max_chars = max_tokens * CHARS_PER_TOKEN
        chunks: list[Chunk] = []
        chunk_idx = 0

        # Regex pour détecter les fonctions et classes au top-level
        pattern = re.compile(
            r"^((?:class|def|async\s+def)\s+\w+[^:]*:)\s*\n"
            r"((?:\s{4,}.+\n)*)",
            re.MULTILINE,
        )

        last_end = 0
        for match in pattern.finditer(content):
            # Capturer le code entre les fonctions (module-level)
            pre_text = content[last_end:match.start()].strip()
            if pre_text and len(pre_text) > 50:
                chunks.append(Chunk(
                    text=pre_text[:max_chars],
                    source_file=source_file,
                    chunk_type="block",
                    chunk_index=chunk_idx,
                    heading="module-level",
                    estimated_tokens=min(len(pre_text) // CHARS_PER_TOKEN, max_tokens),
                ))
                chunk_idx += 1

            # La fonction/classe elle-même
            func_text = match.group(0).strip()
            # Extraire le nom
            name_match = re.match(r"(?:class|def|async\s+def)\s+(\w+)", func_text)
            name = name_match.group(1) if name_match else "unknown"

            if func_text:
                chunks.append(Chunk(
                    text=func_text[:max_chars],
                    source_file=source_file,
                    chunk_type="function",
                    chunk_index=chunk_idx,
                    heading=name,
                    estimated_tokens=min(len(func_text) // CHARS_PER_TOKEN, max_tokens),
                ))
                chunk_idx += 1

            last_end = match.end()

        # Traiter le reste
        remaining = content[last_end:].strip()
        if remaining and len(remaining) > 50:
            chunks.append(Chunk(
                text=remaining[:max_chars],
                source_file=source_file,
                chunk_type="block",
                chunk_index=chunk_idx,
                heading="tail",
                estimated_tokens=min(len(remaining) // CHARS_PER_TOKEN, max_tokens),
            ))

        # Fallback : si rien trouvé, traiter comme bloc unique
        if not chunks and content.strip():
            chunks.append(Chunk(
                text=content.strip()[:max_chars],
                source_file=source_file,
                chunk_type="full",
                chunk_index=0,
                heading=source_file,
                estimated_tokens=min(len(content) // CHARS_PER_TOKEN, max_tokens),
            ))

        return chunks

    @staticmethod
    def chunk_yaml(content: str, source_file: str, max_tokens: int = DEFAULT_MAX_CHUNK_TOKENS) -> list[Chunk]:
        """Découpe un fichier YAML par bloc de premier niveau."""
        max_chars = max_tokens * CHARS_PER_TOKEN
        chunks: list[Chunk] = []
        chunk_idx = 0

        # Splitter par clés de premier niveau
        blocks = re.split(r"^(\w[\w_-]*:)", content, flags=re.MULTILINE)

        current_key = ""
        for _i, block in enumerate(blocks):
            stripped = block.strip()
            if not stripped:
                continue

            # Si c'est une clé de premier niveau
            if re.match(r"^\w[\w_-]*:$", stripped):
                current_key = stripped.rstrip(":")
                continue

            text = f"{current_key}:\n{block}" if current_key else block
            text = text.strip()

            if text and len(text) > 20:
                chunks.append(Chunk(
                    text=text[:max_chars],
                    source_file=source_file,
                    chunk_type="block",
                    chunk_index=chunk_idx,
                    heading=current_key or f"block-{chunk_idx}",
                    estimated_tokens=min(len(text) // CHARS_PER_TOKEN, max_tokens),
                ))
                chunk_idx += 1

        if not chunks and content.strip():
            chunks.append(Chunk(
                text=content.strip()[:max_chars],
                source_file=source_file,
                chunk_type="full",
                chunk_index=0,
                heading=source_file,
                estimated_tokens=min(len(content) // CHARS_PER_TOKEN, max_tokens),
            ))

        return chunks

    @classmethod
    def chunk_file(cls, filepath: Path, project_root: Path, max_tokens: int = DEFAULT_MAX_CHUNK_TOKENS) -> list[Chunk]:
        """Auto-détecte le type et chunk le fichier."""
        try:
            content = filepath.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return []

        relative = str(filepath.relative_to(project_root))
        suffix = filepath.suffix.lower()

        if suffix == ".md":
            return cls.chunk_markdown(content, relative, max_tokens)
        elif suffix == ".py":
            return cls.chunk_python(content, relative, max_tokens)
        elif suffix in (".yaml", ".yml"):
            return cls.chunk_yaml(content, relative, max_tokens)
        elif suffix == ".json":
            # JSON : chunk comme un bloc unique
            max_chars = max_tokens * CHARS_PER_TOKEN
            if content.strip():
                return [Chunk(
                    text=content.strip()[:max_chars],
                    source_file=relative,
                    chunk_type="full",
                    chunk_index=0,
                    heading=relative,
                    estimated_tokens=min(len(content) // CHARS_PER_TOKEN, max_tokens),
                )]
        return []


# ── Hash Index ───────────────────────────────────────────────────────────────

class HashIndex:
    """Index de hashes SHA256 pour l'indexation incrémentale."""

    def __init__(self, index_path: Path):
        self.index_path = index_path
        self._hashes: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if self.index_path.exists():
            try:
                with open(self.index_path, encoding="utf-8") as f:
                    self._hashes = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._hashes = {}

    def save(self) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(self._hashes, f, indent=2, ensure_ascii=False)

    def file_hash(self, filepath: Path) -> str:
        """Calcule le SHA256 d'un fichier."""
        try:
            return hashlib.sha256(filepath.read_bytes()).hexdigest()
        except OSError:
            return ""

    def needs_reindex(self, filepath: Path) -> bool:
        """Vérifie si un fichier a changé depuis la dernière indexation."""
        key = str(filepath)
        current_hash = self.file_hash(filepath)
        if not current_hash:
            return False
        if key not in self._hashes or self._hashes[key] != current_hash:
            return True
        return False

    def mark_indexed(self, filepath: Path) -> None:
        """Marque un fichier comme indexé."""
        self._hashes[str(filepath)] = self.file_hash(filepath)


# ── Embedding Abstraction ────────────────────────────────────────────────────

class EmbeddingProvider:
    """Abstraction pour les embeddings — sentence-transformers ou ollama."""

    def __init__(self, model: str = DEFAULT_EMBEDDING_MODEL, ollama_url: str = ""):
        self.model = model
        self.ollama_url = ollama_url
        self._st_model = None
        self._mode = "none"

        # Tenter sentence-transformers d'abord
        if not ollama_url:
            try:
                from sentence_transformers import SentenceTransformer
                self._st_model = SentenceTransformer(model)
                self._mode = "sentence-transformers"
                return
            except ImportError as _exc:
                _log.debug("ImportError suppressed: %s", _exc)
                # Silent exception — add logging when investigating issues

        # Sinon, ollama
        if ollama_url:
            self._mode = "ollama"
            return

        raise ImportError(
            "Aucun provider d'embedding disponible.\n"
            "Installer : pip install sentence-transformers\n"
            "Ou configurer Grimoire_OLLAMA_URL pour utiliser Ollama."
        )

    @property
    def vector_size(self) -> int:
        model_short = self.model.split("/")[-1]
        return VECTOR_SIZES.get(model_short, DEFAULT_VECTOR_SIZE)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Encode une liste de textes en vecteurs."""
        if self._mode == "sentence-transformers" and self._st_model:
            return self._st_model.encode(texts).tolist()
        elif self._mode == "ollama":
            return self._embed_ollama(texts)
        else:
            raise RuntimeError("No embedding provider available")

    def _embed_ollama(self, texts: list[str]) -> list[list[float]]:
        """Embeddings via Ollama API."""
        import urllib.request

        results = []
        model_short = self.model.split("/")[-1]
        for text in texts:
            data = json.dumps({"model": model_short, "prompt": text}).encode()
            req = urllib.request.Request(
                f"{self.ollama_url.rstrip('/')}/api/embeddings",
                data=data,
                headers={"Content-Type": "application/json"},
            )
            resp = urllib.request.urlopen(req, timeout=30)
            result = json.loads(resp.read().decode())
            results.append(result.get("embedding", []))
        return results


# ── RAG Indexer ──────────────────────────────────────────────────────────────

class RAGIndexer:
    """Pipeline d'indexation Grimoire → Qdrant."""

    def __init__(
        self,
        project_root: Path,
        qdrant_url: str = "",
        qdrant_path: str = "",
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        ollama_url: str = "",
        project_name: str = "grimoire",
        max_chunk_tokens: int = DEFAULT_MAX_CHUNK_TOKENS,
    ):
        self.project_root = project_root
        self.project_name = project_name
        self.max_chunk_tokens = max_chunk_tokens

        # Init Qdrant client
        try:
            from qdrant_client import QdrantClient
        except ImportError as exc:
            raise ImportError(
                "qdrant-client non installé.\n"
                "  pip install qdrant-client sentence-transformers"
            ) from exc

        if qdrant_url:
            _validate_url(qdrant_url)
            self._client = QdrantClient(url=qdrant_url, timeout=10)
        else:
            path = qdrant_path or str(project_root / DEFAULT_QDRANT_PATH)
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._client = QdrantClient(path=path)

        # Init embedding provider
        self._embedder = EmbeddingProvider(
            model=embedding_model,
            ollama_url=ollama_url,
        )

        # Hash index for incremental
        self._hash_index = HashIndex(project_root / HASH_INDEX_FILE)

    def _collection_name(self, collection: str) -> str:
        return f"{self.project_name}-{collection}"

    def _ensure_collection(self, collection: str) -> None:
        """Crée une collection Qdrant si elle n'existe pas."""
        from qdrant_client.models import Distance, VectorParams

        name = self._collection_name(collection)
        existing = [c.name for c in self._client.get_collections().collections]
        if name not in existing:
            self._client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(
                    size=self._embedder.vector_size,
                    distance=Distance.COSINE,
                ),
            )

    def _recreate_collection(self, collection: str) -> None:
        """Supprime et recrée une collection."""
        from qdrant_client.models import Distance, VectorParams

        name = self._collection_name(collection)
        existing = [c.name for c in self._client.get_collections().collections]
        if name in existing:
            self._client.delete_collection(name)
        self._client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(
                size=self._embedder.vector_size,
                distance=Distance.COSINE,
            ),
        )

    def _discover_files(self, collection: str) -> list[Path]:
        """Découvre les fichiers pour une collection."""
        patterns = DISCOVERY_PATTERNS.get(collection, [])
        files: set[Path] = set()

        for pattern in patterns:
            for f in self.project_root.glob(pattern):
                if f.is_file() and not any(excl in str(f) for excl in EXCLUDE_PATTERNS):
                    files.add(f)

        return sorted(files)

    def _upsert_chunks(self, collection: str, chunks: list[Chunk]) -> int:
        """Upsert les chunks dans Qdrant avec embeddings."""
        if not chunks:
            return 0

        from qdrant_client.models import PointStruct

        name = self._collection_name(collection)

        # Batch embed
        texts = [c.text for c in chunks]
        batch_size = 32
        all_vectors: list[list[float]] = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            vectors = self._embedder.embed(batch)
            all_vectors.extend(vectors)

        # Build points
        points: list = []
        for chunk, vector in zip(chunks, all_vectors, strict=True):
            payload = {
                "text": chunk.text,
                "source_file": chunk.source_file,
                "chunk_type": chunk.chunk_type,
                "heading": chunk.heading,
                "chunk_index": chunk.chunk_index,
                "estimated_tokens": chunk.estimated_tokens,
                "indexed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "project": self.project_name,
                **chunk.metadata,
            }
            points.append(PointStruct(
                id=chunk.id,
                vector=vector,
                payload=payload,
            ))

        # Upsert in batches
        upserted = 0
        for i in range(0, len(points), 100):
            batch = points[i:i + 100]
            self._client.upsert(collection_name=name, points=batch)
            upserted += len(batch)

        return upserted

    def index_collection(self, collection: str, full: bool = True) -> IndexReport:
        """Indexe une collection."""
        start = time.time()
        report = IndexReport(collection=collection)

        try:
            if full:
                self._recreate_collection(collection)
            else:
                self._ensure_collection(collection)

            files = self._discover_files(collection)

            for filepath in files:
                # Incremental : skip si pas changé
                if not full and not self._hash_index.needs_reindex(filepath):
                    report.files_skipped += 1
                    continue

                chunks = ChunkingStrategy.chunk_file(
                    filepath, self.project_root, self.max_chunk_tokens,
                )

                if chunks:
                    # Add collection metadata
                    for chunk in chunks:
                        chunk.metadata["collection_type"] = collection

                    upserted = self._upsert_chunks(collection, chunks)
                    report.chunks_created += len(chunks)
                    report.chunks_upserted += upserted
                    report.files_processed += 1
                    self._hash_index.mark_indexed(filepath)
                else:
                    report.files_skipped += 1

        except Exception as e:
            report.errors.append(f"{collection}: {e}")

        report.duration_ms = int((time.time() - start) * 1000)
        return report

    def index_all(self, full: bool = True) -> list[IndexReport]:
        """Indexe toutes les collections."""
        reports: list[IndexReport] = []
        for collection in ALL_COLLECTIONS:
            print(f"  📦 Indexation collection '{collection}'...")
            report = self.index_collection(collection, full=full)
            reports.append(report)
            status = "✅" if not report.errors else "⚠️"
            print(
                f"  {status} {collection}: {report.files_processed} fichiers, "
                f"{report.chunks_upserted} chunks, {report.duration_ms}ms"
            )

        self._hash_index.save()
        return reports

    def search(
        self,
        query: str,
        collection: str | None = None,
        limit: int = 5,
        min_score: float = 0.3,
    ) -> list[SearchResult]:
        """Recherche sémantique dans l'index."""
        vectors = self._embedder.embed([query])
        if not vectors:
            return []
        query_vector = vectors[0]

        results: list[SearchResult] = []
        collections_to_search = [collection] if collection else ALL_COLLECTIONS

        for coll in collections_to_search:
            name = self._collection_name(coll)
            try:
                existing = [c.name for c in self._client.get_collections().collections]
                if name not in existing:
                    continue

                hits = self._client.search(
                    collection_name=name,
                    query_vector=query_vector,
                    limit=limit,
                )

                for hit in hits:
                    if hit.score >= min_score:
                        results.append(SearchResult(
                            text=hit.payload.get("text", "")[:500],
                            source_file=hit.payload.get("source_file", ""),
                            chunk_type=hit.payload.get("chunk_type", ""),
                            score=round(hit.score, 4),
                            heading=hit.payload.get("heading", ""),
                            collection=coll,
                        ))
            except Exception as e:
                print(f"  ⚠️  Erreur recherche {name}: {e}")

        # Trier par score global
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    def status(self) -> dict:
        """Retourne l'état des collections."""
        status: dict = {
            "project": self.project_name,
            "embedding_model": self._embedder.model,
            "vector_size": self._embedder.vector_size,
            "collections": {},
        }

        for collection in ALL_COLLECTIONS:
            name = self._collection_name(collection)
            try:
                existing = [c.name for c in self._client.get_collections().collections]
                if name in existing:
                    info = self._client.get_collection(name)
                    status["collections"][collection] = {
                        "name": name,
                        "points_count": info.points_count,
                        "status": str(info.status),
                    }
                else:
                    status["collections"][collection] = {
                        "name": name,
                        "points_count": 0,
                        "status": "not_created",
                    }
            except Exception as e:
                status["collections"][collection] = {
                    "name": name,
                    "error": str(e),
                }

        return status

    def export_collection(self, collection: str, output: Path) -> int:
        """Exporte une collection en Markdown."""
        name = self._collection_name(collection)
        try:
            points, _ = self._client.scroll(
                collection_name=name,
                limit=10_000,
            )
        except Exception as e:
            print(f"  ❌ Erreur export {name}: {e}")
            return 0

        lines = [
            f"# Export Collection : {collection}",
            f"> Projet : {self.project_name}",
            f"> Date : {time.strftime('%Y-%m-%d %H:%M')}",
            f"> Entrées : {len(points)}",
            "",
            "---",
            "",
        ]

        current_file = ""
        for p in sorted(points, key=lambda x: x.payload.get("source_file", "")):
            payload = p.payload
            source = payload.get("source_file", "unknown")
            if source != current_file:
                current_file = source
                lines.append(f"## {source}")
                lines.append("")

            heading = payload.get("heading", "")
            text = payload.get("text", "")
            if heading:
                lines.append(f"### {heading}")
            lines.append(text)
            lines.append("")

        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("\n".join(lines), encoding="utf-8")
        return len(points)


# ── Config Loading ──────────────────────────────────────────────────────────

def load_rag_config(project_root: Path) -> dict:
    """Charge la config RAG depuis project-context.yaml."""
    try:
        import yaml
    except ImportError:
        return {}

    for candidate in [
        project_root / "project-context.yaml",
        project_root / "grimoire.yaml",
    ]:
        if candidate.exists():
            with open(candidate, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            rag = data.get("rag", data.get("memory", {}))
            return rag
    return {}


def build_indexer_from_config(project_root: Path) -> RAGIndexer:
    """Construit un RAGIndexer depuis la config du projet."""
    config = load_rag_config(project_root)

    qdrant_url = os.environ.get("Grimoire_QDRANT_URL", config.get("qdrant_url", ""))
    ollama_url = os.environ.get("Grimoire_OLLAMA_URL", config.get("ollama_url", ""))
    embedding_model = config.get("embedding_model", DEFAULT_EMBEDDING_MODEL)
    project_name = config.get("collection_prefix", "grimoire")
    max_tokens = config.get("max_chunk_tokens", DEFAULT_MAX_CHUNK_TOKENS)

    return RAGIndexer(
        project_root=project_root,
        qdrant_url=qdrant_url,
        embedding_model=embedding_model,
        ollama_url=ollama_url,
        project_name=project_name,
        max_chunk_tokens=max_tokens,
    )


# ── CLI ─────────────────────────────────────────────────────────────────────

def _print_status(indexer: RAGIndexer) -> None:
    """Affiche l'état des collections."""
    st = indexer.status()
    print(f"\n  🗄️  RAG Index Status — {st['project']}")
    print(f"  Embedding model : {st['embedding_model']} ({st['vector_size']}d)")
    print(f"  {'─' * 50}")
    for coll, info in st["collections"].items():
        if "error" in info:
            print(f"  ❌ {coll:12s} │ error: {info['error']}")
        else:
            count = info.get("points_count", 0)
            status = info.get("status", "unknown")
            icon = "✅" if count > 0 else "⚪"
            print(f"  {icon} {coll:12s} │ {count:>6d} chunks │ {status}")
    print()


def _print_search_results(results: list[SearchResult]) -> None:
    """Affiche les résultats de recherche."""
    if not results:
        print("\n  🔍 Aucun résultat trouvé.\n")
        return

    print(f"\n  🔍 {len(results)} résultats trouvés")
    print(f"  {'─' * 60}")
    for i, r in enumerate(results, 1):
        score_bar = "█" * int(r.score * 10)
        print(f"\n  [{i}] 📄 {r.source_file}")
        print(f"      Score: {r.score} {score_bar}")
        print(f"      Type: {r.chunk_type} | Collection: {r.collection}")
        if r.heading:
            print(f"      Heading: {r.heading}")
        # Tronquer le texte pour l'affichage
        preview = r.text[:200].replace("\n", " ").strip()
        if len(r.text) > 200:
            preview += "..."
        print(f"      {preview}")
    print()


def main() -> None:
    """Point d'entrée CLI."""
    parser = argparse.ArgumentParser(
        description="RAG Indexer — Pipeline d'indexation Grimoire → Qdrant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--project-root", type=Path, default=Path("."),
        help="Racine du projet (défaut: .)",
    )
    parser.add_argument("--version", action="version", version=f"rag-indexer {RAG_INDEXER_VERSION}")

    sub = parser.add_subparsers(dest="command", help="Commande à exécuter")

    # index
    index_p = sub.add_parser("index", help="Indexer les artifacts dans Qdrant")
    index_mode = index_p.add_mutually_exclusive_group(required=True)
    index_mode.add_argument("--full", action="store_true", help="Indexation complète (recrée les collections)")
    index_mode.add_argument("--incremental", action="store_true", help="Indexation incrémentale (fichiers modifiés)")
    index_p.add_argument("--collection", choices=ALL_COLLECTIONS, help="Indexer une seule collection")

    # status
    sub.add_parser("status", help="État des collections Qdrant")

    # search
    search_p = sub.add_parser("search", help="Recherche sémantique")
    search_p.add_argument("--query", required=True, help="Requête de recherche")
    search_p.add_argument("--collection", choices=ALL_COLLECTIONS, help="Filtrer par collection")
    search_p.add_argument("--limit", type=int, default=5, help="Nombre max de résultats")
    search_p.add_argument("--min-score", type=float, default=0.3, help="Score minimum")
    search_p.add_argument("--json", action="store_true", help="Output JSON")

    # export
    export_p = sub.add_parser("export", help="Exporter une collection en Markdown")
    export_p.add_argument("--collection", required=True, choices=ALL_COLLECTIONS, help="Collection à exporter")
    export_p.add_argument("--output", type=Path, required=True, help="Fichier de sortie")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        indexer = build_indexer_from_config(args.project_root)
    except ImportError as e:
        print(f"\n  ❌ {e}\n")
        sys.exit(1)

    if args.command == "index":
        full = getattr(args, "full", False)
        collection = getattr(args, "collection", None)

        print(f"\n  🚀 RAG Indexation {'FULL' if full else 'INCREMENTAL'}")
        print(f"  {'─' * 50}")

        if collection:
            report = indexer.index_collection(collection, full=full)
            reports = [report]
        else:
            reports = indexer.index_all(full=full)

        # Résumé
        total_files = sum(r.files_processed for r in reports)
        total_chunks = sum(r.chunks_upserted for r in reports)
        total_errors = sum(len(r.errors) for r in reports)
        total_time = sum(r.duration_ms for r in reports)

        print(f"\n  {'─' * 50}")
        print(f"  📊 Total : {total_files} fichiers, {total_chunks} chunks, {total_time}ms")
        if total_errors:
            print(f"  ⚠️  {total_errors} erreurs")
            for r in reports:
                for err in r.errors:
                    print(f"     → {err}")
        else:
            print("  ✅ Indexation terminée sans erreur")
        print()

    elif args.command == "status":
        _print_status(indexer)

    elif args.command == "search":
        results = indexer.search(
            query=args.query,
            collection=getattr(args, "collection", None),
            limit=args.limit,
            min_score=args.min_score,
        )
        if getattr(args, "json", False):
            print(json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2))
        else:
            _print_search_results(results)

    elif args.command == "export":
        count = indexer.export_collection(args.collection, args.output)
        print(f"\n  📄 Exporté {count} entrées → {args.output}\n")


if __name__ == "__main__":
    main()
