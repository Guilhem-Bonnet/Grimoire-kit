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

import contextlib
import http.client
import json
import socket
import ssl
import threading
import time
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


def _restant(echeance: float) -> float:
    """Le temps qu'il reste avant l'échéance absolue ; ``EngineDelai`` s'il est épuisé."""
    reste = echeance - time.monotonic()
    if reste <= 0:
        raise EngineDelai("délai dépassé : aucun nouvel essai")
    return reste


def _tcp(host: str, port: int, echeance: float) -> socket.socket:
    """Connexion TCP directe : chaque adresse essayée n'a que le temps RESTANT avant l'échéance totale."""
    dernier: OSError | None = None
    for famille, genre, proto, _, adresse in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        sock = socket.socket(famille, genre, proto)
        try:
            sock.settimeout(_restant(echeance))
            sock.connect(adresse)
            return sock
        except OSError as exc:
            dernier = exc
            sock.close()
        except BaseException:
            sock.close()
            raise
    raise dernier if dernier is not None else OSError("aucune adresse")


def _contexte_tls() -> ssl.SSLContext:
    """Le contexte TLS du client : défauts sûrs (certificat et nom d'hôte vérifiés), jamais en dessous de TLS 1.2."""
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def _ouvrir(conn: http.client.HTTPConnection, contexte: ssl.SSLContext | None, echeance: float, gardes: list[threading.Timer]) -> None:
    """Ouvre ``conn`` en direct, sur le temps restant avant ``echeance`` (échéance absolue, calculée avant la connexion).

    Le gardien d'échéance est armé (dans ``gardes``) dès que le socket existe, AVANT la poignée de main TLS ; celle-ci reprend
    le temps restant comme délai de socket. L'appelant ferme la connexion et attend le gardien.
    """
    sock: Any = _tcp(conn.host, conn.port, echeance)
    conn.sock = sock  # la connexion possède le socket : ``conn.close()`` le ferme, même en cas d'échec de la poignée de main
    minuteur = threading.Timer(max(0.01, echeance - time.monotonic()), _couper, args=(conn,))
    minuteur.daemon = True
    gardes.append(minuteur)
    minuteur.start()
    if contexte is not None:
        sock.settimeout(_restant(echeance))
        tls = contexte.wrap_socket(sock, server_hostname=conn.host, do_handshake_on_connect=False)
        conn.sock = tls
        tls.do_handshake()
    sock_final: Any = conn.sock
    sock_final.settimeout(_restant(echeance))


class _Http(http.client.HTTPConnection):
    """``http`` vers la machine : ``connect`` rend le temps restant à chaque étape, gardien armé dès le socket."""

    def __init__(self, host: str, port: int | None, timeout: float, gardes: list[threading.Timer]) -> None:
        super().__init__(host, port, timeout=timeout)
        self._echeance = time.monotonic() + timeout
        self._gardes = gardes

    def connect(self) -> None:
        _ouvrir(self, None, self._echeance, self._gardes)


class _Https(http.client.HTTPSConnection):
    """``https`` : certificat et nom d'hôte vérifiés (contexte par défaut), mêmes bornes que :class:`_Http`."""

    def __init__(self, host: str, port: int | None, timeout: float, gardes: list[threading.Timer]) -> None:
        ctx = _contexte_tls()
        self._contexte = ctx
        super().__init__(host, port, timeout=timeout, context=ctx)
        self._echeance = time.monotonic() + timeout
        self._gardes = gardes

    def connect(self) -> None:
        _ouvrir(self, self._contexte, self._echeance, self._gardes)


def _connexion(parts: Any, timeout: float, gardes: list[threading.Timer] | None = None) -> http.client.HTTPConnection:
    """Une connexion directe, ni proxy d'environnement ni redirection (``http.client`` n'en suit aucune), pas encore ouverte."""
    classe = _Https if parts.scheme == "https" else _Http
    return classe(parts.hostname, parts.port, timeout, gardes if gardes is not None else [])


def _couper(conn: http.client.HTTPConnection) -> None:
    """Le gardien d'échéance : coupe la connexion, ce qui réveille toute lecture bloquée (statut, en-têtes ou corps)."""
    sock = conn.sock
    if sock is not None:
        with contextlib.suppress(OSError):
            sock.shutdown(socket.SHUT_RDWR)
        with contextlib.suppress(OSError):
            sock.close()


def default_transport(url: str, headers: dict[str, str], body: bytes, timeout: float) -> tuple[int, bytes]:
    """``POST`` borné, sans redirection ni proxy ; ``(statut, corps)``. Lève une :class:`EngineError`, jamais autre chose.

    ``timeout`` vaut pour l'appel ENTIER (connexion, poignée de main, statut, en-têtes, corps) : l'échéance absolue est
    calculée avant la connexion, TCP et TLS n'ont que le temps restant, et un minuteur de surveillance, armé dès que le socket
    existe, le coupe à l'échéance ; il est annulé et attendu à la fin, aucun fil ne survit à l'appel. Le délai d'une opération
    de socket ne suffirait pas : chaque octet reçu le relance. Seule la résolution DNS (``getaddrinfo``) n'est pas interruptible.
    """
    if not _admise(url):
        raise EngineRefus("adresse du service refusée : https, ou http vers la machine seulement")
    parts = urlsplit(url)
    debut = time.monotonic()
    echeance = debut + timeout
    erreur: EngineError | None = None
    status, raw = 0, b""
    gardes: list[threading.Timer] = []
    conn = _connexion(parts, timeout, gardes)
    try:
        conn.connect()  # résolution, TCP et TLS sur le temps restant, gardien armé dès le socket
        conn.request("POST", parts.path or "/", body=body, headers=headers)
        reponse = conn.getresponse()
        status = int(reponse.status)
        raw = _lire(reponse, echeance)
    except EngineError as exc:
        erreur = exc
    except (OSError, http.client.HTTPException, ValueError) as exc:
        tard = isinstance(exc, TimeoutError) or time.monotonic() >= echeance - 0.05
        erreur = EngineDelai("délai dépassé : aucun nouvel essai") if tard else EngineReseau("service injoignable : aucun nouvel essai")
    finally:
        for garde in gardes:
            garde.cancel()
            garde.join()
        conn.close()
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
