"""Le transport minimal des moteurs à modèle de la conversation de l'Atelier (épique #1477, lot B2).

Un transport est une fonction ``(url, en-têtes, corps, délai) -> (statut, octets)``, la MÊME signature que celle de
:mod:`grimoire.tools.atelier_model` : les tests y glissent un faux, jamais une connexion. Le transport réel :

- ne parle qu'``https``, ou ``http`` vers une boucle locale (``127.0.0.1``, ``localhost``, ``::1``) ; ni identifiants dans
  l'URL, ni requête, ni fragment ;
- ne suit AUCUNE redirection et n'utilise aucun proxy d'environnement : l'en-tête d'une clé ne part que vers l'hôte admis ;
- borne la réponse lue ;
- ne dit jamais l'URL, les en-têtes ni le corps dans ses erreurs : un message fixe, jamais une clé.

Toutes les erreurs des moteurs sont des :class:`EngineError` : le gardien les remplace par le moteur sans modèle, et
la vue montre le NOM de la classe (``EngineDelai``, ``EngineReseau``, ``EngineSortie``…) comme raison.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any, Protocol
from urllib.parse import urlsplit

__all__ = [
    "LOOPBACK_HOSTS",
    "MAX_REPONSE",
    "Backend",
    "EngineDelai",
    "EngineError",
    "EngineRefus",
    "EngineReseau",
    "EngineSortie",
    "Transport",
    "default_transport",
    "is_loopback_url",
    "post_json",
]

#: Une réponse plus grosse est hostile ou cassée : refusée.
MAX_REPONSE = 262_144
_MORCEAU = 8192
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

Transport = Callable[[str, dict[str, str], bytes, float], tuple[int, bytes]]


class EngineError(Exception):
    """Une défaillance d'un moteur à modèle. Le message est fixe et ne contient ni clé, ni URL, ni corps de réponse."""


class EngineDelai(EngineError):  # noqa: N818 — le NOM de la classe est la raison que la vue montre
    """Le délai est dépassé."""


class EngineReseau(EngineError):  # noqa: N818
    """Le service est injoignable, a répondu une erreur ou une redirection."""


class EngineSortie(EngineError):  # noqa: N818
    """La sortie du modèle est hors cadre (JSON invalide, champ en trop, texte hors bornes, réponse trop grosse)."""


class EngineRefus(EngineError):  # noqa: N818
    """L'appel n'est pas parti : adresse non admise, clé absente ou entrée hors bornes."""


class Backend(Protocol):
    """Un appel de modèle : ``(texte JSON rendu par le modèle, octets bruts de la réponse)``. Jamais de clé dans une erreur."""

    def completer(self, systeme: str, utilisateur: str, schema: dict[str, Any], max_jetons: int) -> tuple[str, bytes]: ...


def post_json(transport: Transport, url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float) -> tuple[Any, bytes]:
    """Envoie ``payload`` en JSON, rend ``(JSON décodé, octets)`` ; toute défaillance est une :class:`EngineError` au message fixe."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    # Toute erreur est levée HORS du bloc ``except`` : l'exception rendue n'a ni ``__context__`` ni ``__cause__``, donc aucune
    # référence à l'erreur d'origine (dont le texte pourrait citer un secret) ne survit dans une trace ou un journal.
    erreur: EngineError | None = None
    status: Any = None
    raw: Any = b""
    try:
        status, raw = transport(url, {"content-type": "application/json", **headers}, body, timeout)
    except EngineError as exc:
        erreur = exc
    except TimeoutError:
        erreur = EngineDelai("délai dépassé : aucun nouvel essai")
    except Exception as exc:  # tout autre échec du transport : un refus générique, jamais le texte de l'exception
        late = isinstance(getattr(exc, "reason", None), TimeoutError)
        erreur = EngineDelai("délai dépassé : aucun nouvel essai") if late else EngineReseau("service injoignable : aucun nouvel essai")
    if erreur is not None:
        raise erreur
    if not isinstance(status, int) or status != 200:
        raise EngineReseau(f"le service a répondu {status if isinstance(status, int) else '?'} : appel sans effet")
    if not isinstance(raw, bytes) or len(raw) > MAX_REPONSE:
        raise EngineSortie("réponse du service trop volumineuse ou illisible : ignorée")
    decode: Any = None
    illisible = False
    try:
        decode = json.loads(raw)
    except (ValueError, RecursionError):
        illisible = True
    if illisible:
        raise EngineSortie("réponse du service illisible : ignorée")
    return decode, raw


def is_loopback_url(url: str) -> bool:
    """L'adresse est-elle une boucle locale reconnue (http ou https, sans identifiants) ?"""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        _ = parts.port
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and host in LOOPBACK_HOSTS and not (parts.username or parts.password)


def _admise(url: str) -> bool:
    try:
        parts = urlsplit(url)
        _ = parts.port
    except ValueError:
        return False
    if not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        return False
    return parts.scheme == "https" or (parts.scheme == "http" and is_loopback_url(url))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def default_transport(url: str, headers: dict[str, str], body: bytes, timeout: float) -> tuple[int, bytes]:
    """``POST`` borné, sans redirection ni proxy ; ``(statut, corps)``. Lève une :class:`EngineError`, jamais autre chose."""
    if not _admise(url):
        raise EngineRefus("adresse du service refusée : https, ou http vers la machine seulement")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")  # noqa: S310 — adresse vérifiée ci-dessus
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
    echeance = time.monotonic() + timeout  # l'échéance vaut pour l'appel ENTIER : le délai du socket ne borne qu'une opération
    erreur: EngineError | None = None
    status, raw = 0, b""
    try:
        with opener.open(request, timeout=timeout) as response:
            status = int(response.status)
            raw = _lire(response, echeance)
    except urllib.error.HTTPError as exc:  # inclut une redirection non suivie : le statut dit tout
        status = int(exc.code)
        try:
            raw = _lire(exc, echeance)
        except EngineError as inner:
            inner.__context__ = None
            erreur = inner
    except EngineError as exc:
        erreur = exc
    except TimeoutError:
        erreur = EngineDelai("délai dépassé : aucun nouvel essai")
    except urllib.error.URLError as exc:
        late = isinstance(exc.reason, TimeoutError)
        erreur = EngineDelai("délai dépassé : aucun nouvel essai") if late else EngineReseau("service injoignable : aucun nouvel essai")
    except (OSError, ValueError):
        erreur = EngineReseau("service injoignable : aucun nouvel essai")
    if erreur is not None:  # levée hors du ``except`` : aucune référence à l'erreur d'origine
        raise erreur
    return status, raw


def _lire(flux: Any, echeance: float) -> bytes:
    """Lit par morceaux, jamais au-delà de ``MAX_REPONSE`` ni de l'échéance totale ; la connexion est fermée par l'appelant."""
    morceaux: list[bytes] = []
    total = 0
    while True:
        if time.monotonic() >= echeance:
            raise EngineDelai("délai dépassé : aucun nouvel essai")
        bloc = bytes(flux.read(min(_MORCEAU, MAX_REPONSE + 1 - total)))
        if not bloc:
            return b"".join(morceaux)
        total += len(bloc)
        if total > MAX_REPONSE:
            raise EngineSortie("réponse du service trop volumineuse : ignorée")
        morceaux.append(bloc)
