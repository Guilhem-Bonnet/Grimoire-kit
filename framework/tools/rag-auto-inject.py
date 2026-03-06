#!/usr/bin/env python3
"""
rag-auto-inject.py — Automatic RAG context injection for BMAD agents (D11).
═══════════════════════════════════════════════════════════════════

Hook qui injecte automatiquement du contexte RAG pertinent dans les
requêtes d'agent. Fonctionne en mode fichier (fallback) ou Qdrant.

Le hook analyse la requête utilisateur, détermine les termes de recherche
pertinents, et retourne les chunks de contexte les plus proches.

Modes :
  inject   — Injecter du contexte RAG dans une requête
  preview  — Prévisualiser les résultats sans injection
  config   — Afficher la configuration RAG

MCP interface :
  mcp_rag_auto_inject(query, max_chunks=3, project_root=".") → {context, chunks}

Usage :
  python3 rag-auto-inject.py --project-root . inject "Comment implémenter un agent ?"
  python3 rag-auto-inject.py --project-root . preview "workflow design"
  python3 rag-auto-inject.py --project-root . config

Stdlib only (fallback mode) — Qdrant optionnel.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

RAG_AUTO_INJECT_VERSION = "1.0.0"

# Default knowledge directories to search (relative to project root)
_KNOWLEDGE_DIRS = [
    "docs",
    "_bmad/_memory",
    "framework",
]

# File extensions for knowledge extraction
_KNOWLEDGE_EXTENSIONS = {".md", ".txt", ".yaml", ".yml"}

# Maximum chunk size in characters
_MAX_CHUNK_SIZE = 500


# ── Keyword Extraction ───────────────────────────────────────────

def _extract_keywords(query: str) -> list[str]:
    """Extract meaningful keywords from a query."""
    # Remove common French/English stop words
    stop_words = {
        "le", "la", "les", "de", "du", "des", "un", "une", "et", "ou",
        "en", "est", "ce", "qui", "que", "dans", "pour", "par", "sur",
        "avec", "pas", "plus", "tout", "mon", "son", "nous", "vous",
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "to", "of", "in", "for", "on", "with", "at", "by", "from",
        "how", "what", "when", "where", "which", "comment", "quoi",
    }

    words = re.findall(r"\b[a-zA-ZÀ-ÿ]{3,}\b", query.lower())
    return [w for w in words if w not in stop_words]


# ── File-Based RAG ──────────────────────────────────────────────

def _scan_knowledge_files(project_root: Path) -> list[Path]:
    """Scan knowledge directories for relevant files."""
    files: list[Path] = []
    for dir_name in _KNOWLEDGE_DIRS:
        d = project_root / dir_name
        if d.is_dir():
            for f in d.rglob("*"):
                if f.is_file() and f.suffix in _KNOWLEDGE_EXTENSIONS:
                    files.append(f)
    return files


def _chunk_file(file_path: Path, chunk_size: int = _MAX_CHUNK_SIZE) -> list[dict[str, str]]:
    """Split a file into chunks with metadata."""
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    chunks: list[dict[str, str]] = []

    # Split on headings for markdown
    if file_path.suffix == ".md":
        sections = re.split(r"(?=^#+\s)", content, flags=re.MULTILINE)
    else:
        # Split on double newlines for other files
        sections = content.split("\n\n")

    for section in sections:
        section = section.strip()
        if not section or len(section) < 20:
            continue
        if len(section) > chunk_size:
            # Sub-chunk
            for i in range(0, len(section), chunk_size):
                sub = section[i:i + chunk_size].strip()
                if sub:
                    chunks.append({
                        "text": sub,
                        "source": str(file_path.name),
                        "score": 0.0,
                    })
        else:
            chunks.append({
                "text": section,
                "source": str(file_path.name),
                "score": 0.0,
            })

    return chunks


def _score_chunk(chunk: dict[str, str], keywords: list[str]) -> float:
    """Score a chunk based on keyword overlap."""
    text_lower = chunk["text"].lower()
    matches = sum(1 for kw in keywords if kw in text_lower)
    if not keywords:
        return 0.0
    return matches / len(keywords)


def file_based_inject(project_root: Path, query: str,
                      max_chunks: int = 3) -> list[dict[str, Any]]:
    """File-based RAG injection (no external dependencies)."""
    keywords = _extract_keywords(query)
    if not keywords:
        return []

    files = _scan_knowledge_files(project_root)
    all_chunks: list[dict[str, str]] = []

    for f in files:
        all_chunks.extend(_chunk_file(f))

    # Score and rank
    for chunk in all_chunks:
        chunk["score"] = _score_chunk(chunk, keywords)

    # Filter and sort
    relevant = [c for c in all_chunks if c["score"] > 0]
    relevant.sort(key=lambda x: x["score"], reverse=True)

    return relevant[:max_chunks]


# ── Qdrant-based RAG (optional) ─────────────────────────────────

def _try_qdrant_inject(project_root: Path, query: str,
                       max_chunks: int = 3) -> list[dict[str, Any]] | None:
    """Try to use Qdrant via rag-retriever.py. Returns None if unavailable."""
    tools_dir = project_root / "framework" / "tools"
    retriever_path = tools_dir / "rag-retriever.py"
    if not retriever_path.exists():
        return None

    try:
        spec = importlib.util.spec_from_file_location(
            "_rag_retriever_inject", retriever_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        retriever = mod.build_retriever_from_config(project_root)
        result = retriever.retrieve(query, top_k=max_chunks)

        chunks = []
        for item in result.items:
            chunks.append({
                "text": item.text,
                "source": item.source,
                "score": item.score,
            })
        return chunks
    except Exception:
        return None


# ── Main Injection Logic ─────────────────────────────────────────

def auto_inject(project_root: Path, query: str,
                max_chunks: int = 3) -> dict[str, Any]:
    """Auto-inject RAG context for a query."""
    # Try Qdrant first
    chunks = _try_qdrant_inject(project_root, query, max_chunks)
    backend = "qdrant"

    if chunks is None:
        # Fallback to file-based
        chunks = file_based_inject(project_root, query, max_chunks)
        backend = "file"

    # Format context block
    context_parts = []
    for i, c in enumerate(chunks, 1):
        context_parts.append(
            f"[{i}] ({c['source']}, score={c['score']:.2f})\n{c['text']}"
        )

    return {
        "context": "\n\n---\n\n".join(context_parts) if context_parts else "",
        "chunks": chunks,
        "count": len(chunks),
        "backend": backend,
        "keywords": _extract_keywords(query),
    }


# ── MCP Interface ────────────────────────────────────────────────

def mcp_rag_auto_inject(query: str, max_chunks: int = 3,
                        project_root: str = ".") -> dict[str, Any]:
    """MCP tool: injection automatique de contexte RAG.

    Args:
        query: Requête utilisateur.
        max_chunks: Nombre maximum de chunks à injecter (1-10).
        project_root: Racine du projet.

    Returns:
        {context, chunks, count, backend, keywords}
    """
    max_chunks = max(1, min(10, max_chunks))
    return auto_inject(Path(project_root), query, max_chunks)


# ── Commands ─────────────────────────────────────────────────────

def cmd_inject(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    result = auto_inject(project_root, args.query, args.max_chunks)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"\n  🧠 RAG Auto-Inject ({result['backend']} backend)\n")
        print(f"  Keywords: {', '.join(result['keywords'])}")
        print(f"  Chunks:   {result['count']}\n")
        if result["context"]:
            print(result["context"])
        else:
            print("  No relevant context found.")
    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    result = auto_inject(project_root, args.query, args.max_chunks)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"\n  👁️  RAG Preview ({result['backend']})\n")
        for i, c in enumerate(result["chunks"], 1):
            print(f"  [{i}] {c['source']} (score={c['score']:.2f})")
            print(f"      {c['text'][:100]}...")
            print()
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    files = _scan_knowledge_files(project_root)

    config = {
        "knowledge_dirs": _KNOWLEDGE_DIRS,
        "extensions": sorted(_KNOWLEDGE_EXTENSIONS),
        "files_found": len(files),
        "max_chunk_size": _MAX_CHUNK_SIZE,
    }

    if args.json:
        print(json.dumps(config, indent=2, ensure_ascii=False))
    else:
        print("\n  ⚙️ RAG Auto-Inject Config\n")
        print(f"  Knowledge dirs:  {', '.join(_KNOWLEDGE_DIRS)}")
        print(f"  Extensions:      {', '.join(sorted(_KNOWLEDGE_EXTENSIONS))}")
        print(f"  Files found:     {len(files)}")
        print(f"  Max chunk size:  {_MAX_CHUNK_SIZE}")
    return 0


# ── Main ─────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="BMAD RAG Auto-Inject")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--json", action="store_true")

    subs = parser.add_subparsers(dest="command")

    p_inject = subs.add_parser("inject", help="Inject RAG context")
    p_inject.add_argument("query", help="Query to contextualize")
    p_inject.add_argument("--max-chunks", type=int, default=3)
    p_inject.set_defaults(func=cmd_inject)

    p_preview = subs.add_parser("preview", help="Preview RAG results")
    p_preview.add_argument("query", help="Query to preview")
    p_preview.add_argument("--max-chunks", type=int, default=3)
    p_preview.set_defaults(func=cmd_preview)

    p_config = subs.add_parser("config", help="Show RAG config")
    p_config.set_defaults(func=cmd_config)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
