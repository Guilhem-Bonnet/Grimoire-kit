"""Garde air-gap : la vitrine et le cockpit ne chargent rien hors du site.

Les polices sont vendorisées dans ``web/fonts/`` (Geist et Geist Mono, sous-
ensemble latin). Ces tests empêchent qu'une ressource externe revienne : le
site est servi comme le fait le cockpit (``_sync_site``, puis son handler), et
chaque page servie est examinée telle qu'elle arrive au navigateur.

Ce qui compte comme requête : un élément qui charge une ressource (feuille de
style, script, image, cadre, média, précharge), un ``url()`` ou un ``@import``,
et un appel réseau littéral dans le JavaScript. Un lien de navigation
(``<a href>``) ou une URL canonique n'est pas chargé par la page : il n'est pas
visé.
"""

from __future__ import annotations

import re
import threading
import urllib.parse
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from grimoire.cli import cmd_cockpit
from grimoire.data import web_path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
FONTS = WEB / "fonts"
FONT_BUDGET_BYTES = 90 * 1024
COCKPIT_FONTS = {"Geist-Variable-latin.woff2", "GeistMono-Variable-latin.woff2"}
GOOGLE_FONT_HOSTS = (b"fonts.googleapis.com", b"fonts.gstatic.com")
SERVED_SUFFIXES = {".html", ".css", ".js"}

_LOADING_TAG = re.compile(
    r"<(?P<tag>link|script|img|iframe|frame|source|video|audio|embed|object)\b[^>]*>",
    re.IGNORECASE,
)
_LOADING_REL = {"stylesheet", "preload", "modulepreload", "prefetch", "preconnect", "dns-prefetch", "icon", "import"}
_REL_ATTR = re.compile(r"""\brel\s*=\s*["']([^"']*)""", re.IGNORECASE)
_URL_ATTR = re.compile(r"""\b(?:href|src|data|poster)\s*=\s*["']?\s*((?:https?:)?//[^\s"'>]+)""", re.IGNORECASE)
_CSS_URL = re.compile(r"""(?:@import\s+|url\(\s*)["']?\s*((?:https?:)?//[^\s"')]+)""", re.IGNORECASE)
_JS_CALL = re.compile(r"""(?:\bfetch|\bimport|importScripts|sendBeacon)\s*\(\s*['"`]((?:https?:)?//[^'"`]+)""")
_JS_XHR = re.compile(r"""\.open\(\s*['"]\w+['"]\s*,\s*['"`]((?:https?:)?//[^'"`]+)""")
_JS_ASSIGN = re.compile(r"""\.(?:src|href)\s*=\s*['"`]((?:https?:)?//[^'"`]+)""")
_JS_CTOR = re.compile(
    r"""new\s+(?:Image|Audio|EventSource|WebSocket|Worker)\s*\(\s*['"`]((?:https?:)?//[^'"`]+)""",
)


def remote_loads(suffix: str, text: str) -> list[str]:
    """Les URL externes qu'un fichier de suffixe donné fait charger au navigateur."""
    found: list[str] = []
    if suffix == ".html":
        for match in _LOADING_TAG.finditer(text):
            if match["tag"].lower() == "link":
                rel = _REL_ATTR.search(match.group(0))
                if not rel or not set(rel.group(1).lower().split()) & _LOADING_REL:
                    continue
            found += _URL_ATTR.findall(match.group(0))
    found += _CSS_URL.findall(text)
    if suffix in {".html", ".js"}:
        found += _JS_CALL.findall(text) + _JS_XHR.findall(text)
        found += _JS_ASSIGN.findall(text) + _JS_CTOR.findall(text)
    return found


@pytest.fixture
def cockpit_site(tmp_path: Path):
    """Sert ``web/`` comme le cockpit : copie de site puis handler statique."""
    serve = tmp_path / "serve"
    cmd_cockpit._sync_site(serve)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(cmd_cockpit._CockpitHandler, directory=str(serve)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield serve, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _served_pages(serve: Path) -> list[str]:
    return sorted(
        p.relative_to(serve).as_posix() for p in serve.rglob("*") if p.is_file() and p.suffix in SERVED_SUFFIXES
    )


def _fetch(base: str, rel: str) -> bytes:
    # ?legacy=1 : une page héritée n'est pas redirigée vers son remplaçant.
    url = f"{base}/{urllib.parse.quote(rel)}?legacy=1"
    with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310 - serveur de test local
        return resp.read()


def test_no_google_font_host_left_in_web() -> None:
    offenders = [
        p.relative_to(ROOT).as_posix()
        for p in sorted(WEB.rglob("*"))
        if p.is_file() and any(host in p.read_bytes() for host in GOOGLE_FONT_HOSTS)
    ]
    assert offenders == []


def test_vendored_fonts_are_latin_woff2_within_budget() -> None:
    """Tout woff2 de web/fonts est un vrai woff2 ; le budget de 90 Ko porte sur la
    paire Geist du cockpit (le teaser vendorise ses propres fontes à côté)."""
    fonts = sorted(FONTS.glob("*.woff2"))
    names = {f.name for f in fonts}
    assert names >= COCKPIT_FONTS
    for font in fonts:
        assert font.read_bytes()[:4] == b"wOF2", f"{font.name} n'est pas un woff2"
    total = sum(f.stat().st_size for f in fonts if f.name in COCKPIT_FONTS)
    assert total <= FONT_BUDGET_BYTES, f"{total} octets pour les polices du cockpit, budget {FONT_BUDGET_BYTES}"


def test_fonts_stylesheet_points_to_files_that_exist() -> None:
    css = (FONTS / "fonts.css").read_text(encoding="utf-8")
    sources = re.findall(r"url\('([^']+)'\)", css)
    assert sources, "fonts.css ne déclare aucune source"
    for source in sources:
        assert (FONTS / source).is_file(), f"fonts.css référence {source}, absent de web/fonts/"


def test_cockpit_serves_web_without_remote_loads(cockpit_site) -> None:
    serve, base = cockpit_site
    assert web_path() == WEB, "le cockpit doit servir le web/ de ce dépôt"
    pages = _served_pages(serve)
    assert {"index.html", "kanban.html", "fonts/fonts.css"} <= set(pages)

    offenders: dict[str, list[str]] = {}
    for rel in pages:
        body = _fetch(base, rel).decode("utf-8", errors="replace")
        found = remote_loads(Path(rel).suffix, body)
        if found:
            offenders[rel] = found
    assert offenders == {}


def test_served_font_files_are_the_vendored_ones(cockpit_site) -> None:
    _, base = cockpit_site
    for name in ("Geist-Variable-latin.woff2", "GeistMono-Variable-latin.woff2"):
        assert _fetch(base, f"fonts/{name}")[:4] == b"wOF2"


def test_guard_flags_each_kind_of_remote_load() -> None:
    """La garde ne doit pas être vide : chaque forme de chargement est vue."""
    google = "https://fonts.googleapis.com/css2?family=Geist"
    assert remote_loads(".html", f'<link rel="stylesheet" href="{google}">') == [google]
    assert remote_loads(".html", '<script src="https://cdn.example/x.js"></script>') == ["https://cdn.example/x.js"]
    assert remote_loads(".html", '<img src="//cdn.example/a.png">') == ["//cdn.example/a.png"]
    assert remote_loads(".css", f"@import url('{google}');") == [google]
    assert remote_loads(".css", "body{background:url(https://cdn.example/a.png)}") == ["https://cdn.example/a.png"]
    assert remote_loads(".js", "fetch('https://api.example/x')") == ["https://api.example/x"]
    assert remote_loads(".js", "img.src = 'https://cdn.example/a.png'") == ["https://cdn.example/a.png"]


def test_guard_ignores_navigation_and_canonical_links() -> None:
    assert remote_loads(".html", '<a href="https://github.com/x" target="_blank">GitHub</a>') == []
    assert remote_loads(".html", '<link rel="canonical" href="https://example.org/">') == []
    assert remote_loads(".html", '<link rel="stylesheet" href="fonts/fonts.css">') == []
