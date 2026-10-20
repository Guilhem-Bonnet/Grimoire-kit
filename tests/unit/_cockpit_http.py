"""Client HTTP des tests qui parlent à un vrai serveur du cockpit (#857).

Un seul client pour tous les tests qui montent ``_CockpitHandler`` ou
``make_handler`` sur un port local. Il retente au plus :data:`ATTEMPTS` fois,
uniquement quand la connexion est coupée avant que la réponse ait été lue
(``ConnectionAbortedError`` — ``WinError 10053`` —, ``ConnectionResetError``,
``RemoteDisconnected``), avec un court délai, et relit la réponse en entier.
Il ne masque rien d'autre : une 4xx/5xx reste une 4xx/5xx, un refus de
connexion reste un refus, un délai dépassé reste un délai dépassé.

Importer depuis ``tests/unit`` : ``from ._cockpit_http import get, post`` ;
depuis ``tests/`` (hors paquet) : chargement par chemin (``importlib``),
``tests/`` n'étant pas un paquet en CI.
"""

from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.request
from typing import Any

#: Nombre d'essais au plus — une coupure franche deux fois de suite est
#: rendue telle quelle, le test la voit.
ATTEMPTS = 3
#: Délai entre deux essais, en secondes.
RETRY_DELAY_S = 0.2
#: Les seules erreurs retentées : la connexion est partie avant la réponse.
RETRIED = (ConnectionAbortedError, ConnectionResetError, http.client.RemoteDisconnected)


def _decode(raw: bytes) -> Any:
    text = raw.decode("utf-8", "replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def request(
    port: int,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 20.0,
    data: bytes | None = None,
) -> tuple[int, Any]:
    """``(statut, corps JSON)`` d'une requête sur la loopback, réponse lue en entier.

    ``payload`` est sérialisé en JSON ; ``data`` l'emporte s'il est fourni
    (corps brut). Un corps non JSON revient sous ``{"raw": ...}``.
    """
    body = data if data is not None else (json.dumps(payload).encode("utf-8") if payload is not None else None)
    sent_headers = {"Content-Type": "application/json", **(headers or {})} if body is not None else dict(headers or {})
    last: BaseException | None = None
    for attempt in range(1, ATTEMPTS + 1):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", data=body, method=method, headers=sent_headers
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — loopback de test
                return int(resp.status), _decode(resp.read())
        except urllib.error.HTTPError as exc:
            return int(exc.code), _decode(exc.read())
        except urllib.error.URLError as exc:
            if not isinstance(exc.reason, RETRIED):
                raise
            last = exc
        except RETRIED as exc:
            last = exc
        if attempt < ATTEMPTS:
            time.sleep(RETRY_DELAY_S)
    assert last is not None
    raise last


def get(port: int, path: str, *, timeout: float = 20.0) -> tuple[int, Any]:
    return request(port, "GET", path, timeout=timeout)


def post(
    port: int, path: str, payload: dict[str, Any] | None = None, *, timeout: float = 20.0, **kw: Any
) -> tuple[int, Any]:
    return request(port, "POST", path, payload if payload is not None else {}, timeout=timeout, **kw)


def put(port: int, path: str, payload: dict[str, Any], *, timeout: float = 20.0) -> tuple[int, Any]:
    return request(port, "PUT", path, payload, timeout=timeout)
