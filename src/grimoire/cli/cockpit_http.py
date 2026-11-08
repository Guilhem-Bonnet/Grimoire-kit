"""Réponses HTTP du cockpit (P94) : gzip, ``ETag``/304 et chauffe des caches.

Sorti de ``cmd_cockpit`` pour ne pas en gonfler le module ; ``serve_asset``
prend le gestionnaire de requête en premier argument.
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import json
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path
from typing import Any

# P94 — au-delà de ce seuil (octets), JSON et fichiers texte partent en gzip.
GZIP_MIN = 1024
GZIP_TEXT_SUFFIXES = {".js", ".mjs", ".css", ".html", ".svg", ".json", ".txt", ".md"}
_ASSET_CACHE: dict[str, tuple[int, int, bytes, str, bytes | None]] = {}


#: Borne du drainage d'un corps refusé, comme ``request_guard.drain_body``.
DRAIN_LIMIT = 1 << 20


def gzip_body(handler: Any, body: bytes) -> bytes:
    """Compresse ``body`` si le client l'accepte et qu'il dépasse le seuil.

    Pose ``Vary`` et, le cas échéant, ``Content-Encoding`` — à appeler avant
    ``Content-Length``.
    """
    handler.send_header("Vary", "Accept-Encoding")
    if len(body) < GZIP_MIN or not accepts_gzip(handler.headers.get("Accept-Encoding")):
        return body
    handler.send_header("Content-Encoding", "gzip")
    return gzip.compress(body, compresslevel=5, mtime=0)


def accepts_gzip(header: str | None) -> bool:
    """Vrai si ``Accept-Encoding`` admet gzip (et ne le refuse pas par ``q=0``)."""
    for part in (header or "").split(","):
        name, _, params = part.strip().partition(";")
        if name.strip().lower() in {"gzip", "x-gzip"}:
            return params.replace(" ", "").lower() not in {"q=0", "q=0.0", "q=0.00", "q=0.000"}
    return False


def etag_of(body: bytes) -> str:
    """L'``ETag`` d'un corps : l'empreinte de son contenu, entre guillemets (RFC 9110, forme forte)."""
    return f'"{hashlib.blake2b(body, digest_size=16).hexdigest()}"'


def if_none_match(headers: Any, etag: str) -> bool:
    """Vrai si ``If-None-Match`` nomme *etag* (ou ``*``) ; sans l'en-tête, aucun match."""
    candidates = headers.get("If-None-Match")
    if not candidates:
        return False
    wanted = {c.strip().removeprefix("W/") for c in candidates.split(",")}
    return "*" in wanted or etag in wanted


def not_modified(headers: Any, etag: str, mtime: float) -> bool:
    """Validation conditionnelle : ``If-None-Match`` prime sur ``If-Modified-Since``."""
    if headers.get("If-None-Match"):
        return if_none_match(headers, etag)
    since = headers.get("If-Modified-Since")
    if since:
        try:
            return int(mtime) <= int(parsedate_to_datetime(since).timestamp())
        except (TypeError, ValueError, OverflowError):
            return False
    return False


def send_json(handler: Any, code: int, payload: dict[str, object], *, etag: bool = False) -> None:
    """Réponse JSON du cockpit ; ``etag`` pose l'empreinte du corps et honore ``If-None-Match`` (304).

    Le corps de la requête est lu en entier avant la première ligne de réponse (#857) ; la connexion est
    ensuite fermée (``HTTP/1.0``), l'en-tête le dit au client, et le tampon est vidé avant que ``finish``
    ne ferme le socket. L'``ETag`` porte sur le corps *décompressé* (GAO-perf-cockpit-002) : le client qui
    le renvoie ne reçoit pas les 88 à 215 Ko inchangés ; ``Vary: Accept-Encoding`` dit que l'encodage varie.
    """
    handler._drain_unread_body()
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    tag = etag_of(body) if etag and code == 200 else ""
    if tag and if_none_match(handler.headers, tag):
        handler.send_response(304)
        handler.send_header("ETag", tag)
        handler.send_header("Vary", "Accept-Encoding")
        handler.end_headers()
        handler.wfile.flush()
        return
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    if tag:
        handler.send_header("ETag", tag)
    body = gzip_body(handler, body)
    handler.send_header("Content-Length", str(len(body)))
    if handler.close_connection:  # le client l'a demandé (ou l'erreur de requête l'impose) : on le dit
        handler.send_header("Connection", "close")
    handler.end_headers()
    handler.wfile.write(body)
    handler.wfile.flush()


def warm_sheet(root: Path) -> None:
    """Chauffe les caches de ``sheet`` et de ``tasks`` ; un échec ne coûte que la chauffe."""
    with contextlib.suppress(Exception):
        from grimoire.tools import workspace_api

        workspace_api.sheet_view(root)
        workspace_api.tasks_view(root)


def serve_asset(self: Any, target: Path) -> None:
    """Fichier statique avec ``ETag`` (empreinte du contenu) et réponse 304.

    ``Cache-Control: no-cache`` pour JS/CSS : le navigateur revalide à chaque
    chargement et ne reçoit le corps que si le fichier a changé — jamais un
    module périmé après une mise à jour du kit. Icônes et polices gardent
    une minute de fraîcheur.
    """
    try:
        stat = target.stat()
        key = str(target)
        cached = _ASSET_CACHE.get(key)
        if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
            _, _, data, etag, packed = cached
        else:
            data = target.read_bytes()
            etag = f'"{hashlib.sha256(data).hexdigest()[:32]}"'
            packed = (
                gzip.compress(data, compresslevel=6, mtime=0)
                if len(data) >= GZIP_MIN and target.suffix.lower() in GZIP_TEXT_SUFFIXES
                else None
            )
            _ASSET_CACHE[key] = (stat.st_mtime_ns, stat.st_size, data, etag, packed)
    except OSError:
        self.send_error(404, "File not found")
        return
    suffix = target.suffix.lower()
    cache = "no-cache" if suffix in {".js", ".mjs", ".css"} else "max-age=60"
    compressible = suffix in GZIP_TEXT_SUFFIXES
    if not_modified(self.headers, etag, stat.st_mtime):
        self.send_response(304)
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", cache)
        if compressible:
            self.send_header("Vary", "Accept-Encoding")
        self.end_headers()
        return
    body = data
    self.send_response(200)
    self.send_header("Content-Type", self.guess_type(str(target)))
    self.send_header("ETag", etag)
    self.send_header("Last-Modified", formatdate(stat.st_mtime, usegmt=True))
    self.send_header("Cache-Control", cache)
    if compressible:
        self.send_header("Vary", "Accept-Encoding")
        if packed is not None and accepts_gzip(self.headers.get("Accept-Encoding")):
            self.send_header("Content-Encoding", "gzip")
            body = packed
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)


class CountingReader:
    """``rfile`` qui compte les octets lus — pour savoir ce qu'il reste du corps.

    ``http.client`` envoie l'en-tête et le corps en deux ``send`` distincts ;
    sous charge, le serveur peut parser l'en-tête, refuser (403/404), répondre
    et fermer avant que le corps n'arrive. Le corps atteint alors un socket
    fermé : RST, que Windows répercute au client en jetant la réponse déjà
    reçue (``WinError 10053``, #857). Compter ce qui a été lu permet à
    :meth:`_CockpitHandler._drain_unread_body` de consommer *exactement* le
    reste déclaré par ``Content-Length`` avant de répondre, sans jamais
    bloquer sur un corps déjà lu.
    """

    def __init__(self, raw: Any) -> None:
        self._raw = raw
        self.consumed = 0

    def read(self, *args: Any) -> bytes:
        data: bytes = self._raw.read(*args)
        self.consumed += len(data)
        return data

    def readline(self, *args: Any) -> bytes:
        data: bytes = self._raw.readline(*args)
        self.consumed += len(data)
        return data

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw, name)
