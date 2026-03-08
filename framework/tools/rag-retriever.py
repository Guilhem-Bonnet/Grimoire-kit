#!/usr/bin/env python3
"""
rag-retriever.py — Retrieval sémantique Grimoire au runtime agent (BM-42 Story 2.3).
============================================================

Enrichit automatiquement le contexte agent avant chaque réponse en
injectant les chunks Qdrant les plus pertinents. S'intègre avec le
Context Router (BM-07) pour respecter le budget tokens.

Modes :
  retrieve    — Recherche et formate les chunks pour injection
  augment     — Augmente un prompt avec du contexte RAG
  preflight   — Vérifie que Qdrant est accessible et indexé
  benchmark   — Mesure la latence et la qualité du retrieval

Usage :
  python3 rag-retriever.py --project-root . retrieve --agent architect --query "auth system design"
  python3 rag-retriever.py --project-root . augment --agent dev --prompt "Implement login endpoint"
  python3 rag-retriever.py --project-root . preflight
  python3 rag-retriever.py --project-root . benchmark --queries "auth,terraform,memory"

Dépendances optionnelles :
  pip install qdrant-client sentence-transformers

Références :
  - RAG Best Practices (Anthropic) : https://docs.anthropic.com/en/docs/build-with-claude/retrieval-augmented-generation
  - RAPTOR (Stanford)              : https://arxiv.org/abs/2401.18059
  - ColBERT v2                     : https://github.com/stanford-futuredata/ColBERT
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ── Version ──────────────────────────────────────────────────────────────────

RAG_RETRIEVER_VERSION = "1.0.0"

# ── SSRF Protection ──────────────────────────────────────────────────────────

_BLOCKED_METADATA_HOSTS = frozenset({"169.254.169.254", "metadata.google.internal"})


def _validate_qdrant_url(url: str) -> str:
    """Validate Qdrant URL against SSRF (cloud metadata endpoints)."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"URL scheme '{parsed.scheme}' non autorisé")
    host = (parsed.hostname or "").lower()
    if host in _BLOCKED_METADATA_HOSTS:
        raise ValueError(f"URL bloquée (cloud metadata): {host}")
    return url

# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_MAX_CHUNKS = 5
DEFAULT_MIN_SCORE = 0.3
DEFAULT_MAX_CONTEXT_TOKENS = 4096
DEFAULT_RERANK_BOOST = {
    "agent_match": 0.15,       # Boost si source_file match agent
    "recent_memory": 0.10,     # Boost si collection = memory
    "heading_match": 0.10,     # Boost si heading contient un keyword de la query
    "decision_boost": 0.08,    # Boost si decisions-log
    "code_penalty": -0.05,     # Léger penalty pour code chunks (souvent trop verbeux)
}
CHARS_PER_TOKEN = 4

# Collections Qdrant standard (sync avec rag-indexer.py)
ALL_COLLECTIONS = ["agents", "memory", "docs", "code"]


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class RetrievedChunk:
    """Un chunk récupéré depuis Qdrant avec score et metadata."""
    text: str
    source_file: str
    chunk_type: str
    heading: str
    score: float
    collection: str
    rerank_score: float = 0.0
    estimated_tokens: int = 0

    @property
    def final_score(self) -> float:
        return round(self.score + self.rerank_score, 4)


@dataclass
class RetrievalResult:
    """Résultat complet d'un retrieval."""
    query: str
    agent: str
    chunks: list[RetrievedChunk] = field(default_factory=list)
    total_tokens: int = 0
    retrieval_time_ms: int = 0
    qdrant_available: bool = True
    fallback_used: bool = False

    @property
    def context_block(self) -> str:
        """Formate les chunks en bloc de contexte injectable."""
        if not self.chunks:
            return ""
        lines = [
            "<!-- RAG Context — auto-injected -->",
            f"<!-- {len(self.chunks)} chunks, {self.total_tokens} tokens, "
            f"{self.retrieval_time_ms}ms -->",
            "",
        ]
        for i, chunk in enumerate(self.chunks, 1):
            lines.append(f"### [{i}] {chunk.heading} ({chunk.source_file})")
            lines.append(f"> Score: {chunk.final_score} | Collection: {chunk.collection}")
            lines.append("")
            lines.append(chunk.text)
            lines.append("")
            lines.append("---")
            lines.append("")
        lines.append("<!-- /RAG Context -->")
        return "\n".join(lines)


@dataclass
class AugmentedPrompt:
    """Prompt augmenté avec contexte RAG."""
    original_prompt: str
    rag_context: str
    augmented_prompt: str
    retrieval: RetrievalResult
    budget_tokens_used: int = 0
    budget_pct: float = 0.0


@dataclass
class PreflightReport:
    """Rapport de vérification RAG."""
    qdrant_reachable: bool = False
    embedding_available: bool = False
    collections_status: dict = field(default_factory=dict)
    total_indexed_chunks: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return self.qdrant_reachable and self.embedding_available and not self.errors


# ── Reranker ─────────────────────────────────────────────────────────────────

class Reranker:
    """
    Reranking heuristique — boost/penalty basé sur metadata.
    Pas de ML, juste des heuristiques smart.
    """

    def __init__(self, boosts: dict[str, float] | None = None):
        self.boosts = boosts or DEFAULT_RERANK_BOOST

    def rerank(
        self,
        chunks: list[RetrievedChunk],
        query: str,
        agent_id: str = "",
    ) -> list[RetrievedChunk]:
        """Reranke les chunks par heuristiques contextuelles."""
        query_keywords = set(re.findall(r"\w+", query.lower()))

        for chunk in chunks:
            boost = 0.0

            # Agent match : source_file contient l'agent_id
            if agent_id and agent_id.lower() in chunk.source_file.lower():
                boost += self.boosts.get("agent_match", 0.0)

            # Memory collection boost
            if chunk.collection == "memory":
                boost += self.boosts.get("recent_memory", 0.0)

            # Heading match : heading contient des keywords de la query
            if chunk.heading:
                heading_words = set(re.findall(r"\w+", chunk.heading.lower()))
                overlap = query_keywords & heading_words
                if overlap:
                    boost += self.boosts.get("heading_match", 0.0) * len(overlap)

            # Decision boost
            if "decision" in chunk.source_file.lower():
                boost += self.boosts.get("decision_boost", 0.0)

            # Code penalty (souvent trop long et pas le plus utile)
            if chunk.collection == "code":
                boost += self.boosts.get("code_penalty", 0.0)

            chunk.rerank_score = round(boost, 4)

        # Re-sort par final_score
        chunks.sort(key=lambda c: c.final_score, reverse=True)
        return chunks


# ── RAG Retriever ────────────────────────────────────────────────────────────

class RAGRetriever:
    """
    Retriever sémantique — query Qdrant + reranking + budget tokens.

    Fallback gracieux : si Qdrant est down, retourne un résultat vide
    plutôt que de crasher. Le context-router utilise alors les fichiers bruts.
    """

    def __init__(
        self,
        project_root: Path,
        qdrant_url: str = "",
        qdrant_path: str = "",
        embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2",
        ollama_url: str = "",
        project_name: str = "grimoire",
        max_chunks: int = DEFAULT_MAX_CHUNKS,
        min_score: float = DEFAULT_MIN_SCORE,
        max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
    ):
        self.project_root = project_root
        self.project_name = project_name
        self.max_chunks = max_chunks
        self.min_score = min_score
        self.max_context_tokens = max_context_tokens
        self._reranker = Reranker()

        # Init Qdrant + embedding (lazy — only on first use)
        self._qdrant_url = qdrant_url
        self._qdrant_path = qdrant_path or str(project_root / "_grimoire-output" / ".qdrant_data")
        self._embedding_model = embedding_model
        self._ollama_url = ollama_url
        self._client = None
        self._embedder = None

    def _init_qdrant(self) -> bool:
        """Init lazy du client Qdrant. Retourne False si indisponible."""
        if self._client is not None:
            return True
        try:
            from qdrant_client import QdrantClient
            if self._qdrant_url:
                _validate_qdrant_url(self._qdrant_url)
                self._client = QdrantClient(url=self._qdrant_url, timeout=5)
            else:
                if not Path(self._qdrant_path).parent.exists():
                    return False
                self._client = QdrantClient(path=self._qdrant_path)
            return True
        except Exception:
            return False

    def _init_embedder(self) -> bool:
        """Init lazy de l'embedding provider."""
        if self._embedder is not None:
            return True
        try:
            # Import from rag-indexer (même module)
            import importlib.util
            indexer_path = self.project_root / "framework" / "tools" / "rag-indexer.py"
            if not indexer_path.exists():
                # Fallback : chercher relativement
                indexer_path = Path(__file__).parent / "rag-indexer.py"
            if indexer_path.exists():
                spec = importlib.util.spec_from_file_location("rag_indexer_mod", indexer_path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                self._embedder = mod.EmbeddingProvider(
                    model=self._embedding_model,
                    ollama_url=self._ollama_url,
                )
                return True
            # Fallback direct : sentence-transformers
            from sentence_transformers import SentenceTransformer
            self._st_model = SentenceTransformer(self._embedding_model)
            return True
        except (ImportError, Exception):
            return False

    def _embed(self, texts: list[str]) -> list[list[float]]:
        """Embed des textes via le provider disponible."""
        if self._embedder:
            return self._embedder.embed(texts)
        if hasattr(self, "_st_model"):
            return self._st_model.encode(texts).tolist()
        return []

    def _collection_name(self, collection: str) -> str:
        return f"{self.project_name}-{collection}"

    def retrieve(
        self,
        query: str,
        agent_id: str = "",
        collections: list[str] | None = None,
        max_chunks: int | None = None,
        min_score: float | None = None,
    ) -> RetrievalResult:
        """
        Recherche sémantique cross-collection avec reranking.

        Args:
            query: Requête en langage naturel
            agent_id: ID de l'agent (pour reranking boost)
            collections: Collections à chercher (défaut: toutes)
            max_chunks: Override du max chunks
            min_score: Override du score minimum
        """
        start = time.time()
        result = RetrievalResult(query=query, agent=agent_id)
        max_c = max_chunks or self.max_chunks
        min_s = min_score or self.min_score

        # Init Qdrant + embedder
        if not self._init_qdrant():
            result.qdrant_available = False
            result.fallback_used = True
            result.retrieval_time_ms = int((time.time() - start) * 1000)
            return result

        if not self._init_embedder():
            result.qdrant_available = True
            result.fallback_used = True
            result.retrieval_time_ms = int((time.time() - start) * 1000)
            return result

        # Embed query
        try:
            vectors = self._embed([query])
            if not vectors:
                result.fallback_used = True
                result.retrieval_time_ms = int((time.time() - start) * 1000)
                return result
            query_vector = vectors[0]
        except Exception:
            result.fallback_used = True
            result.retrieval_time_ms = int((time.time() - start) * 1000)
            return result

        # Search all collections
        search_collections = collections or ALL_COLLECTIONS
        raw_chunks: list[RetrievedChunk] = []

        for coll in search_collections:
            name = self._collection_name(coll)
            try:
                existing = [c.name for c in self._client.get_collections().collections]
                if name not in existing:
                    continue

                hits = self._client.search(
                    collection_name=name,
                    query_vector=query_vector,
                    limit=max_c * 2,  # Retrieve more for reranking
                )

                for hit in hits:
                    if hit.score >= min_s:
                        text = hit.payload.get("text", "")
                        raw_chunks.append(RetrievedChunk(
                            text=text,
                            source_file=hit.payload.get("source_file", ""),
                            chunk_type=hit.payload.get("chunk_type", ""),
                            heading=hit.payload.get("heading", ""),
                            score=round(hit.score, 4),
                            collection=coll,
                            estimated_tokens=len(text) // CHARS_PER_TOKEN,
                        ))
            except Exception:
                continue

        # Rerank
        raw_chunks = self._reranker.rerank(raw_chunks, query, agent_id)

        # Budget tokens : prendre les top chunks dans le budget
        budget_remaining = self.max_context_tokens
        for chunk in raw_chunks:
            if len(result.chunks) >= max_c:
                break
            if chunk.estimated_tokens <= budget_remaining:
                result.chunks.append(chunk)
                result.total_tokens += chunk.estimated_tokens
                budget_remaining -= chunk.estimated_tokens

        result.retrieval_time_ms = int((time.time() - start) * 1000)
        return result

    def augment_prompt(
        self,
        prompt: str,
        agent_id: str = "",
        model_window: int = 200_000,
    ) -> AugmentedPrompt:
        """
        Augmente un prompt avec du contexte RAG pertinent.

        Calcule le budget RAG comme fraction de la fenêtre du modèle,
        puis injecte les chunks les plus pertinents.
        """
        # Budget RAG : 5% de la fenêtre modèle ou max_context_tokens

        retrieval = self.retrieve(
            query=prompt,
            agent_id=agent_id,
        )

        rag_context = retrieval.context_block
        if rag_context:
            augmented = f"{rag_context}\n\n---\n\n{prompt}"
        else:
            augmented = prompt

        budget_used = retrieval.total_tokens
        budget_pct = round(budget_used / model_window * 100, 2) if model_window else 0.0

        return AugmentedPrompt(
            original_prompt=prompt,
            rag_context=rag_context,
            augmented_prompt=augmented,
            retrieval=retrieval,
            budget_tokens_used=budget_used,
            budget_pct=budget_pct,
        )

    def preflight(self) -> PreflightReport:
        """Vérifie que le système RAG est opérationnel."""
        report = PreflightReport()

        # Check Qdrant
        report.qdrant_reachable = self._init_qdrant()
        if not report.qdrant_reachable:
            report.errors.append("Qdrant non accessible (URL ou path invalide)")
            return report

        # Check embedding
        report.embedding_available = self._init_embedder()
        if not report.embedding_available:
            report.errors.append(
                "Embedding model non disponible — "
                "pip install sentence-transformers ou configurer Grimoire_OLLAMA_URL"
            )

        # Check collections
        try:
            for coll in ALL_COLLECTIONS:
                name = self._collection_name(coll)
                existing = [c.name for c in self._client.get_collections().collections]
                if name in existing:
                    info = self._client.get_collection(name)
                    count = info.points_count
                    report.collections_status[coll] = {
                        "exists": True,
                        "points": count,
                        "status": str(info.status),
                    }
                    report.total_indexed_chunks += count
                else:
                    report.collections_status[coll] = {
                        "exists": False,
                        "points": 0,
                        "status": "not_created",
                    }
        except Exception as e:
            report.errors.append(f"Erreur collections: {e}")

        if report.total_indexed_chunks == 0:
            report.errors.append(
                "Aucun chunk indexé — lancer: python3 rag-indexer.py --project-root . index --full"
            )

        return report


# ── Fallback : file-based retrieval ──────────────────────────────────────────

def file_based_fallback(
    project_root: Path,
    query: str,
    agent_id: str = "",
    max_chunks: int = DEFAULT_MAX_CHUNKS,
) -> RetrievalResult:
    """
    Fallback sans Qdrant — recherche par mots-clés dans les fichiers MD.
    Utilisé quand Qdrant n'est pas disponible.
    """
    start = time.time()
    result = RetrievalResult(query=query, agent=agent_id, qdrant_available=False, fallback_used=True)
    keywords = set(re.findall(r"\w{3,}", query.lower()))

    if not keywords:
        return result

    # Chercher dans les fichiers mémoire et docs
    search_dirs = [
        project_root / "_grimoire" / "_memory",
        project_root / "docs",
        project_root / "_grimoire-output" / "planning-artifacts",
    ]

    scored_chunks: list[tuple[float, RetrievedChunk]] = []

    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        for md_file in search_dir.rglob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            # Split par sections
            sections = re.split(r"^(#{1,3}\s+.+)$", content, flags=re.MULTILINE)
            current_heading = md_file.stem

            for section in sections:
                stripped = section.strip()
                if not stripped or len(stripped) < 30:
                    continue

                header_match = re.match(r"^#{1,3}\s+(.+)$", stripped)
                if header_match:
                    current_heading = header_match.group(1)
                    continue

                # Score par keyword overlap
                section_lower = stripped.lower()
                matches = sum(1 for kw in keywords if kw in section_lower)
                if matches == 0:
                    continue

                score = matches / len(keywords)
                relative = str(md_file.relative_to(project_root))

                scored_chunks.append((score, RetrievedChunk(
                    text=stripped[:2000],
                    source_file=relative,
                    chunk_type="section",
                    heading=current_heading,
                    score=round(score, 4),
                    collection="fallback",
                    estimated_tokens=len(stripped[:2000]) // CHARS_PER_TOKEN,
                )))

    # Top chunks
    scored_chunks.sort(key=lambda x: x[0], reverse=True)
    for _, chunk in scored_chunks[:max_chunks]:
        result.chunks.append(chunk)
        result.total_tokens += chunk.estimated_tokens

    result.retrieval_time_ms = int((time.time() - start) * 1000)
    return result


# ── Config Loading ──────────────────────────────────────────────────────────

def load_retriever_config(project_root: Path) -> dict:
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
            return data.get("rag", {})
    return {}


def build_retriever_from_config(project_root: Path) -> RAGRetriever:
    """Construit un RAGRetriever depuis la config."""
    config = load_retriever_config(project_root)

    return RAGRetriever(
        project_root=project_root,
        qdrant_url=os.environ.get("Grimoire_QDRANT_URL", config.get("qdrant_url", "")),
        embedding_model=config.get("embedding_model", "sentence-transformers/all-MiniLM-L6-v2"),
        ollama_url=os.environ.get("Grimoire_OLLAMA_URL", config.get("ollama_url", "")),
        project_name=config.get("collection_prefix", "grimoire"),
        max_chunks=config.get("max_chunks", DEFAULT_MAX_CHUNKS),
        min_score=config.get("min_score", DEFAULT_MIN_SCORE),
        max_context_tokens=config.get("max_context_tokens", DEFAULT_MAX_CONTEXT_TOKENS),
    )


# ── CLI ─────────────────────────────────────────────────────────────────────

def _print_retrieval(result: RetrievalResult) -> None:
    """Affiche un résultat de retrieval."""
    status = "✅" if result.qdrant_available else "⚠️ FALLBACK"
    print(f"\n  🔍 RAG Retrieval — {status}")
    print(f"  Agent: {result.agent} | Query: {result.query[:80]}")
    print(f"  Chunks: {len(result.chunks)} | Tokens: {result.total_tokens} | {result.retrieval_time_ms}ms")

    if not result.chunks:
        print("  Aucun résultat pertinent.\n")
        return

    print(f"  {'─' * 60}")
    for i, chunk in enumerate(result.chunks, 1):
        score_bar = "█" * int(chunk.final_score * 10)
        print(f"\n  [{i}] 📄 {chunk.source_file}")
        print(f"      Score: {chunk.final_score} {score_bar}")
        if chunk.rerank_score != 0:
            print(f"      Rerank: {chunk.score} → {chunk.final_score} ({chunk.rerank_score:+.4f})")
        print(f"      Type: {chunk.chunk_type} | Collection: {chunk.collection}")
        if chunk.heading:
            print(f"      Heading: {chunk.heading}")
        preview = chunk.text[:200].replace("\n", " ").strip()
        if len(chunk.text) > 200:
            preview += "..."
        print(f"      {preview}")
    print()


def _print_preflight(report: PreflightReport) -> None:
    """Affiche le rapport preflight."""
    status = "✅ HEALTHY" if report.healthy else "❌ ISSUES"
    print(f"\n  🏥 RAG Preflight — {status}")
    print(f"  {'─' * 50}")
    print(f"  Qdrant    : {'✅' if report.qdrant_reachable else '❌'}")
    print(f"  Embedding : {'✅' if report.embedding_available else '❌'}")
    print(f"  Chunks    : {report.total_indexed_chunks}")

    if report.collections_status:
        print(f"  {'─' * 50}")
        for coll, info in report.collections_status.items():
            icon = "✅" if info.get("exists") and info.get("points", 0) > 0 else "⚪"
            print(f"  {icon} {coll:12s} │ {info.get('points', 0):>6d} chunks │ {info.get('status', '?')}")

    if report.errors:
        print("\n  ⚠️  Problèmes détectés:")
        for err in report.errors:
            print(f"     → {err}")
    print()


def _print_augmented(aug: AugmentedPrompt) -> None:
    """Affiche le prompt augmenté."""
    print("\n  🧩 Prompt Augmenté")
    print(f"  {'─' * 60}")
    print(f"  Original  : {aug.original_prompt[:80]}...")
    print(f"  Chunks RAG: {len(aug.retrieval.chunks)}")
    print(f"  Tokens RAG: {aug.budget_tokens_used} ({aug.budget_pct}% du budget)")
    print(f"  Retrieval : {aug.retrieval.retrieval_time_ms}ms")

    if aug.rag_context:
        print("\n  --- Contexte RAG injecté ---")
        # Afficher les premières lignes
        for line in aug.rag_context.split("\n")[:20]:
            print(f"  {line}")
        if aug.rag_context.count("\n") > 20:
            print(f"  ... ({aug.rag_context.count(chr(10)) - 20} lignes de plus)")
    print()


def main() -> None:
    """Point d'entrée CLI."""
    parser = argparse.ArgumentParser(
        description="RAG Retriever — Retrieval sémantique Grimoire pour enrichir le contexte agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--project-root", type=Path, default=Path("."),
        help="Racine du projet (défaut: .)",
    )
    parser.add_argument("--version", action="version", version=f"rag-retriever {RAG_RETRIEVER_VERSION}")

    sub = parser.add_subparsers(dest="command", help="Commande à exécuter")

    # retrieve
    ret_p = sub.add_parser("retrieve", help="Recherche sémantique + reranking")
    ret_p.add_argument("--agent", default="", help="ID de l'agent")
    ret_p.add_argument("--query", required=True, help="Requête de recherche")
    ret_p.add_argument("--collection", choices=ALL_COLLECTIONS, help="Filtrer par collection")
    ret_p.add_argument("--max-chunks", type=int, help="Nombre max de chunks")
    ret_p.add_argument("--min-score", type=float, help="Score minimum")
    ret_p.add_argument("--json", action="store_true", help="Output JSON")
    ret_p.add_argument("--fallback", action="store_true", help="Forcer le mode fallback (sans Qdrant)")

    # augment
    aug_p = sub.add_parser("augment", help="Augmenter un prompt avec contexte RAG")
    aug_p.add_argument("--agent", default="", help="ID de l'agent")
    aug_p.add_argument("--prompt", required=True, help="Prompt à augmenter")
    aug_p.add_argument("--model-window", type=int, default=200_000, help="Fenêtre du modèle (tokens)")
    aug_p.add_argument("--json", action="store_true", help="Output JSON")

    # preflight
    sub.add_parser("preflight", help="Vérifier la disponibilité du système RAG")

    # benchmark
    bench_p = sub.add_parser("benchmark", help="Benchmark latence et qualité")
    bench_p.add_argument("--queries", required=True, help="Queries séparées par virgule")
    bench_p.add_argument("--agent", default="dev", help="Agent pour context")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    project_root = args.project_root.resolve()

    if args.command == "retrieve":
        if getattr(args, "fallback", False):
            result = file_based_fallback(
                project_root, args.query, args.agent, args.max_chunks or DEFAULT_MAX_CHUNKS,
            )
        else:
            try:
                retriever = build_retriever_from_config(project_root)
                collections = [args.collection] if args.collection else None
                result = retriever.retrieve(
                    query=args.query,
                    agent_id=args.agent,
                    collections=collections,
                    max_chunks=args.max_chunks,
                    min_score=args.min_score,
                )
                # Si Qdrant pas dispo, fallback auto
                if not result.qdrant_available:
                    result = file_based_fallback(
                        project_root, args.query, args.agent, args.max_chunks or DEFAULT_MAX_CHUNKS,
                    )
            except (ImportError, Exception):
                result = file_based_fallback(
                    project_root, args.query, args.agent, args.max_chunks or DEFAULT_MAX_CHUNKS,
                )

        if getattr(args, "json", False):
            print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        else:
            _print_retrieval(result)

    elif args.command == "augment":
        try:
            retriever = build_retriever_from_config(project_root)
            aug = retriever.augment_prompt(
                prompt=args.prompt,
                agent_id=args.agent,
                model_window=args.model_window,
            )
        except (ImportError, Exception):
            # Fallback
            fallback_result = file_based_fallback(project_root, args.prompt, args.agent)
            rag_ctx = fallback_result.context_block
            aug = AugmentedPrompt(
                original_prompt=args.prompt,
                rag_context=rag_ctx,
                augmented_prompt=f"{rag_ctx}\n\n---\n\n{args.prompt}" if rag_ctx else args.prompt,
                retrieval=fallback_result,
                budget_tokens_used=fallback_result.total_tokens,
            )

        if getattr(args, "json", False):
            out = {
                "original_prompt": aug.original_prompt,
                "augmented_prompt": aug.augmented_prompt,
                "chunks_count": len(aug.retrieval.chunks),
                "tokens_used": aug.budget_tokens_used,
                "budget_pct": aug.budget_pct,
                "retrieval_time_ms": aug.retrieval.retrieval_time_ms,
                "fallback_used": aug.retrieval.fallback_used,
            }
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            _print_augmented(aug)

    elif args.command == "preflight":
        try:
            retriever = build_retriever_from_config(project_root)
            report = retriever.preflight()
        except (ImportError, Exception) as e:
            report = PreflightReport(errors=[str(e)])
        _print_preflight(report)

    elif args.command == "benchmark":
        queries = [q.strip() for q in args.queries.split(",") if q.strip()]
        print(f"\n  ⏱️  RAG Benchmark — {len(queries)} queries")
        print(f"  {'─' * 60}")

        try:
            retriever = build_retriever_from_config(project_root)
        except (ImportError, Exception) as e:
            print(f"  ❌ {e}")
            sys.exit(1)

        total_time = 0
        total_chunks = 0
        for q in queries:
            result = retriever.retrieve(query=q, agent_id=args.agent)
            total_time += result.retrieval_time_ms
            total_chunks += len(result.chunks)
            status = "✅" if result.chunks else "⚪"
            print(f"  {status} \"{q}\" → {len(result.chunks)} chunks, {result.retrieval_time_ms}ms")

        avg_time = total_time / len(queries) if queries else 0
        print(f"\n  📊 Moyenne: {avg_time:.0f}ms/query, {total_chunks / len(queries):.1f} chunks/query")
        print()


if __name__ == "__main__":
    main()
