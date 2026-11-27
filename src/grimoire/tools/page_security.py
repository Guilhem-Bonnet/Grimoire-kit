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

import secrets
from html.parser import HTMLParser
from typing import Any

from grimoire.tools.request_guard import SESSION_SCRIPT, SESSION_SCRIPT_PATH, inject_session_token

__all__ = [
    "CSP_STRICT",
    "SecureHandlerMixin",
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


class _InlineScan(HTMLParser):
    """Position des balises ``<script>``/``<style>`` d'un document, et présence d'un contenu en ligne.

    Un vrai parseur : ``<script`` dans un commentaire HTML, dans une chaîne JavaScript ou dans un attribut n'est pas
    une balise, et le contenu d'un ``<script>`` n'est pas relu comme du balisage.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.tags: list[tuple[int, int, str]] = []  # (ligne, colonne, nom) des balises à marquer
        self.inline = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        names = {name for name, _ in attrs}
        if tag in {"script", "style"}:
            if "nonce" not in names:
                line, col = self.getpos()
                self.tags.append((line, col, tag))
            if tag == "style" or "src" not in names:
                self.inline = True
        if "style" in names or any(name.startswith("on") for name in names):
            self.inline = True


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

    Invariant : ``html`` est un FICHIER DU KIT (``web/``), jamais un contenu produit à partir d'une donnée de projet
    ou d'un utilisateur. Le nonce autorise tout script du document : un script contrôlé par un tiers, mêlé à la page,
    s'y exécuterait. Les appelants (``cmd_cockpit``, ``forge_http``) ne lui passent que le contenu d'un fichier
    statique ; ``test_html_page_ne_recoit_que_des_fichiers_du_kit`` l'impose.
    """
    body = inject_session_token(html)
    text = body.decode("utf-8")
    scan = _InlineScan()
    scan.feed(text)
    scan.close()
    if not scan.inline:
        return body, page_headers(CSP_STRICT)
    nonce = secrets.token_urlsafe(16)
    starts = [0]
    for row in text.split("\n")[:-1]:
        starts.append(starts[-1] + len(row) + 1)
    for line, col, tag in sorted(scan.tags, reverse=True):
        at = starts[line - 1] + col + 1 + len(tag)  # juste après `<script` ou `<style`
        text = text[:at] + f' nonce="{nonce}"' + text[at:]
    return text.encode("utf-8"), page_headers(csp_with_nonce(nonce))


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


class SecureHandlerMixin:
    """Mixin d'un handler ``http.server`` : durcit TOUTES ses réponses, pas seulement les pages servies par ``html_page``.

    * ``nosniff`` sur chaque réponse (JS, CSS, SVG, JSON, redirections, flux) ;
    * une réponse ``text/html`` sans politique (les pages d'erreur 404/501 de la bibliothèque standard) reçoit la
      politique stricte : elles n'ont ni script ni style ;
    * un dossier sans ``index.html`` est un 404 : l'index de dossier de la bibliothèque standard est une page HTML
      générée, le cockpit n'en a pas l'usage.
    """

    #: Page d'erreur sans `<style>` : celle de la bibliothèque standard (3.14) en porte un, que la politique stricte bloque.
    error_message_format = (
        '<!DOCTYPE HTML>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<title>Error response</title>\n</head>\n'
        "<body>\n<h1>Error response</h1>\n<p>Error code: %(code)d</p>\n<p>Message: %(message)s.</p>\n"
        "<p>Error code explanation: %(code)s - %(explain)s.</p>\n</body>\n</html>\n"
    )

    def end_headers(self) -> None:
        sent = [line.decode("latin-1").lower() for line in getattr(self, "_headers_buffer", [])]
        if not any(line.startswith("x-content-type-options:") for line in sent):
            self.send_header("X-Content-Type-Options", "nosniff")  # type: ignore[attr-defined]
        html = any(line.startswith("content-type: text/html") for line in sent)
        if html and not any(line.startswith("content-security-policy:") for line in sent):
            self.send_header("Content-Security-Policy", CSP_STRICT)  # type: ignore[attr-defined]
        super().end_headers()  # type: ignore[misc]

    def list_directory(self, path: Any) -> None:
        self.send_error(404, "Not Found")  # type: ignore[attr-defined]
