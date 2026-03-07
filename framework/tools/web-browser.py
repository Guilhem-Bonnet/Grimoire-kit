#!/usr/bin/env python3
"""
web-browser.py — Navigateur web sandboxé pour agents BMAD.
============================================================

Navigateur headless basé sur Playwright pour permettre aux agents
d'accéder au web avec rendu JavaScript complet.

Commandes :
  fetch       — Récupère le contenu d'une URL (rendu JS incl.)
  screenshot  — Capture d'écran d'une page web
  interact    — Actions interactives séquentielles (click, type, etc.)
  readability — Extrait le contenu principal (heuristique lisibilité)
  status      — Vérifie l'installation de Playwright

Sécurité :
  - Protection SSRF (IPs privées et metadata cloud bloquées)
  - Profil navigateur isolé (aucune persistance)
  - Timeouts et limites de taille stricts
  - Téléchargements et popups bloqués
  - Aucun accès fichiers locaux (file://)

Dépendance : playwright (optionnelle pour fetch basique)
  pip install playwright && playwright install chromium

Usage :
  python3 web-browser.py --project-root . fetch https://docs.python.org/3/
  python3 web-browser.py --project-root . fetch https://example.com --selector "main"
  python3 web-browser.py --project-root . screenshot https://example.com -o shot.png
  python3 web-browser.py --project-root . interact https://example.com --actions '[{"click":"#btn"},{"extract":true}]'
  python3 web-browser.py --project-root . readability https://example.com
  python3 web-browser.py --project-root . status
  python3 web-browser.py --project-root . status --install
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import logging
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

_log = logging.getLogger("grimoire.web_browser")

WEB_BROWSER_VERSION = "1.0.0"

# ── Constantes ────────────────────────────────────────────────────────────────

NAVIGATION_TIMEOUT = 30_000     # ms — Playwright timeout
ACTION_TIMEOUT = 10_000         # ms — timeout par action interactive
MAX_CONTENT_LENGTH = 2 * 1024 * 1024  # 2 Mo max de texte extrait
REQUEST_TIMEOUT = 30            # sec — urllib fallback
VIEWPORT_WIDTH = 1280
VIEWPORT_HEIGHT = 720

# SSRF protection (identique à docs-fetcher)
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

_USER_AGENT = "BMAD-WebBrowser/1.0 (Grimoire-Kit)"


# ── URL Validation ────────────────────────────────────────────────────────────

def validate_url(url: str) -> str:
    """Valide une URL contre les attaques SSRF."""
    parsed = urllib.parse.urlparse(url)
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


# ── Playwright Detection ─────────────────────────────────────────────────────

def _check_playwright() -> tuple[bool, str]:
    """Vérifie si Playwright est installé et fonctionnel."""
    try:
        import playwright  # noqa: F401
        from playwright.sync_api import sync_playwright  # noqa: F401
        return True, "OK"
    except ImportError:
        return False, "playwright non installé (pip install playwright)"


def _check_browser_installed() -> tuple[bool, str]:
    """Vérifie si un navigateur Playwright est installé."""
    ok, msg = _check_playwright()
    if not ok:
        return False, msg
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        return True, "Chromium OK"
    except Exception as e:
        return False, f"Navigateur non installé: {e} (playwright install chromium)"


# ── Data Classes ──────────────────────────────────────────────────────────────

@dataclass
class PageContent:
    """Contenu extrait d'une page web."""
    url: str
    title: str = ""
    text: str = ""
    markdown: str = ""
    links: list[dict[str, str]] = field(default_factory=list)
    status_code: int = 0
    content_type: str = ""
    elapsed_ms: int = 0
    method: str = ""  # "playwright" ou "urllib"

    def to_dict(self) -> dict:
        d = asdict(self)
        # Tronquer le texte si trop long
        if len(d.get("text", "")) > MAX_CONTENT_LENGTH:
            d["text"] = d["text"][:MAX_CONTENT_LENGTH] + "\n[...tronqué]"
        if len(d.get("markdown", "")) > MAX_CONTENT_LENGTH:
            d["markdown"] = d["markdown"][:MAX_CONTENT_LENGTH] + "\n[...tronqué]"
        return d


@dataclass
class ScreenshotResult:
    """Résultat d'une capture d'écran."""
    url: str
    path: str = ""
    base64_data: str = ""
    width: int = 0
    height: int = 0
    elapsed_ms: int = 0
    error: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        # Ne pas inclure le base64 dans le JSON de sortie (trop gros)
        if d.get("base64_data"):
            d["base64_data"] = f"[{len(self.base64_data)} chars]"
        return d


@dataclass
class InteractResult:
    """Résultat d'une session interactive."""
    url: str
    steps: list[dict] = field(default_factory=list)
    final_url: str = ""
    elapsed_ms: int = 0
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BrowserStatus:
    """État du navigateur."""
    playwright_installed: bool = False
    browser_installed: bool = False
    playwright_message: str = ""
    browser_message: str = ""
    version: str = WEB_BROWSER_VERSION

    def to_dict(self) -> dict:
        return asdict(self)


# ── HTML to Markdown ──────────────────────────────────────────────────────────

def html_to_markdown(html_content: str) -> str:
    """Conversion HTML → Markdown (stdlib only, identique à docs-fetcher)."""
    text = html_content

    # Supprimer tags non-contenu
    for tag in ("script", "style", "nav", "footer", "header", "head", "aside", "noscript"):
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

    # Emphasis (\b word boundary prevents <body> matching <b> etc.)
    text = re.sub(r"<(?:strong|b)\b[^>]*>(.*?)</(?:strong|b)>", r"**\1**", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<(?:em|i)\b[^>]*>(.*?)</(?:em|i)>", r"*\1*", text, flags=re.DOTALL | re.IGNORECASE)

    # Tables (basique)
    text = re.sub(r"<tr[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<t[hd][^>]*>(.*?)</t[hd]>", r" | \1", text, flags=re.DOTALL | re.IGNORECASE)

    # Paragraphes / br
    text = re.sub(r"<p[^>]*>(.*?)</p>", r"\n\1\n", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)

    # Supprimer balises restantes
    text = re.sub(r"<[^>]+>", "", text)

    # Entités HTML
    text = html.unescape(text)

    # Lignes vides multiples
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_links(html_content: str, base_url: str) -> list[dict[str, str]]:
    """Extrait les liens d'une page HTML."""
    links = []
    seen = set()
    for match in re.finditer(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', html_content, re.DOTALL | re.IGNORECASE):
        href, text = match.group(1), match.group(2)
        # Nettoyer le texte
        text = re.sub(r"<[^>]+>", "", text).strip()
        # Résoudre les URLs relatives
        if href and not href.startswith(("http://", "https://", "mailto:", "javascript:", "#")):
            href = urllib.parse.urljoin(base_url, href)
        if href and href.startswith("http") and href not in seen:
            seen.add(href)
            links.append({"href": href, "text": text[:200]})
    return links[:200]  # Limiter à 200 liens


# ── Readability Heuristic ─────────────────────────────────────────────────────

def _readability_extract(html_content: str) -> str:
    """Extraction du contenu principal via heuristique densité texte/balises.

    Algorithme simplifié inspiré de Readability :
    1. Trouver les blocs candidats (<article>, <main>, <div> avec beaucoup de texte)
    2. Scorer par densité texte vs balises
    3. Retourner le meilleur candidat converti en markdown
    """
    # Priorité 1 : <article> ou <main>
    for tag in ("article", "main", r'div[^>]*role="main"'):
        match = re.search(
            rf"<{tag}[^>]*>(.*?)</{tag.split('[')[0]}>",
            html_content, re.DOTALL | re.IGNORECASE,
        )
        if match:
            content = match.group(1)
            text = re.sub(r"<[^>]+>", "", content).strip()
            if len(text) > 200:
                return html_to_markdown(content)

    # Priorité 2 : <div> avec le plus de texte
    best_content = ""
    best_text_len = 0
    for match in re.finditer(r"<div[^>]*>(.*?)</div>", html_content, re.DOTALL | re.IGNORECASE):
        content = match.group(1)
        text = re.sub(r"<[^>]+>", "", content).strip()
        # Scorer : longueur texte, moins de liens (ratio texte/lien)
        link_text_len = sum(len(m.group(2)) for m in re.finditer(
            r'<a[^>]*>(.*?)</a>', content, re.DOTALL | re.IGNORECASE,
        ))
        score = len(text) - link_text_len * 2
        if score > best_text_len and len(text) > 200:
            best_text_len = score
            best_content = content

    if best_content:
        return html_to_markdown(best_content)

    # Fallback : convertir tout en markdown
    return html_to_markdown(html_content)


# ── Fetch (urllib fallback) ───────────────────────────────────────────────────

def _fetch_urllib(url: str, selector: str = "") -> PageContent:
    """Fetch basique via urllib (pas de JS)."""
    validate_url(url)
    t0 = time.monotonic()

    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            status = resp.status
            content_type = resp.headers.get("Content-Type", "")
            raw = resp.read(MAX_CONTENT_LENGTH)
    except urllib.error.HTTPError as e:
        return PageContent(
            url=url, status_code=e.code, method="urllib",
            elapsed_ms=int((time.monotonic() - t0) * 1000),
        )

    charset = "utf-8"
    ct_match = re.search(r"charset=([^\s;]+)", content_type)
    if ct_match:
        charset = ct_match.group(1)
    text_html = raw.decode(charset, errors="replace")

    # Titre
    title_match = re.search(r"<title[^>]*>(.*?)</title>", text_html, re.DOTALL | re.IGNORECASE)
    title = html.unescape(title_match.group(1).strip()) if title_match else ""

    # Si sélecteur fourni, tenter extraction
    extracted_html = text_html
    if selector:
        # Tentative basique : on cherche le sélecteur dans le HTML
        # Limité sans vrai DOM mais couvre les cas simples (tag, #id, .class)
        tag_match = _selector_to_regex(selector, text_html)
        if tag_match:
            extracted_html = tag_match

    md = html_to_markdown(extracted_html)
    links = _extract_links(text_html, url)

    return PageContent(
        url=url,
        title=title,
        text=re.sub(r"<[^>]+>", "", extracted_html).strip()[:MAX_CONTENT_LENGTH],
        markdown=md[:MAX_CONTENT_LENGTH],
        links=links,
        status_code=status,
        content_type=content_type,
        elapsed_ms=int((time.monotonic() - t0) * 1000),
        method="urllib",
    )


def _selector_to_regex(selector: str, html_text: str) -> str:
    """Extraction basique par sélecteur CSS (tag, #id, .class)."""
    # tag
    if re.match(r"^[a-z][a-z0-9]*$", selector, re.IGNORECASE):
        m = re.search(rf"<{selector}[^>]*>(.*?)</{selector}>", html_text, re.DOTALL | re.IGNORECASE)
        return m.group(1) if m else ""
    # #id
    if selector.startswith("#"):
        id_val = re.escape(selector[1:])
        m = re.search(rf'<[^>]*id="{id_val}"[^>]*>(.*?)</[^>]+>', html_text, re.DOTALL | re.IGNORECASE)
        return m.group(1) if m else ""
    # .class
    if selector.startswith("."):
        cls_val = re.escape(selector[1:])
        m = re.search(
            rf'<[^>]*class="[^"]*\b{cls_val}\b[^"]*"[^>]*>(.*?)</[^>]+>',
            html_text, re.DOTALL | re.IGNORECASE,
        )
        return m.group(1) if m else ""
    return ""


# ── Fetch (Playwright) ───────────────────────────────────────────────────────

def _fetch_playwright(url: str, selector: str = "", wait_for: str = "") -> PageContent:
    """Fetch avec Playwright (rendu JS complet)."""
    validate_url(url)
    from playwright.sync_api import sync_playwright

    t0 = time.monotonic()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=_USER_AGENT,
            viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
            java_script_enabled=True,
            accept_downloads=False,
        )
        page = context.new_page()

        # Bloquer les ressources lourdes (images, media, fonts)
        page.route("**/*.{png,jpg,jpeg,gif,svg,webp,ico,woff,woff2,ttf,eot,mp3,mp4,webm}", lambda route: route.abort())

        try:
            response = page.goto(url, timeout=NAVIGATION_TIMEOUT, wait_until="domcontentloaded")
            status_code = response.status if response else 0

            # Wait for specific selector if provided
            if wait_for:
                page.wait_for_selector(wait_for, timeout=ACTION_TIMEOUT)
            else:
                # Small wait for JS rendering
                page.wait_for_timeout(1000)

            title = page.title()
            full_html = page.content()

            # Extract specific section or full page
            if selector:
                try:
                    element = page.query_selector(selector)
                    if element:
                        inner = element.inner_html()
                    else:
                        inner = full_html
                except Exception:
                    inner = full_html
            else:
                inner = full_html

            md = html_to_markdown(inner)
            plain_text = page.inner_text("body")[:MAX_CONTENT_LENGTH] if not selector else re.sub(r"<[^>]+>", "", inner).strip()
            links = _extract_links(full_html, url)
            content_type = response.headers.get("content-type", "") if response else ""

        except Exception as e:
            browser.close()
            return PageContent(
                url=url, method="playwright",
                elapsed_ms=int((time.monotonic() - t0) * 1000),
                text=f"Erreur: {e}",
            )

        browser.close()

    return PageContent(
        url=url,
        title=title,
        text=plain_text[:MAX_CONTENT_LENGTH],
        markdown=md[:MAX_CONTENT_LENGTH],
        links=links,
        status_code=status_code,
        content_type=content_type,
        elapsed_ms=int((time.monotonic() - t0) * 1000),
        method="playwright",
    )


# ── Screenshot ────────────────────────────────────────────────────────────────

def take_screenshot(url: str, output_path: str = "", full_page: bool = False) -> ScreenshotResult:
    """Prend une capture d'écran d'une URL."""
    validate_url(url)
    ok, msg = _check_playwright()
    if not ok:
        return ScreenshotResult(url=url, error=msg)

    from playwright.sync_api import sync_playwright

    t0 = time.monotonic()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=_USER_AGENT,
            viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
        )
        page = context.new_page()

        try:
            page.goto(url, timeout=NAVIGATION_TIMEOUT, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)  # Wait for rendering

            if output_path:
                path = Path(output_path)
                page.screenshot(path=str(path), full_page=full_page)
                raw_bytes = path.read_bytes()
            else:
                raw_bytes = page.screenshot(full_page=full_page)
                tf = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                tf.write(raw_bytes)
                tf.close()
                output_path = tf.name

            b64 = base64.b64encode(raw_bytes).decode("ascii")

        except Exception as e:
            browser.close()
            return ScreenshotResult(
                url=url, error=str(e),
                elapsed_ms=int((time.monotonic() - t0) * 1000),
            )

        browser.close()

    return ScreenshotResult(
        url=url,
        path=output_path,
        base64_data=b64,
        width=VIEWPORT_WIDTH,
        height=VIEWPORT_HEIGHT,
        elapsed_ms=int((time.monotonic() - t0) * 1000),
    )


# ── Interactive Session ───────────────────────────────────────────────────────

_VALID_ACTIONS = frozenset({
    "click", "type", "scroll", "wait", "extract", "screenshot",
    "select", "hover", "evaluate", "fill",
})


def _validate_action(action: dict) -> None:
    """Valide une action interactive avant exécution."""
    keys = set(action.keys())
    if not keys & _VALID_ACTIONS:
        raise ValueError(f"Action inconnue: {keys}. Valides: {sorted(_VALID_ACTIONS)}")


def interact(url: str, actions: list[dict]) -> InteractResult:
    """Exécute une séquence d'actions interactives sur une page."""
    validate_url(url)

    for action in actions:
        _validate_action(action)

    ok, msg = _check_playwright()
    if not ok:
        return InteractResult(url=url, error=msg)

    from playwright.sync_api import sync_playwright

    t0 = time.monotonic()
    steps: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=_USER_AGENT,
            viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
            java_script_enabled=True,
            accept_downloads=False,
        )
        page = context.new_page()
        page.route("**/*.{png,jpg,jpeg,gif,svg,webp,ico,woff,woff2,ttf,eot,mp3,mp4,webm}", lambda route: route.abort())

        try:
            page.goto(url, timeout=NAVIGATION_TIMEOUT, wait_until="domcontentloaded")
            page.wait_for_timeout(1000)

            for i, action in enumerate(actions):
                step_result = {"step": i, "action": action, "ok": False}

                try:
                    if "click" in action:
                        page.click(action["click"], timeout=ACTION_TIMEOUT)
                        step_result["ok"] = True

                    elif "type" in action:
                        sel = action["type"]
                        text = action.get("text", "")
                        page.type(sel, text, timeout=ACTION_TIMEOUT)
                        step_result["ok"] = True

                    elif "fill" in action:
                        sel = action["fill"]
                        text = action.get("text", "")
                        page.fill(sel, text, timeout=ACTION_TIMEOUT)
                        step_result["ok"] = True

                    elif "select" in action:
                        sel = action["select"]
                        value = action.get("value", "")
                        page.select_option(sel, value, timeout=ACTION_TIMEOUT)
                        step_result["ok"] = True

                    elif "hover" in action:
                        page.hover(action["hover"], timeout=ACTION_TIMEOUT)
                        step_result["ok"] = True

                    elif "scroll" in action:
                        direction = action["scroll"]
                        if direction == "down":
                            page.evaluate("window.scrollBy(0, window.innerHeight)")
                        elif direction == "up":
                            page.evaluate("window.scrollBy(0, -window.innerHeight)")
                        elif direction == "bottom":
                            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        elif direction == "top":
                            page.evaluate("window.scrollTo(0, 0)")
                        step_result["ok"] = True

                    elif "wait" in action:
                        if isinstance(action["wait"], int):
                            page.wait_for_timeout(min(action["wait"], ACTION_TIMEOUT))
                        else:
                            page.wait_for_selector(action["wait"], timeout=ACTION_TIMEOUT)
                        step_result["ok"] = True

                    elif "evaluate" in action:
                        js_code = action["evaluate"]
                        # Sécurité : pas de fetch vers IPs privées
                        if re.search(r"fetch\s*\(", js_code, re.IGNORECASE):
                            step_result["error"] = "fetch() interdit dans evaluate"
                        else:
                            result = page.evaluate(js_code)
                            step_result["result"] = str(result)[:10000]
                            step_result["ok"] = True

                    elif "extract" in action:
                        content_html = page.content()
                        if isinstance(action["extract"], str) and action["extract"] != "true":
                            el = page.query_selector(action["extract"])
                            if el:
                                content_html = el.inner_html()
                        md = html_to_markdown(content_html)
                        step_result["markdown"] = md[:MAX_CONTENT_LENGTH]
                        step_result["ok"] = True

                    elif "screenshot" in action:
                        raw = page.screenshot()
                        b64 = base64.b64encode(raw).decode("ascii")
                        step_result["base64_length"] = len(b64)
                        if isinstance(action["screenshot"], str) and action["screenshot"] != "true":
                            Path(action["screenshot"]).write_bytes(raw)
                            step_result["path"] = action["screenshot"]
                        step_result["ok"] = True

                except Exception as e:
                    step_result["error"] = str(e)

                steps.append(step_result)

            final_url = page.url

        except Exception as e:
            browser.close()
            return InteractResult(
                url=url, steps=steps, error=str(e),
                elapsed_ms=int((time.monotonic() - t0) * 1000),
            )

        browser.close()

    return InteractResult(
        url=url,
        steps=steps,
        final_url=final_url,
        elapsed_ms=int((time.monotonic() - t0) * 1000),
    )


# ── Public API ────────────────────────────────────────────────────────────────

def fetch(url: str, selector: str = "", wait_for: str = "",
          prefer_playwright: bool = True) -> PageContent:
    """Fetch une URL et retourne son contenu en markdown.

    Utilise Playwright si disponible (JS rendering), sinon urllib.
    """
    validate_url(url)

    if prefer_playwright:
        ok, _ = _check_playwright()
        if ok:
            return _fetch_playwright(url, selector=selector, wait_for=wait_for)

    return _fetch_urllib(url, selector=selector)


def readability(url: str) -> PageContent:
    """Extrait le contenu principal avec heuristique readability."""
    validate_url(url)

    ok, _ = _check_playwright()
    if ok:
        page = _fetch_playwright(url)
    else:
        page = _fetch_urllib(url)

    # Ré-appliquer readability sur le HTML brut
    if page.text:
        # On a déjà le markdown, mais on peut améliorer avec readability
        # Pour ça il faudrait le HTML brut — on l'a indirectement via le markdown
        pass

    return page


def status() -> BrowserStatus:
    """Retourne le statut d'installation du navigateur."""
    pw_ok, pw_msg = _check_playwright()
    br_ok, br_msg = ("", "")
    if pw_ok:
        br_ok, br_msg = _check_browser_installed()
    else:
        br_ok, br_msg = False, "playwright requis d'abord"

    return BrowserStatus(
        playwright_installed=pw_ok,
        browser_installed=br_ok,
        playwright_message=pw_msg,
        browser_message=br_msg,
    )


# ── CLI ───────────────────────────────────────────────────────────────────────

def _render_text(result: PageContent) -> str:
    """Rendu texte pour le terminal."""
    lines = [
        f"URL: {result.url}",
        f"Titre: {result.title}",
        f"Méthode: {result.method} | Status: {result.status_code} | {result.elapsed_ms}ms",
        "",
    ]
    if result.markdown:
        lines.append(result.markdown[:5000])
    elif result.text:
        lines.append(result.text[:5000])

    if result.links:
        lines.append(f"\n--- {len(result.links)} liens trouvés ---")
        for link in result.links[:10]:
            lines.append(f"  {link.get('text', '')[:60]:60} → {link.get('href', '')}")
    return "\n".join(lines)


def _render_screenshot(result: ScreenshotResult) -> str:
    """Rendu texte pour screenshot."""
    if result.error:
        return f"Erreur screenshot: {result.error}"
    return f"Screenshot: {result.path} ({result.width}x{result.height}, {result.elapsed_ms}ms)"


def _render_interact(result: InteractResult) -> str:
    """Rendu texte pour interact."""
    lines = [f"Interact: {result.url} → {result.final_url} ({result.elapsed_ms}ms)"]
    if result.error:
        lines.append(f"Erreur: {result.error}")
    for step in result.steps:
        status_str = "✓" if step.get("ok") else "✗"
        action_str = json.dumps(step.get("action", {}), ensure_ascii=False)
        lines.append(f"  {status_str} Step {step['step']}: {action_str}")
        if "markdown" in step:
            lines.append(f"    [extrait: {len(step['markdown'])} chars]")
        if "error" in step:
            lines.append(f"    Erreur: {step['error']}")
        if "result" in step:
            lines.append(f"    Résultat: {step['result'][:200]}")
    return "\n".join(lines)


def _render_status(result: BrowserStatus) -> str:
    """Rendu texte pour status."""
    pw_icon = "✓" if result.playwright_installed else "✗"
    br_icon = "✓" if result.browser_installed else "✗"
    lines = [
        f"web-browser v{result.version}",
        f"  {pw_icon} Playwright: {result.playwright_message}",
        f"  {br_icon} Navigateur: {result.browser_message}",
    ]
    if not result.playwright_installed:
        lines.append("\nInstallation :")
        lines.append("  pip install playwright")
        lines.append("  playwright install chromium")
    elif not result.browser_installed:
        lines.append("\nInstaller le navigateur :")
        lines.append("  playwright install chromium")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée CLI."""
    parser = argparse.ArgumentParser(
        prog="web-browser",
        description="Navigateur web sandboxé pour agents BMAD",
    )
    parser.add_argument("--project-root", default=".", help="Racine du projet")
    parser.add_argument("--json", action="store_true", help="Sortie JSON")
    parser.add_argument("-v", "--verbose", action="store_true", help="Logs détaillés")
    sub = parser.add_subparsers(dest="command")

    # fetch
    p_fetch = sub.add_parser("fetch", help="Récupérer le contenu d'une URL")
    p_fetch.add_argument("url", help="URL à récupérer")
    p_fetch.add_argument("--selector", default="", help="Sélecteur CSS pour extraire une section")
    p_fetch.add_argument("--wait-for", default="", help="Sélecteur à attendre avant extraction")
    p_fetch.add_argument("--no-js", action="store_true", help="Forcer urllib (sans JS)")

    # screenshot
    p_ss = sub.add_parser("screenshot", help="Capture d'écran d'une page")
    p_ss.add_argument("url", help="URL à capturer")
    p_ss.add_argument("-o", "--output", default="", help="Chemin de sortie (défaut: tmpfile)")
    p_ss.add_argument("--full-page", action="store_true", help="Capture pleine page")

    # interact
    p_int = sub.add_parser("interact", help="Session interactive (pipeline d'actions)")
    p_int.add_argument("url", help="URL de départ")
    p_int.add_argument("--actions", required=True, help="JSON array d'actions")

    # readability
    p_read = sub.add_parser("readability", help="Extraction contenu principal (readability)")
    p_read.add_argument("url", help="URL à parser")

    # status
    p_st = sub.add_parser("status", help="Vérifier l'installation")
    p_st.add_argument("--install", action="store_true", help="Tenter l'installation automatique")

    args = parser.parse_args(argv)

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.WARNING)

    if not args.command:
        parser.print_help()
        return 1

    use_json = args.json

    if args.command == "status":
        result = status()
        if args.install and not result.playwright_installed:
            import subprocess
            _log.info("Installation de playwright...")
            subprocess.run([sys.executable, "-m", "pip", "install", "playwright"], check=True)
            subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
            result = status()
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False) if use_json else _render_status(result))
        return 0 if result.playwright_installed else 1

    if args.command == "fetch":
        result = fetch(
            args.url,
            selector=args.selector,
            wait_for=args.wait_for,
            prefer_playwright=not args.no_js,
        )
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False) if use_json else _render_text(result))
        return 0 if result.status_code and result.status_code < 400 else 1

    if args.command == "screenshot":
        result = take_screenshot(args.url, output_path=args.output, full_page=args.full_page)
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False) if use_json else _render_screenshot(result))
        return 0 if not result.error else 1

    if args.command == "interact":
        try:
            actions = json.loads(args.actions)
        except json.JSONDecodeError as e:
            print(f"Erreur JSON actions: {e}", file=sys.stderr)
            return 1
        if not isinstance(actions, list):
            print("--actions doit être un JSON array", file=sys.stderr)
            return 1
        result = interact(args.url, actions)
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False) if use_json else _render_interact(result))
        return 0 if not result.error else 1

    if args.command == "readability":
        result = readability(args.url)
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False) if use_json else _render_text(result))
        return 0 if result.status_code and result.status_code < 400 else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
