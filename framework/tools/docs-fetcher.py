#!/usr/bin/env python3
"""
docs-fetcher.py — Indexation de documentation externe Grimoire.
=============================================================

Télécharge, parse et stocke de la documentation externe pour
enrichir le RAG local. Équivalent du ``@docs`` de Cursor, mais
avec chunking adaptatif et intégration native rag-indexer.

Fonctionnement :
  1. Télécharge une page ou un site de docs (HTML → Markdown)
  2. Chunk le contenu (respecte la structure heading)
  3. Stocke dans ``_grimoire-output/.docs-cache/`` avec metadata
  4. Optionnel : pousse dans l'index RAG Qdrant via rag-indexer

Sources supportées :
  - URL unique (fetch + parse)
  - Sitemap XML (crawl borné)
  - Fichier local (.md, .html, .txt, .rst)
  - Manifest YAML (liste d'URLs nommées)

Sécurité :
  - Validation SSRF sur toutes les URLs
  - Limites de taille (max 5 Mo par page)
  - Rate limiting intégré
  - Blocage des IPs privées et metadata cloud

Usage :
  python3 docs-fetcher.py --project-root . fetch https://docs.python.org/3/library/ast.html
  python3 docs-fetcher.py --project-root . fetch https://docs.python.org/3/library/ast.html --name "Python AST"
  python3 docs-fetcher.py --project-root . manifest docs-sources.yaml
  python3 docs-fetcher.py --project-root . list
  python3 docs-fetcher.py --project-root . search "ast.parse"
  python3 docs-fetcher.py --project-root . remove python-ast
  python3 docs-fetcher.py --project-root . status
  python3 docs-fetcher.py --project-root . --json

Stdlib only — aucune dépendance externe (urllib uniquement).
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlparse

_log = logging.getLogger("grimoire.docs_fetcher")

# ── Constantes ────────────────────────────────────────────────────────────────

DOCS_FETCHER_VERSION = "1.0.0"

CACHE_DIR = "_grimoire-output/.docs-cache"
INDEX_FILE = "_grimoire-output/.docs-index.json"
MAX_PAGE_SIZE = 5 * 1024 * 1024  # 5 Mo
MAX_PAGES_PER_SITEMAP = 50
RATE_LIMIT_DELAY = 1.0  # secondes entre requêtes
REQUEST_TIMEOUT = 30  # secondes
CHARS_PER_TOKEN = 4
DEFAULT_MAX_CHUNK_TOKENS = 512

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


# ── URL Validation ───────────────────────────────────────────────────────────

def validate_url(url: str) -> str:
    """Valide une URL contre les attaques SSRF.

    Bloque les endpoints metadata cloud et les IPs privées.
    Raises ValueError si l'URL est dangereuse.
    """
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"Scheme '{parsed.scheme}' non autorisé (http/https uniquement)")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("URL sans hostname")
    if host in _BLOCKED_HOSTS:
        raise ValueError(f"URL bloquée (cloud metadata): {host}")
    if any(host.startswith(p) for p in _BLOCKED_IP_PREFIXES):
        raise ValueError(f"URL vers IP privée bloquée: {host}")
    return url


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class DocChunk:
    """Un chunk de documentation externe."""
    text: str
    source_url: str
    source_name: str
    heading: str = ""
    chunk_index: int = 0
    estimated_tokens: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DocSource:
    """Source de documentation indexée."""
    name: str
    url: str
    slug: str
    chunks_count: int = 0
    fetched_at: str = ""
    content_hash: str = ""
    size_bytes: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FetchResult:
    """Résultat d'un fetch."""
    source: DocSource
    chunks: list[DocChunk] = field(default_factory=list)
    error: str = ""
    cached: bool = False

    def to_dict(self) -> dict:
        return {
            "source": self.source.to_dict(),
            "chunks_count": len(self.chunks),
            "error": self.error,
            "cached": self.cached,
        }


@dataclass
class DocsReport:
    """Rapport global."""
    sources: list[DocSource] = field(default_factory=list)
    total_chunks: int = 0
    total_size_bytes: int = 0

    def to_dict(self) -> dict:
        return {
            "sources_count": len(self.sources),
            "sources": [s.to_dict() for s in self.sources],
            "total_chunks": self.total_chunks,
            "total_size_bytes": self.total_size_bytes,
        }


# ── Slug Generation ─────────────────────────────────────────────────────────

def make_slug(name: str) -> str:
    """Génère un slug filesystem-safe depuis un nom."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug[:80] or "unnamed"


# ── HTML to Markdown ─────────────────────────────────────────────────────────

def html_to_markdown(html_content: str) -> str:
    """Conversion HTML → Markdown simplifiée (stdlib only).

    Gère les cas courants : headings, paragraphes, liens, code blocks,
    listes, emphase. Pas besoin de BeautifulSoup.
    """
    text = html_content

    # Supprimer <script>, <style>, <nav>, <footer>, <header>, <head>, <aside> et leur contenu
    for tag in ("script", "style", "nav", "footer", "header", "head", "aside"):
        text = re.sub(rf"<{tag}[^>]*>.*?</{tag}>", "", text, flags=re.DOTALL | re.IGNORECASE)

    # Headings
    for level in range(6, 0, -1):
        prefix = "#" * level
        text = re.sub(
            rf"<h{level}[^>]*>(.*?)</h{level}>",
            rf"\n{prefix} \1\n",
            text, flags=re.DOTALL | re.IGNORECASE,
        )

    # Code blocks
    text = re.sub(
        r"<pre[^>]*><code[^>]*>(.*?)</code></pre>",
        r"\n```\n\1\n```\n",
        text, flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(r"<pre[^>]*>(.*?)</pre>", r"\n```\n\1\n```\n", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<code[^>]*>(.*?)</code>", r"`\1`", text, flags=re.DOTALL | re.IGNORECASE)

    # Links
    text = re.sub(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', r"[\2](\1)", text, flags=re.DOTALL | re.IGNORECASE)

    # Lists
    text = re.sub(r"<li[^>]*>(.*?)</li>", r"\n- \1", text, flags=re.DOTALL | re.IGNORECASE)

    # Emphasis (\b prevents <body> matching <b>, <iframe> matching <i>, etc.)
    text = re.sub(r"<(?:strong|b)\b[^>]*>(.*?)</(?:strong|b)>", r"**\1**", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<(?:em|i)\b[^>]*>(.*?)</(?:em|i)>", r"*\1*", text, flags=re.DOTALL | re.IGNORECASE)

    # Paragraphes
    text = re.sub(r"<p[^>]*>(.*?)</p>", r"\n\1\n", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)

    # Supprimer toutes les balises restantes
    text = re.sub(r"<[^>]+>", "", text)

    # Décoder les entités HTML
    text = html.unescape(text)

    # Nettoyer les lignes vides multiples
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ── Chunking ─────────────────────────────────────────────────────────────────

def chunk_markdown(content: str, source_url: str, source_name: str,
                   max_tokens: int = DEFAULT_MAX_CHUNK_TOKENS) -> list[DocChunk]:
    """Découpe du Markdown par heading."""
    max_chars = max_tokens * CHARS_PER_TOKEN
    chunks: list[DocChunk] = []
    current_heading = source_name
    current_lines: list[str] = []
    chunk_idx = 0

    for line in content.split("\n"):
        header_match = re.match(r"^(#{1,3})\s+(.+)", line)
        if header_match:
            # Flush
            if current_lines:
                text = "\n".join(current_lines).strip()
                if text and len(text) > 30:
                    chunks.append(DocChunk(
                        text=text[:max_chars],
                        source_url=source_url,
                        source_name=source_name,
                        heading=current_heading,
                        chunk_index=chunk_idx,
                        estimated_tokens=min(len(text) // CHARS_PER_TOKEN, max_tokens),
                    ))
                    chunk_idx += 1
                current_lines = []
            current_heading = header_match.group(2).strip()
            current_lines.append(line)
        else:
            current_lines.append(line)
            joined = "\n".join(current_lines)
            if len(joined) > max_chars:
                text = joined.strip()
                if text:
                    chunks.append(DocChunk(
                        text=text[:max_chars],
                        source_url=source_url,
                        source_name=source_name,
                        heading=current_heading,
                        chunk_index=chunk_idx,
                        estimated_tokens=min(len(text) // CHARS_PER_TOKEN, max_tokens),
                    ))
                    chunk_idx += 1
                current_lines = []

    # Flush final
    if current_lines:
        text = "\n".join(current_lines).strip()
        if text and len(text) > 30:
            chunks.append(DocChunk(
                text=text[:max_chars],
                source_url=source_url,
                source_name=source_name,
                heading=current_heading,
                chunk_index=chunk_idx,
                estimated_tokens=min(len(text) // CHARS_PER_TOKEN, max_tokens),
            ))

    # Fallback : si rien, tout en un chunk
    if not chunks and content.strip():
        chunks.append(DocChunk(
            text=content.strip()[:max_chars],
            source_url=source_url,
            source_name=source_name,
            heading=source_name,
            chunk_index=0,
            estimated_tokens=min(len(content) // CHARS_PER_TOKEN, max_tokens),
        ))

    return chunks


# ── Fetcher ──────────────────────────────────────────────────────────────────

def fetch_url(url: str, timeout: int = REQUEST_TIMEOUT) -> tuple[str, str]:
    """Fetch le contenu d'une URL validée.

    Returns (content, error). En cas d'erreur, content est vide.
    Ne fetch jamais sans validation SSRF préalable.
    """
    validate_url(url)  # Raises ValueError si dangereux

    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Grimoire-DocsFetcher/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.length and resp.length > MAX_PAGE_SIZE:
                return "", f"Page trop volumineuse ({resp.length} bytes > {MAX_PAGE_SIZE})"
            content = resp.read(MAX_PAGE_SIZE + 1)
            if len(content) > MAX_PAGE_SIZE:
                return "", f"Page trop volumineuse (>{MAX_PAGE_SIZE} bytes)"
            encoding = resp.headers.get_content_charset() or "utf-8"
            return content.decode(encoding, errors="replace"), ""
    except Exception as e:
        return "", str(e)


def fetch_and_parse(url: str, name: str | None = None,
                    timeout: int = REQUEST_TIMEOUT) -> FetchResult:
    """Fetch une URL, parse en Markdown, chunk.

    Le ``name`` est utilisé pour le slug et le display. Si absent,
    déduit de l'URL.
    """
    if not name:
        parsed = urlparse(url)
        name = parsed.path.rstrip("/").rsplit("/", 1)[-1] or parsed.hostname or "docs"
        name = re.sub(r"\.(html?|htm|md|txt)$", "", name, flags=re.IGNORECASE)

    slug = make_slug(name)

    raw_html, error = fetch_url(url, timeout=timeout)
    if error:
        return FetchResult(
            source=DocSource(name=name, url=url, slug=slug),
            error=error,
        )

    markdown = html_to_markdown(raw_html) if "<html" in raw_html.lower()[:500] else raw_html
    content_hash = hashlib.sha256(markdown.encode()).hexdigest()
    chunks = chunk_markdown(markdown, url, name)

    source = DocSource(
        name=name,
        url=url,
        slug=slug,
        chunks_count=len(chunks),
        fetched_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        content_hash=content_hash,
        size_bytes=len(markdown.encode()),
    )

    return FetchResult(source=source, chunks=chunks)


# ── Local File Support ───────────────────────────────────────────────────────

def fetch_local_file(filepath: Path, name: str | None = None) -> FetchResult:
    """Indexe un fichier local comme source de docs."""
    if not filepath.exists():
        slug = make_slug(name or filepath.stem)
        return FetchResult(
            source=DocSource(name=name or filepath.stem, url=str(filepath), slug=slug),
            error=f"File not found: {filepath}",
        )

    if not name:
        name = filepath.stem
    slug = make_slug(name)

    content = filepath.read_text(encoding="utf-8", errors="replace")

    if filepath.suffix.lower() in (".html", ".htm"):
        content = html_to_markdown(content)

    content_hash = hashlib.sha256(content.encode()).hexdigest()
    chunks = chunk_markdown(content, str(filepath), name)

    source = DocSource(
        name=name,
        url=str(filepath),
        slug=slug,
        chunks_count=len(chunks),
        fetched_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        content_hash=content_hash,
        size_bytes=len(content.encode()),
    )

    return FetchResult(source=source, chunks=chunks)


# ── Cache / Index ────────────────────────────────────────────────────────────

class DocsIndex:
    """Gère l'index des docs fetchées et le cache local."""

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.cache_dir = project_root / CACHE_DIR
        self.index_path = project_root / INDEX_FILE
        self._sources: dict[str, DocSource] = {}
        self._load()

    def _load(self) -> None:
        if self.index_path.exists():
            try:
                with open(self.index_path, encoding="utf-8") as f:
                    data = json.load(f)
                for slug, entry in data.items():
                    self._sources[slug] = DocSource(**entry)
            except (json.JSONDecodeError, OSError, TypeError):
                self._sources = {}

    def save(self) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        data = {slug: asdict(src) for slug, src in self._sources.items()}
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def store(self, result: FetchResult) -> None:
        """Stocke un résultat de fetch dans le cache."""
        if result.error:
            return
        slug = result.source.slug
        self._sources[slug] = result.source

        # Sauvegarder les chunks en Markdown
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        md_path = self.cache_dir / f"{slug}.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(f"# {result.source.name}\n\n")
            f.write(f"> Source: {result.source.url}\n")
            f.write(f"> Fetched: {result.source.fetched_at}\n\n")
            f.writelines(f"{chunk.text}\n\n---\n\n" for chunk in result.chunks)

        # Sauvegarder les chunks en JSON pour le RAG
        json_path = self.cache_dir / f"{slug}.chunks.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump([c.to_dict() for c in result.chunks], f, indent=2, ensure_ascii=False)

        self.save()

    def remove(self, slug: str) -> bool:
        """Supprime une source de l'index et du cache."""
        if slug not in self._sources:
            return False
        del self._sources[slug]
        md_path = self.cache_dir / f"{slug}.md"
        json_path = self.cache_dir / f"{slug}.chunks.json"
        for p in (md_path, json_path):
            if p.exists():
                p.unlink()
        self.save()
        return True

    def get(self, slug: str) -> DocSource | None:
        return self._sources.get(slug)

    def list_sources(self) -> list[DocSource]:
        return list(self._sources.values())

    def search(self, query: str) -> list[DocChunk]:
        """Recherche texte simple dans les chunks cachés."""
        results: list[DocChunk] = []
        query_lower = query.lower()
        for slug in self._sources:
            json_path = self.cache_dir / f"{slug}.chunks.json"
            if not json_path.exists():
                continue
            try:
                with open(json_path, encoding="utf-8") as f:
                    chunks_data = json.load(f)
                for cd in chunks_data:
                    if query_lower in cd.get("text", "").lower():
                        results.append(DocChunk(**cd))
            except (json.JSONDecodeError, OSError, TypeError):
                continue
        return results

    def status(self) -> DocsReport:
        """Rapport de l'état du cache."""
        total_chunks = 0
        total_size = 0
        for src in self._sources.values():
            total_chunks += src.chunks_count
            total_size += src.size_bytes
        return DocsReport(
            sources=list(self._sources.values()),
            total_chunks=total_chunks,
            total_size_bytes=total_size,
        )

    def needs_refresh(self, slug: str, new_hash: str) -> bool:
        """Vérifie si une source a changé depuis le dernier fetch."""
        src = self._sources.get(slug)
        if not src:
            return True
        return src.content_hash != new_hash


# ── Manifest Support ─────────────────────────────────────────────────────────

def load_manifest(manifest_path: Path) -> list[dict[str, str]]:
    """Charge un manifest YAML de sources de docs.

    Format attendu :
      sources:
        - name: "Python AST"
          url: "https://docs.python.org/3/library/ast.html"
        - name: "FastAPI"
          url: "https://fastapi.tiangolo.com/"
    """
    if not manifest_path.exists():
        return []

    content = manifest_path.read_text(encoding="utf-8")
    sources: list[dict[str, str]] = []

    # Parse YAML simplifié (stdlib only)
    in_sources = False
    current: dict[str, str] = {}
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped == "sources:":
            in_sources = True
            continue
        if not in_sources:
            continue
        if stripped.startswith("- name:"):
            if current:
                sources.append(current)
            current = {"name": stripped.split(":", 1)[1].strip().strip('"').strip("'")}
        elif stripped.startswith("url:"):
            current["url"] = stripped.split(":", 1)[1].strip().strip('"').strip("'")
            # Re-add the scheme that got split off
            if current["url"].startswith("//"):
                # url: https://... gets split into url: https and //...
                pass  # already complete
            elif "url" in current and not current["url"].startswith("http"):
                # url field was 'https://...' but yaml split on first ':'
                # Re-read from raw line
                m = re.search(r'url:\s*["\']?(https?://[^\s"\']+)', line)
                if m:
                    current["url"] = m.group(1)

    if current:
        sources.append(current)

    return sources


# ── Renderers ────────────────────────────────────────────────────────────────

def render_text_status(report: DocsReport) -> str:
    lines = [
        "╔══════════════════════════════════════════════╗",
        "║     📚 DOCS FETCHER — Status                 ║",
        "╚══════════════════════════════════════════════╝",
        "",
        f"Sources indexées : {len(report.sources)}",
        f"Chunks total     : {report.total_chunks}",
        f"Taille totale    : {report.total_size_bytes / 1024:.1f} Ko",
        "",
    ]
    if not report.sources:
        lines.append("  (aucune source indexée)")
    else:
        for src in report.sources:
            lines.append(f"  📖 {src.name} [{src.slug}]")
            lines.append(f"     URL     : {src.url}")
            lines.append(f"     Chunks  : {src.chunks_count}")
            lines.append(f"     Fetched : {src.fetched_at}")
            lines.append("")
    return "\n".join(lines)


def render_text_fetch(result: FetchResult) -> str:
    if result.error:
        return f"❌ Erreur pour '{result.source.name}': {result.error}"
    status = "📦 (cache)" if result.cached else "✅ (frais)"
    return (
        f"{status} {result.source.name} — "
        f"{result.source.chunks_count} chunks, "
        f"{result.source.size_bytes / 1024:.1f} Ko"
    )


def render_text_search(chunks: list[DocChunk], query: str) -> str:
    lines = [f"🔍 Résultats pour '{query}' : {len(chunks)} chunk(s)", ""]
    for i, chunk in enumerate(chunks[:20]):  # Max 20 résultats
        preview = chunk.text[:150].replace("\n", " ")
        lines.append(f"  [{i + 1}] {chunk.source_name} > {chunk.heading}")
        lines.append(f"      {preview}...")
        lines.append("")
    if len(chunks) > 20:
        lines.append(f"  ... et {len(chunks) - 20} résultats supplémentaires")
    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Docs Fetcher — indexation de documentation externe")
    p.add_argument("--project-root", default=".", help="Racine du projet")
    p.add_argument("--json", action="store_true", dest="as_json", help="Sortie JSON")
    sub = p.add_subparsers(dest="command")

    fetch = sub.add_parser("fetch", help="Fetch une URL de docs")
    fetch.add_argument("url", help="URL à fetcher")
    fetch.add_argument("--name", default=None, help="Nom de la source")

    manifest = sub.add_parser("manifest", help="Fetch depuis un manifest YAML")
    manifest.add_argument("path", help="Chemin vers le manifest")

    sub.add_parser("list", help="Lister les sources indexées")

    search = sub.add_parser("search", help="Rechercher dans les docs")
    search.add_argument("query", help="Texte à rechercher")

    remove = sub.add_parser("remove", help="Supprimer une source")
    remove.add_argument("slug", help="Slug de la source")

    sub.add_parser("status", help="État de l'index")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = Path(args.project_root).resolve()
    index = DocsIndex(root)

    if args.command == "fetch":
        result = fetch_and_parse(args.url, name=args.name)
        if not result.error:
            index.store(result)
        if args.as_json:
            print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        else:
            print(render_text_fetch(result))
        return 1 if result.error else 0

    elif args.command == "manifest":
        manifest_path = Path(args.path)
        if not manifest_path.is_absolute():
            manifest_path = root / manifest_path
        sources = load_manifest(manifest_path)
        if not sources:
            print("⚠️  Manifest vide ou introuvable")
            return 1
        results = []
        for src in sources:
            url = src.get("url", "")
            name = src.get("name")
            if not url:
                continue
            r = fetch_and_parse(url, name=name)
            if not r.error:
                index.store(r)
            results.append(r)
            time.sleep(RATE_LIMIT_DELAY)
        if args.as_json:
            print(json.dumps([r.to_dict() for r in results], indent=2, ensure_ascii=False))
        else:
            for r in results:
                print(render_text_fetch(r))
        return 0

    elif args.command == "list":
        report = index.status()
        if args.as_json:
            print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        else:
            print(render_text_status(report))
        return 0

    elif args.command == "search":
        results = index.search(args.query)
        if args.as_json:
            print(json.dumps([c.to_dict() for c in results], indent=2, ensure_ascii=False))
        else:
            print(render_text_search(results, args.query))
        return 0

    elif args.command == "remove":
        ok = index.remove(args.slug)
        if args.as_json:
            print(json.dumps({"removed": ok, "slug": args.slug}))
        else:
            print(f"✅ Supprimé: {args.slug}" if ok else f"❌ Non trouvé: {args.slug}")
        return 0 if ok else 1

    elif args.command == "status":
        report = index.status()
        if args.as_json:
            print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        else:
            print(render_text_status(report))
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
