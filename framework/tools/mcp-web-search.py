#!/usr/bin/env python3
"""
mcp-web-search.py — MCP-compatible web search tool for BMAD agents.
═══════════════════════════════════════════════════════════════════

Recherche web via des API publiques (DuckDuckGo HTML, ou URL API
configurable). Expose une interface MCP pour que les agents puissent
enrichir leurs réponses avec du contenu web récent.

Modes :
  search  — Recherche et retourne les résultats
  test    — Vérifie la connectivité

MCP interface :
  mcp_web_search(query, max_results=5) → list[{title, url, snippet}]

Usage :
  python3 mcp-web-search.py --project-root . search "BMAD methodology"
  python3 mcp-web-search.py --project-root . search "Python dataclass" --max-results 3

Stdlib only — aucune dépendance externe.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from html import unescape
from typing import Any

MCP_WEB_SEARCH_VERSION = "1.0.0"

# DuckDuckGo HTML search (no API key needed)
_DDG_URL = "https://html.duckduckgo.com/html/"
_USER_AGENT = "BMAD-WebSearch/1.0 (stdlib; no external deps)"
_TIMEOUT = 10  # seconds


# ── Search Engine ────────────────────────────────────────────────

def _extract_results(html: str, max_results: int = 5) -> list[dict[str, str]]:
    """Parse DuckDuckGo HTML results page."""
    results: list[dict[str, str]] = []

    # Each result is in a div with class="result"
    # Title in <a class="result__a">, snippet in <a class="result__snippet">
    result_blocks = re.findall(
        r'<div[^>]*class="[^"]*result[^"]*"[^>]*>(.*?)</div>\s*</div>',
        html, re.DOTALL,
    )

    if not result_blocks:
        # Fallback: try simpler pattern
        result_blocks = re.findall(
            r'class="result__body">(.*?)</div>', html, re.DOTALL,
        )

    for block in result_blocks[:max_results * 2]:
        # Extract URL
        url_match = re.search(
            r'href="([^"]*uddg=([^&"]+))"', block,
        )
        url = ""
        if url_match:
            url = urllib.parse.unquote(url_match.group(2))
        else:
            url_match = re.search(r'href="(https?://[^"]+)"', block)
            if url_match:
                url = url_match.group(1)

        if not url or not url.startswith("http"):
            continue

        # Extract title
        title_match = re.search(
            r'class="result__a"[^>]*>(.*?)</a>', block, re.DOTALL,
        )
        title = ""
        if title_match:
            title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip()
            title = unescape(title)

        # Extract snippet
        snippet_match = re.search(
            r'class="result__snippet"[^>]*>(.*?)</[at]>', block, re.DOTALL,
        )
        snippet = ""
        if snippet_match:
            snippet = re.sub(r'<[^>]+>', '', snippet_match.group(1)).strip()
            snippet = unescape(snippet)

        if title or snippet:
            results.append({
                "title": title or "(untitled)",
                "url": url,
                "snippet": snippet,
            })

        if len(results) >= max_results:
            break

    return results


def web_search(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """Perform a web search and return structured results."""
    data = urllib.parse.urlencode({"q": query}).encode("utf-8")
    req = urllib.request.Request(
        _DDG_URL,
        data=data,
        headers={"User-Agent": _USER_AGENT},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as exc:
        return [{"error": str(exc), "title": "", "url": "", "snippet": ""}]

    return _extract_results(html, max_results)


# ── MCP Interface ────────────────────────────────────────────────

def mcp_web_search(query: str, max_results: int = 5) -> dict[str, Any]:
    """MCP tool: recherche web.

    Args:
        query: Termes de recherche.
        max_results: Nombre max de résultats (1-10).

    Returns:
        {results: [{title, url, snippet}], count: N, query: str}
    """
    max_results = max(1, min(10, max_results))
    results = web_search(query, max_results)
    return {
        "results": results,
        "count": len(results),
        "query": query,
    }


# ── Commands ─────────────────────────────────────────────────────

def cmd_search(args: argparse.Namespace) -> int:
    query = args.query
    results = web_search(query, args.max_results)

    if args.json:
        print(json.dumps({
            "query": query,
            "results": results,
            "count": len(results),
        }, indent=2, ensure_ascii=False))
    else:
        print(f"\n  🔍 Résultats pour: {query}\n")
        if not results:
            print("  Aucun résultat.")
        for i, r in enumerate(results, 1):
            if r.get("error"):
                print(f"  ❌ Erreur: {r['error']}")
                continue
            print(f"  {i}. {r['title']}")
            print(f"     {r['url']}")
            if r["snippet"]:
                print(f"     {r['snippet'][:120]}")
            print()
    return 0


def cmd_test(args: argparse.Namespace) -> int:
    results = web_search("test connectivity", max_results=1)

    if args.json:
        ok = bool(results) and not results[0].get("error")
        print(json.dumps({"ok": ok, "results": results},
                          indent=2, ensure_ascii=False))
    else:
        if results and not results[0].get("error"):
            print("  ✅ Web search operational")
        else:
            error = results[0].get("error", "unknown") if results else "no response"
            print(f"  ❌ Web search failed: {error}")
    return 0


# ── Main ─────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="BMAD Web Search Tool")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--json", action="store_true")

    subs = parser.add_subparsers(dest="command")

    p_search = subs.add_parser("search", help="Web search")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--max-results", type=int, default=5,
                          help="Max results (default: 5)")
    p_search.set_defaults(func=cmd_search)

    p_test = subs.add_parser("test", help="Test connectivity")
    p_test.set_defaults(func=cmd_test)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
