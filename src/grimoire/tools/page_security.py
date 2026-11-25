"""En-têtes de sécurité des pages HTML — une seule politique pour l'atelier et le cockpit (#1366).

Une page de la coque (``workspace/``) n'a ni script en ligne, ni ``<style>``, ni attribut
``style=``, ni gestionnaire ``on*=`` : elle reçoit la politique **stricte**.

Les pages historiques et vitrine (``?legacy=1``, ``index.html``, ``demo.html``…) portent encore des scripts et
des ``<style>`` en ligne, et leur JavaScript fabrique des ``style="…"`` dans ses gabarits. Elles reçoivent la
politique **à nonce** : chaque ``<script>`` et ``<style>`` du document est marqué d'un nonce tiré à la
réponse (``script-src`` n'a jamais ``'unsafe-inline'``), et seuls les attributs ``style=`` restent permis
(``style-src-attr``). Un script ou un ``<style>`` injecté dans la page n'a pas le nonce : il ne s'exécute pas.

Le module ne dépend d'aucun serveur : il transforme des octets et rend des en-têtes.
"""

from __future__ import annotations

import re
import secrets
from typing import Any

from grimoire.tools.request_guard import SESSION_SCRIPT, SESSION_SCRIPT_PATH, inject_session_token

__all__ = [
    "CSP_STRICT",
    "NoDirectoryListing",
    "csp_with_nonce",
    "html_page",
    "page_headers",
    "serve_session_script",
]

# `img-src data:` : icônes (favicon, pastille de notification) ; `media-src blob: data:` : message vocal
# (WAV en `data:`), `connect-src 'self'` : fetch et EventSource (flux du chat, pouls) du même serveur.
_COMMON = (
    "default-src 'self'; img-src 'self' data:; media-src 'self' blob: data:; connect-src 'self'; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)

#: Politique de la coque : rien d'en ligne, ni script ni style.
CSP_STRICT = "script-src 'self'; style-src 'self'; " + _COMMON

_INLINE_TAG = re.compile(rb"<(script|style)\b(?![^>]*\bnonce=)", re.IGNORECASE)
_INLINE_HINT = re.compile(rb"<style\b|<script\b(?![^>]*\bsrc\s*=)|\sstyle\s*=|\son[a-z]+\s*=", re.IGNORECASE)


def csp_with_nonce(nonce: str) -> str:
    """Politique des pages à scripts ou styles en ligne : nonce pour les balises, ``style=`` toléré seul."""
    return (
        f"script-src 'self' 'nonce-{nonce}'; style-src 'self' 'nonce-{nonce}'; "
        "style-src-attr 'unsafe-inline'; " + _COMMON
    )


def html_page(html: bytes) -> tuple[bytes, list[tuple[str, str]]]:
    """Page prête à servir (jeton de session injecté) et ses en-têtes de sécurité.

    Politique stricte quand le document n'a rien d'en ligne ; sinon politique à nonce, les balises
    ``<script>``/``<style>`` du document étant marquées du nonce de cette réponse.
    """
    body = inject_session_token(html)
    if not _INLINE_HINT.search(html):
        return body, page_headers(CSP_STRICT)
    nonce = secrets.token_urlsafe(16)
    marked = _INLINE_TAG.sub(lambda m: b"<" + m.group(1) + b' nonce="' + nonce.encode("ascii") + b'"', body)
    return marked, page_headers(csp_with_nonce(nonce))


def page_headers(csp: str) -> list[tuple[str, str]]:
    return [
        ("Content-Security-Policy", csp),
        ("X-Content-Type-Options", "nosniff"),
        ("Referrer-Policy", "no-referrer"),
    ]


def serve_session_script(handler: Any, path: str) -> bool:
    """Sert le relais ``fetch`` si ``path`` le nomme ; rend vrai quand la réponse est partie."""
    if path.split("?", 1)[0] != SESSION_SCRIPT_PATH:
        return False
    handler.send_response(200)
    handler.send_header("Content-Type", "text/javascript; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Content-Length", str(len(SESSION_SCRIPT)))
    handler.end_headers()
    handler.wfile.write(SESSION_SCRIPT)
    return True


class NoDirectoryListing:
    """Mixin de ``SimpleHTTPRequestHandler`` : un dossier sans ``index.html`` est un 404.

    L'index de dossier de la bibliothèque standard est une page HTML générée, sans en-têtes de sécurité :
    le cockpit n'en a pas l'usage, il ne la sert pas.
    """

    def list_directory(self, path: Any) -> None:
        self.send_error(404, "Not Found")  # type: ignore[attr-defined]
