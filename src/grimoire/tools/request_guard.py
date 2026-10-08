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

from typing import Protocol
from urllib.parse import urlparse

__all__ = ["LOOPBACK_NAMES", "check_host", "check_mutation", "has_body"]

LOOPBACK_NAMES = ("127.0.0.1", "localhost", "::1")

#: ``Sec-Fetch-Site`` émis par un navigateur pour une page qui n'est pas l'interface.
_FOREIGN_FETCH_SITES = ("cross-site", "same-site")

Refusal = tuple[int, str]


class _Headers(Protocol):
    def get(self, name: str, default: str | None = None, /) -> str | None: ...


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
    return (
        parsed.scheme == "http"
        and (parsed.hostname or "").lower() in LOOPBACK_NAMES
        and origin_port == port
    )


def check_mutation(headers: _Headers, port: int, *, with_body: bool) -> Refusal | None:
    """Contrôle d'une écriture ; ``port`` est celui sur lequel ce serveur écoute."""
    refused = check_host(headers)
    if refused:
        return refused
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
