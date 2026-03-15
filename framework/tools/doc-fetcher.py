#!/usr/bin/env python3
"""
doc-fetcher.py — Indexation de documentation externe pour RAG Grimoire.
=====================================================================

Comparable au `@docs` de Cursor : indexe des sites de documentation
externes (React, Django, Terraform, etc.) dans le RAG local pour
enrichir les réponses des agents avec des références à jour.

Features :
  - fetch     — Télécharge et indexe une source de docs
  - list      — Liste les sources configurées et leur état
  - search    — Recherche dans les docs indexées
  - remove    — Supprime une source indexée
  - refresh   — Rafraîchit toutes les sources

Configuration dans project-context.yaml :
  external_docs:
    - name: "python"
      url: "https://docs.python.org/3/"
      paths: ["library/index.html", "reference/index.html"]
    - name: "react"
      url: "https://react.dev/"
      paths: ["reference/react"]

Usage :
  python3 doc-fetcher.py --project-root . fetch --name python --url https://docs.python.org/3/ --paths library/pathlib.html
  python3 doc-fetcher.py --project-root . list
  python3 doc-fetcher.py --project-root . search "pathlib Path"
  python3 doc-fetcher.py --project-root . remove python
  python3 doc-fetcher.py --project-root . refresh
  python3 doc-fetcher.py --project-root . --json

Stdlib only — aucune dépendance externe.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path

_log = logging.getLogger("grimoire.doc_fetcher")

# ── Constantes ────────────────────────────────────────────────────────────────

DOC_FETCHER_VERSION = "1.0.0"

DOCS_CACHE_DIR = "_grimoire-output/.doc-cache"
DOCS_INDEX_FILE = "_grimoire-output/.doc-index.json"
_USER_AGENT = "Grimoire-DocFetcher/1.0 (stdlib; documentation indexer)"
_TIMEOUT = 15  # seconds
_MAX_PAGE_SIZE = 500_000  # 500 KB max par page
_MAX_PAGES_PER_SOURCE = 50  # Limite de pages par source

# SSRF protection
_ALLOWED_SCHEMES = frozenset({"http", "https"})
_BLOCKED_HOSTS = frozenset({
    "169.254.169.254",
    "metadata.google.internal",
    "metadata.internal",
})
_BLOCKED_IP_PREFIXES = (
    "169.254.", "127.", "10.",
    "172.16.", "172.17.", "172.18.", "172.19.",
    "172.20.", "172.21.", "172.22.", "172.23.",
    "172.24.", "172.25.", "172.26.", "172.27.",
    "172.28.", "172.29.", "172.30.", "172.31.",
    "192.168.", "0.", "[::1]", "[fe80:",
)


# ── Security ──────────────────────────────────────────────────────────────────

def _validate_url(url: str) -> str:
    """Valide une URL contre les attaques SSRF."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"Schéma URL '{parsed.scheme}' non autorisé (http/https uniquement)")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("URL sans hostname")
    if host in _BLOCKED_HOSTS:
        raise ValueError(f"URL bloquée (cloud metadata): {host}")
    if any(host.startswith(p) for p in _BLOCKED_IP_PREFIXES):
        raise ValueError(f"URL vers IP privée bloquée: {host}")
    return url


# ── HTML Parser ───────────────────────────────────────────────────────────────

class _ContentExtractor(HTMLParser):
    """Extrait le texte lisible d'une page HTML."""

    SKIP_TAGS = frozenset({"script", "style", "nav", "header", "footer",
                           "aside", "noscript", "svg", "form"})

    def __init__(self):
        super().__init__()
        self.text_parts: list[str] = []
        self.title = ""
        self._skip_depth = 0
        self._in_title = False
        self._current_tag = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._current_tag = tag
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        # Add newlines for block elements
        if tag in ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
                    "li", "tr", "br", "hr", "pre", "blockquote"):
            self.text_parts.append("\n")
        # Heading markers
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag[1])
            self.text_parts.append("#" * level + " ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title and not self.title:
            self.title = data.strip()
        if self._skip_depth == 0:
            self.text_parts.append(data)

    def get_text(self) -> str:
        """Retourne le texte nettoyé."""
        raw = "".join(self.text_parts)
        # Normaliser les espaces
        lines = []
        for line in raw.splitlines():
            stripped = line.strip()
            if stripped:
                lines.append(stripped)
        return "\n".join(lines)


def extract_text_from_html(html: str) -> tuple[str, str]:
    """Extrait le texte et le titre d'une page HTML.

    Returns: (text, title)
    """
    parser = _ContentExtractor()
    parser.feed(html)
    return parser.get_text(), parser.title


# ── Data Classes ──────────────────────────────────────────────────────────────

@dataclass
class DocPage:
    """Une page de documentation indexée."""
    url: str
    title: str
    text: str
    hash: str
    fetched_at: str
    size: int = 0

    @property
    def id(self) -> str:
        return hashlib.sha256(self.url.encode()).hexdigest()[:16]


@dataclass
class DocSource:
    """Une source de documentation."""
    name: str
    base_url: str
    paths: list[str] = field(default_factory=list)
    pages: list[DocPage] = field(default_factory=list)
    last_refresh: str = ""
    total_chunks: int = 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "base_url": self.base_url,
            "paths": self.paths,
            "pages_count": len(self.pages),
            "last_refresh": self.last_refresh,
            "total_chunks": self.total_chunks,
        }


@dataclass
class DocIndex:
    """Index global des docs."""
    version: str = DOC_FETCHER_VERSION
    sources: dict[str, DocSource] = field(default_factory=dict)


@dataclass
class FetchReport:
    """Rapport de fetch."""
    source_name: str = ""
    pages_fetched: int = 0
    pages_cached: int = 0
    pages_failed: int = 0
    chunks_created: int = 0
    errors: list[str] = field(default_factory=list)
    duration_ms: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


# ── Index Persistence ─────────────────────────────────────────────────────────

def load_doc_index(project_root: Path) -> DocIndex:
    """Charge l'index des docs."""
    index_path = project_root / DOCS_INDEX_FILE
    if not index_path.exists():
        return DocIndex()

    try:
        with open(index_path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return DocIndex()

    idx = DocIndex(version=data.get("version", DOC_FETCHER_VERSION))
    for name, src_data in data.get("sources", {}).items():
        source = DocSource(
            name=name,
            base_url=src_data.get("base_url", ""),
            paths=src_data.get("paths", []),
            last_refresh=src_data.get("last_refresh", ""),
            total_chunks=src_data.get("total_chunks", 0),
        )
        for pg in src_data.get("pages", []):
            source.pages.append(DocPage(
                url=pg["url"],
                title=pg.get("title", ""),
                text="",  # Text stocké en fichier cache, pas dans l'index
                hash=pg.get("hash", ""),
                fetched_at=pg.get("fetched_at", ""),
                size=pg.get("size", 0),
            ))
        idx.sources[name] = source

    return idx


def save_doc_index(project_root: Path, idx: DocIndex) -> None:
    """Sauvegarde l'index des docs."""
    index_path = project_root / DOCS_INDEX_FILE
    index_path.parent.mkdir(parents=True, exist_ok=True)

    data: dict = {
        "version": idx.version,
        "sources": {},
    }
    for name, src in idx.sources.items():
        data["sources"][name] = {
            "base_url": src.base_url,
            "paths": src.paths,
            "last_refresh": src.last_refresh,
            "total_chunks": src.total_chunks,
            "pages": [
                {
                    "url": p.url,
                    "title": p.title,
                    "hash": p.hash,
                    "fetched_at": p.fetched_at,
                    "size": p.size,
                }
                for p in src.pages
            ],
        }

    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ── Fetcher ───────────────────────────────────────────────────────────────────

def _fetch_page(url: str) -> str:
    """Télécharge une page HTML."""
    _validate_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            content_length = resp.headers.get("Content-Length")
            if content_length and int(content_length) > _MAX_PAGE_SIZE:
                raise ValueError(f"Page trop grande: {content_length} bytes")
            data = resp.read(_MAX_PAGE_SIZE + 1)
            if len(data) > _MAX_PAGE_SIZE:
                data = data[:_MAX_PAGE_SIZE]
            charset = resp.headers.get_content_charset() or "utf-8"
            return data.decode(charset, errors="replace")
    except urllib.error.URLError as e:
        raise ConnectionError(f"Impossible de télécharger {url}: {e}") from e


def _cache_page(project_root: Path, source_name: str, page: DocPage) -> Path:
    """Sauvegarde une page en cache local."""
    cache_dir = project_root / DOCS_CACHE_DIR / source_name
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Nom de fichier basé sur le hash de l'URL
    filename = f"{page.id}.md"
    filepath = cache_dir / filename

    # Convertir en Markdown-like
    content = f"# {page.title}\n\nSource: {page.url}\n\n---\n\n{page.text}"
    filepath.write_text(content, encoding="utf-8")
    return filepath


def _load_cached_text(project_root: Path, source_name: str, page: DocPage) -> str:
    """Charge le texte d'une page depuis le cache."""
    cache_dir = project_root / DOCS_CACHE_DIR / source_name
    filepath = cache_dir / f"{page.id}.md"
    if filepath.exists():
        return filepath.read_text(encoding="utf-8")
    return ""


def _chunk_text(text: str, max_chars: int = 2000) -> list[str]:
    """Découpe le texte en chunks pour la recherche."""
    chunks: list[str] = []
    paragraphs = re.split(r"\n{2,}", text)

    current: list[str] = []
    current_len = 0

    for p in paragraphs:
        if current_len + len(p) > max_chars and current:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0

        current.append(p)
        current_len += len(p)

    if current:
        chunks.append("\n\n".join(current))

    return chunks


def fetch_source(project_root: Path, name: str, base_url: str,
                 paths: list[str]) -> tuple[DocSource, FetchReport]:
    """Télécharge et indexe une source de documentation."""
    report = FetchReport(source_name=name)
    start = time.monotonic()

    from datetime import datetime
    now = datetime.now().isoformat()

    source = DocSource(name=name, base_url=base_url, paths=paths)

    # Construire les URLs
    urls: list[str] = []
    if paths:
        for path in paths[:_MAX_PAGES_PER_SOURCE]:
            url = base_url.rstrip("/") + "/" + path.lstrip("/")
            urls.append(url)
    else:
        urls.append(base_url)

    for url in urls:
        try:
            html = _fetch_page(url)
            text, title = extract_text_from_html(html)

            if not text.strip():
                report.errors.append(f"Page vide: {url}")
                report.pages_failed += 1
                continue

            content_hash = hashlib.sha256(text.encode()).hexdigest()[:16]

            page = DocPage(
                url=url,
                title=title or Path(urllib.parse.urlparse(url).path).stem,
                text=text,
                hash=content_hash,
                fetched_at=now,
                size=len(text),
            )

            _cache_page(project_root, name, page)
            chunks = _chunk_text(text)
            source.total_chunks += len(chunks)
            report.chunks_created += len(chunks)

            # Stocker sans le texte complet (il est en cache)
            page.text = ""
            source.pages.append(page)
            report.pages_fetched += 1

        except (ConnectionError, ValueError, OSError) as e:
            report.errors.append(str(e))
            report.pages_failed += 1

    source.last_refresh = now
    report.duration_ms = int((time.monotonic() - start) * 1000)
    return source, report


# ── Search ────────────────────────────────────────────────────────────────────

def search_docs(project_root: Path, query: str, source_filter: str | None = None,
                max_results: int = 5) -> list[dict]:
    """Recherche basique dans les docs indexées (TF-IDF simplifié)."""
    idx = load_doc_index(project_root)
    query_terms = set(query.lower().split())
    results: list[tuple[float, dict]] = []

    for name, source in idx.sources.items():
        if source_filter and name != source_filter:
            continue

        for page in source.pages:
            text = _load_cached_text(project_root, name, page)
            if not text:
                continue

            # Score basique : fréquence des termes de la query
            text_lower = text.lower()
            score = 0.0
            for term in query_terms:
                count = text_lower.count(term)
                if count > 0:
                    score += count / max(len(text_lower.split()), 1)

            if score > 0:
                # Trouver le meilleur snippet
                snippet = _find_best_snippet(text, query_terms)
                results.append((score, {
                    "source": name,
                    "url": page.url,
                    "title": page.title,
                    "score": round(score, 4),
                    "snippet": snippet,
                }))

    # Trier par score décroissant
    results.sort(key=lambda x: x[0], reverse=True)
    return [r[1] for r in results[:max_results]]


def _find_best_snippet(text: str, query_terms: set[str], context: int = 200) -> str:
    """Trouve le meilleur extrait contenant les termes de la query."""
    text_lower = text.lower()
    best_pos = 0
    best_score = 0

    for term in query_terms:
        pos = text_lower.find(term)
        if pos >= 0:
            # Compter combien de termes sont proches
            window = text_lower[max(0, pos - context):pos + context]
            score = sum(1 for t in query_terms if t in window)
            if score > best_score:
                best_score = score
                best_pos = pos

    start = max(0, best_pos - context // 2)
    end = min(len(text), best_pos + context)
    snippet = text[start:end].strip()

    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet = snippet + "..."

    return snippet.replace("\n", " ")


# ── Display ───────────────────────────────────────────────────────────────────

def format_list(idx: DocIndex, as_json: bool = False) -> str:
    """Affiche la liste des sources."""
    if as_json:
        return json.dumps(
            {n: s.to_dict() for n, s in idx.sources.items()},
            indent=2, ensure_ascii=False,
        )

    if not idx.sources:
        return "\n  📚 Aucune source de documentation indexée.\n"

    lines: list[str] = []
    lines.append("\n  📚 Sources de documentation indexées")
    lines.append(f"  {'─' * 50}")

    for name, source in sorted(idx.sources.items()):
        lines.append(f"\n  📖 {name}")
        lines.append(f"     URL   : {source.base_url}")
        lines.append(f"     Pages : {len(source.pages)}")
        lines.append(f"     Chunks: {source.total_chunks}")
        lines.append(f"     MAJ   : {source.last_refresh or 'jamais'}")

    lines.append("")
    return "\n".join(lines)


def format_search_results(results: list[dict], as_json: bool = False) -> str:
    """Affiche les résultats de recherche."""
    if as_json:
        return json.dumps(results, indent=2, ensure_ascii=False)

    if not results:
        return "\n  🔍 Aucun résultat trouvé.\n"

    lines: list[str] = []
    lines.append(f"\n  🔍 {len(results)} résultat(s)")
    lines.append(f"  {'─' * 55}")

    for i, r in enumerate(results, 1):
        lines.append(f"\n  [{i}] 📄 {r['title']}")
        lines.append(f"      Source: {r['source']} — {r['url']}")
        lines.append(f"      Score : {r['score']}")
        lines.append(f"      {r['snippet'][:200]}")

    lines.append("")
    return "\n".join(lines)


# ── Config Loading ────────────────────────────────────────────────────────────

def load_docs_config(project_root: Path) -> list[dict]:
    """Charge la config des docs externes depuis project-context.yaml."""
    for candidate in [
        project_root / "project-context.yaml",
        project_root / "grimoire.yaml",
    ]:
        if not candidate.exists():
            continue
        try:
            # Parse YAML minimalement sans dépendance
            content = candidate.read_text(encoding="utf-8")
            # Chercher la section external_docs
            in_section = False
            docs: list[dict] = []
            current: dict = {}

            for line in content.splitlines():
                stripped = line.strip()
                if stripped == "external_docs:":
                    in_section = True
                    continue
                if in_section:
                    if not stripped or (not line.startswith(" ") and not line.startswith("\t")):
                        if not stripped.startswith("-") and not stripped.startswith("name:"):
                            break
                    if stripped.startswith("- name:"):
                        if current:
                            docs.append(current)
                        current = {"name": stripped.split(":", 1)[1].strip().strip('"').strip("'")}
                    elif stripped.startswith("url:") and current:
                        current["url"] = stripped.split(":", 1)[1].strip().strip('"').strip("'")
                    elif stripped.startswith("paths:") and current:
                        current["paths"] = []
                    elif stripped.startswith("- ") and "paths" in current:
                        current["paths"].append(stripped[2:].strip().strip('"').strip("'"))

            if current:
                docs.append(current)
            return docs

        except OSError:
            continue
    return []


# ── CLI ───────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Doc Fetcher — Indexation de documentation externe Grimoire",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project-root", type=Path, default=Path(),
                        help="Racine du projet")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--version", action="version",
                        version=f"doc-fetcher {DOC_FETCHER_VERSION}")

    sub = parser.add_subparsers(dest="command", help="Commande")

    # fetch
    fetch_p = sub.add_parser("fetch", help="Télécharger et indexer une source")
    fetch_p.add_argument("--name", required=True, help="Nom de la source")
    fetch_p.add_argument("--url", required=True, help="URL de base")
    fetch_p.add_argument("--paths", nargs="*", default=[], help="Chemins relatifs à indexer")

    # list
    sub.add_parser("list", help="Lister les sources indexées")

    # search
    search_p = sub.add_parser("search", help="Rechercher dans les docs")
    search_p.add_argument("query", help="Requête de recherche")
    search_p.add_argument("--source", help="Filtrer par source")
    search_p.add_argument("--limit", type=int, default=5, help="Nombre max de résultats")

    # remove
    rm_p = sub.add_parser("remove", help="Supprimer une source")
    rm_p.add_argument("name", help="Nom de la source à supprimer")

    # refresh
    sub.add_parser("refresh", help="Rafraîchir toutes les sources")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    project_root = args.project_root.resolve()
    as_json = getattr(args, "json", False)

    if args.command == "fetch":
        _validate_url(args.url)  # Valider avant tout
        idx = load_doc_index(project_root)
        source, report = fetch_source(project_root, args.name, args.url, args.paths)
        idx.sources[args.name] = source
        save_doc_index(project_root, idx)

        if as_json:
            print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        else:
            print(f"\n  📖 {args.name} — {report.pages_fetched} page(s) indexée(s), "
                  f"{report.chunks_created} chunks en {report.duration_ms}ms")
            if report.errors:
                for e in report.errors:
                    print(f"     ⚠️  {e}")
            print()

    elif args.command == "list":
        idx = load_doc_index(project_root)
        print(format_list(idx, as_json))

    elif args.command == "search":
        results = search_docs(project_root, args.query,
                              getattr(args, "source", None),
                              getattr(args, "limit", 5))
        print(format_search_results(results, as_json))

    elif args.command == "remove":
        idx = load_doc_index(project_root)
        name = args.name
        if name not in idx.sources:
            print(f"\n  ❌ Source '{name}' non trouvée.\n")
            sys.exit(1)

        del idx.sources[name]
        # Supprimer le cache
        cache_dir = project_root / DOCS_CACHE_DIR / name
        if cache_dir.exists():
            shutil.rmtree(cache_dir)
        save_doc_index(project_root, idx)
        print(f"\n  ✅ Source '{name}' supprimée.\n")

    elif args.command == "refresh":
        idx = load_doc_index(project_root)
        if not idx.sources:
            print("\n  📚 Aucune source à rafraîchir.\n")
            return

        for name, source in idx.sources.items():
            print(f"  🔄 Rafraîchissement {name}...")
            new_source, report = fetch_source(
                project_root, name, source.base_url, source.paths)
            idx.sources[name] = new_source
            print(f"     ✅ {report.pages_fetched} pages, {report.chunks_created} chunks")

        save_doc_index(project_root, idx)
        print()


if __name__ == "__main__":
    main()
