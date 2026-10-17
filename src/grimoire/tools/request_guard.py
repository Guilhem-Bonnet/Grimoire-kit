"""Garde des requêtes d'un serveur local — une seule règle pour l'atelier et le cockpit.

Deux portes différentes, deux contrôles :

* **Hôte** (toute requête, lectures comprises) : l'en-tête ``Host`` doit nommer la
  loopback. Ferme le rebinding DNS, où une page tierce devient same-origin.
* **Écriture** (``POST``/``PUT``) : l'interface du produit est la seule à pouvoir
  écrire. Une page tierce peut émettre une requête « simple » vers la loopback
  sans préflight ; on la reconnaît à son ``Origin`` (schéma, hôte et port),
  à ``Sec-Fetch-Site`` et à un ``Content-Type`` autre que JSON, et on la refuse
  avant de lire le corps.

Les outils locaux (curl, scripts) n'envoient ni ``Origin`` ni ``Sec-Fetch-Site`` :
ils passent, à condition de déclarer ``application/json``.

Le module ne dépend d'aucun serveur : il lit un objet d'en-têtes et rend un refus
``(code, message)`` ou ``None``.
"""

from __future__ import annotations

import hmac
import secrets
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from email.message import Message

    _Headers = Message[str, str]

__all__ = [
    "LOOPBACK_NAMES",
    "SESSION_HEADER",
    "check_host",
    "check_mutation",
    "has_body",
    "inject_session_token",
    "new_session_token",
    "session_token",
]

#: En-tête exigé sur toute écriture : le jeton généré au démarrage du serveur.
SESSION_HEADER = "X-Grimoire-Session"

_session_token = secrets.token_urlsafe(32)


def new_session_token() -> str:
    """Tire un jeton neuf (appelé au démarrage d'un serveur) ; l'ancien meurt."""
    global _session_token
    _session_token = secrets.token_urlsafe(32)
    return _session_token


def session_token() -> str:
    return _session_token


def _token_ok(headers: _Headers) -> bool:
    given = headers.get(SESSION_HEADER) or ""
    return hmac.compare_digest(given.encode("utf-8"), _session_token.encode("utf-8"))


def inject_session_token(html: bytes) -> bytes:
    """Insère dans une page HTML servie le jeton et le relais ``fetch``.

    Le script patche ``window.fetch`` : toute écriture vers la même origine porte
    l'en-tête, sans que chaque page ait à le savoir. Un jeton dans une page
    n'est lisible que par une page de la même origine (la politique du navigateur
    interdit à un tiers de lire la réponse).
    """
    script = (
        '<meta name="grimoire-session" content="' + _session_token + '">'
        "<script>(function(){var t=" + repr(_session_token) + ";var f=window.fetch;"
        "window.__grimoireSession=t;"
        "window.fetch=function(u,o){o=o||{};var m=(o.method||(u&&u.method)||'GET')"
        ".toUpperCase();if(m!=='GET'&&m!=='HEAD'&&m!=='OPTIONS'){var h=new Headers("
        "o.headers||(u&&u.headers)||{});if(!h.has('"
        + SESSION_HEADER
        + "'))h.set('"
        + SESSION_HEADER
        + "',t);o.headers=h;}return f.call(this,u,o);};})();</script>"
    ).encode("utf-8")
    lower = html.lower()
    at = lower.find(b"<head>")
    if at >= 0:
        at += len(b"<head>")
        return html[:at] + script + html[at:]
    return script + html


LOOPBACK_NAMES = ("127.0.0.1", "localhost", "::1")

#: ``Sec-Fetch-Site`` émis par un navigateur pour une page qui n'est pas l'interface.
_FOREIGN_FETCH_SITES = ("cross-site", "same-site")

Refusal = tuple[int, str]


def _host_name(host_header: str) -> str:
    """Nom d'hôte d'un en-tête ``Host``, crochets IPv6 compris."""
    value = host_header.strip().lower()
    if value.startswith("["):
        return value[1:].split("]", 1)[0]
    return value.split(":", 1)[0]


def check_host(headers: _Headers) -> Refusal | None:
    """Refuse un ``Host`` qui ne nomme pas la loopback (anti DNS-rebinding)."""
    name = _host_name(headers.get("Host") or "")
    if name not in (*LOOPBACK_NAMES, ""):
        return 403, "hôte non autorisé"
    return None


def _origin_matches(origin: str, port: int) -> bool:
    if origin.strip().lower() == "null":
        return False
    try:
        parsed = urlparse(origin)
        origin_port = parsed.port if parsed.port is not None else 80
    except ValueError:
        return False
    return parsed.scheme == "http" and (parsed.hostname or "").lower() in LOOPBACK_NAMES and origin_port == port


def check_mutation(headers: _Headers, port: int, *, with_body: bool) -> Refusal | None:
    """Contrôle d'une écriture ; ``port`` est celui sur lequel ce serveur écoute."""
    refused = check_host(headers)
    if refused:
        return refused
    if not _token_ok(headers):
        return 403, "jeton de session requis"
    origin = headers.get("Origin")
    if origin is not None and not _origin_matches(origin, port):
        return 403, "origine non autorisée"
    if (headers.get("Sec-Fetch-Site") or "").strip().lower() in _FOREIGN_FETCH_SITES:
        return 403, "origine non autorisée"
    if with_body:
        media_type = (headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if media_type != "application/json":
            return 415, "Content-Type application/json requis"
    return None


def has_body(headers: _Headers) -> bool:
    """Vrai si la requête déclare un corps (``Content-Length`` > 0 ou chunked)."""
    if (headers.get("Transfer-Encoding") or "").strip():
        return True
    try:
        return int(headers.get("Content-Length") or 0) > 0
    except ValueError:
        return True
