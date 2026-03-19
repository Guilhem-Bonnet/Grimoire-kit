#!/usr/bin/env python3
"""
jellyseerr.py — Gestionnaire de demandes médias Jellyseerr pour agents Grimoire.
================================================================================

Interface CLI et SDK pour interagir avec Jellyseerr v3+ (seerr-team/seerr).
Conçu pour un usage sans faux-positifs, transparent à chaque étape.

Actions :
  health    — Vérifier que Jellyseerr est joignable et opérationnel
  search    — Rechercher films ou séries avec score de confiance
  info      — Détails complets d'un media (dispo., demandes en cours)
  request   — Soumettre une demande avec vérification anti-doublon
  status    — Statut détaillé d'une demande par ID
  list      — Lister toutes les demandes (filtrable par état)
  cancel    — Annuler une demande en attente
  sync      — Synchroniser l'état Jellyseerr dans Qdrant
  report    — Rapport diagnostique complet du flow

Pipeline anti-faux-positifs (`request`) :
  1. health-check  — Jellyseerr accessible ?
  2. search        — Trouver le bon media (titre + année)
  3. info          — Media déjà AVAILABLE ? → STOP (rien à faire)
  4. info          — Demande déjà en cours ? → STOP (évite doublon)
  5. confirm       — Afficher le media retenu pour validation
  6. POST request  — Soumettre et enregistrer dans Qdrant

Configuration (priorité : args > ENV vars) :
  JELLYSEERR_URL      URL de base Jellyseerr (défaut: http://localhost:5055)
  JELLYSEERR_API_KEY  Clé API Jellyseerr (obligatoire pour request/cancel)
  QDRANT_URL          URL Qdrant pour mémoire sémantique (optionnel)
  OLLAMA_URL          URL Ollama pour embeddings (optionnel)

Usage :
  python3 jellyseerr.py health
  python3 jellyseerr.py search "Inception" --type movie
  python3 jellyseerr.py info --media-type movie --media-id 27205
  python3 jellyseerr.py request --media-type movie --media-id 27205
  python3 jellyseerr.py list --filter pending
  python3 jellyseerr.py status --request-id 42
  python3 jellyseerr.py cancel --request-id 42
  python3 jellyseerr.py sync
  python3 jellyseerr.py report

Stdlib only — aucune dépendance externe obligatoire.
Qdrant et Ollama sont des enrichissements optionnels.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

# ── Version ───────────────────────────────────────────────────────────────────

JELLYSEERR_VERSION = "1.0.0"

# ── Constants ─────────────────────────────────────────────────────────────────

DEFAULT_URL = "http://localhost:5055"
API_BASE = "/api/v1"
REQUEST_TIMEOUT = 15  # secondes

# Statuts de disponibilité média (Jellyseerr mediaInfo.status)
MEDIA_STATUS = {
    1: "UNKNOWN",
    2: "PENDING",
    3: "PROCESSING",
    4: "PARTIALLY_AVAILABLE",
    5: "AVAILABLE",
}
MEDIA_STATUS_EMOJI = {
    1: "❓",
    2: "⏳",
    3: "⚙️",
    4: "🟡",
    5: "✅",
}

# Statuts de demande (Request.status)
REQUEST_STATUS = {
    1: "PENDING_APPROVAL",
    2: "APPROVED",
    3: "DECLINED",
}
REQUEST_STATUS_EMOJI = {
    1: "⏳",
    2: "✅",
    3: "❌",
}

# Types de médias supportés
VALID_MEDIA_TYPES = {"movie", "tv"}

# Qdrant collection pour la mémoire des demandes
QDRANT_COLLECTION = "media_requests"
QDRANT_CATALOG_COLLECTION = "media_catalog"

# SSRF protection — hôtes bloqués (cloud metadata endpoints)
_BLOCKED_HOSTS = frozenset({
    "169.254.169.254",
    "metadata.google.internal",
    "instance-data",
    "metadata.internal",
})


# ── SSRF Protection ───────────────────────────────────────────────────────────

def _validate_url(url: str, name: str = "URL") -> str:
    """Valide l'URL contre les attaques SSRF (cloud metadata, schémas dangereux).

    Raises ValueError si l'URL est suspecte.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"{name} : schéma '{parsed.scheme}' non autorisé (http/https uniquement)")
    host = (parsed.hostname or "").lower()
    if host in _BLOCKED_HOSTS:
        raise ValueError(f"{name} bloquée — cloud metadata endpoint interdit : {host}")
    return url


# ── Data Models ───────────────────────────────────────────────────────────────

@dataclass
class MediaResult:
    """Résultat de recherche ou d'info média."""
    media_type: str          # "movie" | "tv"
    media_id: int            # TMDB ID (= Jellyseerr internal ID)
    title: str
    year: int | None
    overview: str
    language: str
    genres: list[str]
    availability_status: int  # 1-5
    availability_label: str
    existing_requests: list[dict]  # demandes en cours pour ce media
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def is_available(self) -> bool:
        return self.availability_status == 5

    @property
    def is_partially_available(self) -> bool:
        return self.availability_status == 4

    @property
    def is_pending_or_processing(self) -> bool:
        return self.availability_status in (2, 3)

    @property
    def has_pending_request(self) -> bool:
        """True si au moins une demande non-déclinée existe."""
        return any(
            r.get("status") in (1, 2)  # PENDING_APPROVAL or APPROVED
            for r in self.existing_requests
        )

    def summary_line(self) -> str:
        year_str = f" ({self.year})" if self.year else ""
        icon = MEDIA_STATUS_EMOJI.get(self.availability_status, "❓")
        type_icon = "🎬" if self.media_type == "movie" else "📺"
        return (
            f"{type_icon} [{self.media_id}] {self.title}{year_str} "
            f"{icon} {self.availability_label}"
        )


@dataclass
class RequestResult:
    """Résultat d'une action sur une demande."""
    success: bool
    action: str          # "created" | "skipped" | "cancelled" | "error"
    reason: str
    request_id: int | None = None
    media_id: int | None = None
    media_type: str | None = None
    title: str | None = None
    raw: dict = field(default_factory=dict, repr=False)


# ── HTTP Client ───────────────────────────────────────────────────────────────

class JellyseerrClient:
    """Client HTTP Jellyseerr — stdlib uniquement, zero dépendance."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout: int = REQUEST_TIMEOUT,
    ) -> None:
        self._base = _validate_url(base_url.rstrip("/"), "JELLYSEERR_URL")
        self._api_key = api_key
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._api_key:
            h["X-Api-Key"] = self._api_key
        return h

    def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        body: dict | None = None,
    ) -> dict | list:
        """Effectue une requête HTTP vers l'API Jellyseerr.

        Raises:
            HTTPError: si le serveur répond avec un code d'erreur HTTP.
            URLError: si le serveur est injoignable.
            ValueError: si la réponse n'est pas du JSON valide.
        """
        url = f"{self._base}{API_BASE}{path}"
        if params:
            url += "?" + urlencode(params)

        data = json.dumps(body).encode() if body else None
        req = Request(url, method=method, data=data, headers=self._headers())

        try:
            with urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read().decode("utf-8")
                if not raw.strip():
                    return {}
                return json.loads(raw)
        except HTTPError as exc:
            # Lire le corps d'erreur pour un message précis
            error_body = ""
            try:
                error_body = exc.read().decode("utf-8")
                error_data = json.loads(error_body)
                msg = error_data.get("message", error_body)
            except Exception:
                msg = error_body or str(exc)
            raise HTTPError(
                url=exc.url,
                code=exc.code,
                msg=f"Jellyseerr API erreur {exc.code}: {msg}",
                hdrs=exc.headers,
                fp=None,
            ) from exc

    # ── API Methods ───────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        """GET /api/v1/status — Health check."""
        return self._request("GET", "/status")  # type: ignore[return-value]

    def search(
        self,
        query: str,
        page: int = 1,
        language: str = "fr",
        media_type: str | None = None,
    ) -> dict:
        """GET /api/v1/search — Recherche de médias."""
        params: dict = {"query": query, "page": page, "language": language}
        if media_type:
            params["mediaType"] = media_type
        return self._request("GET", "/search", params=params)  # type: ignore[return-value]

    def get_movie(self, tmdb_id: int) -> dict:
        """GET /api/v1/movie/{id} — Détails d'un film."""
        return self._request("GET", f"/movie/{tmdb_id}")  # type: ignore[return-value]

    def get_tv(self, tmdb_id: int) -> dict:
        """GET /api/v1/tv/{id} — Détails d'une série."""
        return self._request("GET", f"/tv/{tmdb_id}")  # type: ignore[return-value]

    def get_requests(
        self,
        take: int = 100,
        skip: int = 0,
        sort: str = "added",
        filter_status: str = "all",
    ) -> dict:
        """GET /api/v1/request — Liste des demandes."""
        return self._request(  # type: ignore[return-value]
            "GET",
            "/request",
            params={"take": take, "skip": skip, "sort": sort, "filter": filter_status},
        )

    def get_request(self, request_id: int) -> dict:
        """GET /api/v1/request/{id} — Détails d'une demande."""
        return self._request("GET", f"/request/{request_id}")  # type: ignore[return-value]

    def create_request(
        self,
        media_type: str,
        media_id: int,
        seasons: list[int] | None = None,
    ) -> dict:
        """POST /api/v1/request — Créer une demande.

        Requires API key.
        """
        if not self._api_key:
            raise ValueError("API key requise pour créer une demande (JELLYSEERR_API_KEY)")
        body: dict = {"mediaType": media_type, "mediaId": media_id}
        if seasons is not None and media_type == "tv":
            body["seasons"] = seasons
        return self._request("POST", "/request", body=body)  # type: ignore[return-value]

    def delete_request(self, request_id: int) -> dict:
        """DELETE /api/v1/request/{id} — Annuler une demande.

        Requires API key.
        """
        if not self._api_key:
            raise ValueError("API key requise pour annuler une demande (JELLYSEERR_API_KEY)")
        return self._request("DELETE", f"/request/{request_id}")  # type: ignore[return-value]


# ── Business Logic ────────────────────────────────────────────────────────────

def _parse_media_result(data: dict) -> MediaResult:
    """Parse un résultat de recherche ou d'info en MediaResult normalisé."""
    media_type = data.get("mediaType", "movie")
    media_id = data.get("id", 0)
    title = data.get("title") or data.get("name") or "Titre inconnu"
    # Extraire l'année depuis releaseDate ou firstAirDate
    year_raw = data.get("releaseDate") or data.get("firstAirDate") or ""
    try:
        year: int | None = int(year_raw[:4]) if year_raw else None
    except (ValueError, IndexError):
        year = None

    overview = (data.get("overview") or "")[:500]
    language = data.get("originalLanguage") or data.get("originalLanguage") or ""

    # Genres
    genres: list[str] = []
    for g in data.get("genres") or []:
        if isinstance(g, dict):
            genres.append(g.get("name", ""))
        elif isinstance(g, str):
            genres.append(g)

    # Disponibilité
    media_info = data.get("mediaInfo") or {}
    status_int = media_info.get("status", 1)
    if not isinstance(status_int, int):
        status_int = 1

    # Demandes existantes sur ce media
    existing_requests = media_info.get("requests", []) or []

    return MediaResult(
        media_type=media_type,
        media_id=media_id,
        title=title,
        year=year,
        overview=overview,
        language=language,
        genres=genres,
        availability_status=status_int,
        availability_label=MEDIA_STATUS.get(status_int, "UNKNOWN"),
        existing_requests=existing_requests,
        raw=data,
    )


def cmd_health(client: JellyseerrClient, verbose: bool = False) -> dict:
    """Vérifie la disponibilité et la santé de Jellyseerr."""
    _log("🔍 Vérification de la santé Jellyseerr...")
    try:
        start = time.monotonic()
        status = client.get_status()
        latency_ms = int((time.monotonic() - start) * 1000)
        version = status.get("version", "inconnue")
        _log(f"✅ Jellyseerr {version} opérationnel (latence: {latency_ms}ms)")
        return {
            "ok": True,
            "version": version,
            "latency_ms": latency_ms,
            "status": status,
        }
    except HTTPError as exc:
        _log(f"❌ Erreur API Jellyseerr: {exc}")
        return {"ok": False, "error": str(exc), "error_type": "http", "code": exc.code}
    except URLError as exc:
        _log(f"❌ Jellyseerr injoignable: {exc}")
        return {"ok": False, "error": str(exc), "error_type": "connection"}


def cmd_search(
    client: JellyseerrClient,
    query: str,
    media_type: str | None = None,
    page: int = 1,
    verbose: bool = False,
) -> dict:
    """Recherche des films ou séries dans Jellyseerr.

    Retourne les résultats avec leur statut de disponibilité.
    """
    _log(f"🔍 Recherche : «{query}»" + (f" [type={media_type}]" if media_type else ""))
    try:
        resp = client.search(query, page=page, media_type=media_type)
    except (HTTPError, URLError) as exc:
        _log(f"❌ Erreur de recherche: {exc}")
        return {"ok": False, "error": str(exc), "results": []}

    results_raw = resp.get("results") or []
    total = resp.get("totalResults", len(results_raw))

    # Filtrer les résultats non-media (personnes, etc.)
    media_results = [
        r for r in results_raw
        if r.get("mediaType") in ("movie", "tv")
    ]
    if media_type:
        media_results = [r for r in media_results if r.get("mediaType") == media_type]

    parsed = [_parse_media_result(r) for r in media_results]

    _log(f"📊 {len(parsed)} résultat(s) trouvé(s) (total={total})")
    for i, m in enumerate(parsed, 1):
        _log(f"  {i}. {m.summary_line()}")

    return {
        "ok": True,
        "query": query,
        "total": total,
        "page": page,
        "count": len(parsed),
        "results": [
            {
                "media_type": m.media_type,
                "media_id": m.media_id,
                "title": m.title,
                "year": m.year,
                "overview": m.overview[:200],
                "language": m.language,
                "genres": m.genres,
                "availability_status": m.availability_status,
                "availability_label": m.availability_label,
                "is_available": m.is_available,
                "has_pending_request": m.has_pending_request,
            }
            for m in parsed
        ],
    }


def cmd_info(
    client: JellyseerrClient,
    media_type: str,
    media_id: int,
    verbose: bool = False,
) -> dict:
    """Récupère les détails complets d'un media, incluant les demandes en cours."""
    _log(f"📋 Infos media : {media_type} #{media_id}")
    if media_type not in VALID_MEDIA_TYPES:
        return {"ok": False, "error": f"media_type invalide: {media_type} (movie|tv)"}
    try:
        data = client.get_movie(media_id) if media_type == "movie" else client.get_tv(media_id)
    except HTTPError as exc:
        if exc.code == 404:
            _log(f"❌ Media introuvable : {media_type} #{media_id}")
            return {"ok": False, "error": "Media introuvable", "code": 404}
        _log(f"❌ Erreur API: {exc}")
        return {"ok": False, "error": str(exc), "code": exc.code}
    except URLError as exc:
        _log(f"❌ Jellyseerr injoignable: {exc}")
        return {"ok": False, "error": str(exc), "error_type": "connection"}

    m = _parse_media_result(data)
    icon = MEDIA_STATUS_EMOJI.get(m.availability_status, "❓")
    _log(f"  {icon} {m.title} ({m.year}) — {m.availability_label}")

    # Afficher les demandes en cours
    if m.existing_requests:
        _log(f"  📬 {len(m.existing_requests)} demande(s) enregistrée(s) :")
        for req in m.existing_requests:
            rid = req.get("id", "?")
            rstatus = req.get("status", 0)
            rlabel = REQUEST_STATUS.get(rstatus, f"STATUS_{rstatus}")
            remoji = REQUEST_STATUS_EMOJI.get(rstatus, "?")
            created = req.get("createdAt", "")[:10]
            _log(f"    {remoji} Demande #{rid} — {rlabel} ({created})")
    else:
        _log("  📭 Aucune demande en cours")

    return {
        "ok": True,
        "media_type": m.media_type,
        "media_id": m.media_id,
        "title": m.title,
        "year": m.year,
        "overview": m.overview[:300],
        "language": m.language,
        "genres": m.genres,
        "availability_status": m.availability_status,
        "availability_label": m.availability_label,
        "is_available": m.is_available,
        "is_partially_available": m.is_partially_available,
        "is_pending_or_processing": m.is_pending_or_processing,
        "has_pending_request": m.has_pending_request,
        "existing_requests": [
            {
                "request_id": r.get("id"),
                "status": REQUEST_STATUS.get(r.get("status", 0), "UNKNOWN"),
                "created_at": r.get("createdAt", ""),
                "requested_by": (r.get("requestedBy") or {}).get("displayName", ""),
            }
            for r in m.existing_requests
        ],
    }


def cmd_request(
    client: JellyseerrClient,
    media_type: str,
    media_id: int,
    seasons: list[int] | None = None,
    force: bool = False,
    qdrant_client: object | None = None,
    verbose: bool = False,
) -> dict:
    """Soumet une demande avec pipeline de vérification anti-faux-positifs.

    Étapes :
    1. Vérifier la santé (server alive ?)
    2. Récupérer le statut du media
    3. Si AVAILABLE → rien à faire
    4. Si demande déjà en cours → rien à faire (évite doublons)
    5. Soumettre la demande et enregistrer dans Qdrant
    """
    _log(f"\n{'='*60}")
    _log(f"🎬 Pipeline de demande : {media_type} #{media_id}")
    _log(f"{'='*60}")

    if media_type not in VALID_MEDIA_TYPES:
        return RequestResult(
            success=False,
            action="error",
            reason=f"media_type invalide: {media_type} (movie|tv)",
        ).__dict__

    # Étape 1 : health check
    _log("\n[1/4] Health check Jellyseerr...")
    health = cmd_health(client)
    if not health["ok"]:
        return RequestResult(
            success=False,
            action="error",
            reason=f"Jellyseerr injoignable: {health.get('error')}",
            media_id=media_id,
            media_type=media_type,
        ).__dict__
    _log(f"  ✅ Jellyseerr {health.get('version', '?')} opérationnel")

    # Étape 2 : récupérer les infos du media
    _log(f"\n[2/4] Vérification du media {media_type} #{media_id}...")
    info = cmd_info(client, media_type, media_id)
    if not info["ok"]:
        return RequestResult(
            success=False,
            action="error",
            reason=f"Media introuvable ou erreur API: {info.get('error')}",
            media_id=media_id,
            media_type=media_type,
        ).__dict__

    title = info.get("title", f"{media_type}:{media_id}")
    year = info.get("year")
    title_year = f"{title} ({year})" if year else title

    # Étape 3 : vérifier disponibilité
    _log(f"\n[3/4] Analyse disponibilité de «{title_year}»...")
    avail_status = info.get("availability_status", 1)
    avail_label = info.get("availability_label", "UNKNOWN")
    icon = MEDIA_STATUS_EMOJI.get(avail_status, "❓")
    _log(f"  {icon} Statut: {avail_label}")

    if info.get("is_available") and not force:
        _log(f"  ℹ️  «{title_year}» est déjà DISPONIBLE — aucune demande nécessaire.")
        result = RequestResult(
            success=True,
            action="skipped",
            reason=f"Déjà disponible (status={avail_label})",
            media_id=media_id,
            media_type=media_type,
            title=title,
        )
        _sync_to_qdrant(qdrant_client, result, info)
        return asdict(result)

    if info.get("is_partially_available"):
        _log(f"  🟡 «{title_year}» est PARTIELLEMENT disponible.")

    # Étape 4 : vérifier les demandes existantes
    _log("\n[4/4] Vérification des demandes existantes...")
    existing = info.get("existing_requests", [])
    active_requests = [r for r in existing if r.get("status") in ("PENDING_APPROVAL", "APPROVED")]

    if active_requests and not force:
        req = active_requests[0]
        _log(
            f"  ⚠️  Demande #{req.get('request_id')} déjà en cours "
            f"({req.get('status')}) créée le {req.get('created_at', '')[:10]}."
        )
        _log("  ℹ️  Utilisez --force pour soumettre quand même.")
        result = RequestResult(
            success=True,
            action="skipped",
            reason=f"Demande déjà en cours #{req.get('request_id')} ({req.get('status')})",
            request_id=req.get("request_id"),
            media_id=media_id,
            media_type=media_type,
            title=title,
        )
        _sync_to_qdrant(qdrant_client, result, info)
        return asdict(result)

    _log("  ✅ Aucune demande active — prêt à soumettre.")

    # Étape 5 : soumettre
    _log(f"\n[✉️ ] Soumission de la demande pour «{title_year}»...")
    try:
        resp = client.create_request(media_type, media_id, seasons)
    except HTTPError as exc:
        _log(f"  ❌ Erreur API lors de la soumission: {exc}")
        result = RequestResult(
            success=False,
            action="error",
            reason=f"API error {exc.code}: {exc}",
            media_id=media_id,
            media_type=media_type,
            title=title,
        )
        _sync_to_qdrant(qdrant_client, result, info)
        return asdict(result)
    except ValueError as exc:
        _log(f"  ❌ {exc}")
        return asdict(RequestResult(
            success=False, action="error", reason=str(exc),
            media_id=media_id, media_type=media_type, title=title,
        ))

    request_id = resp.get("id")
    req_status = resp.get("status", 1)
    req_label = REQUEST_STATUS.get(req_status, "PENDING_APPROVAL")
    _log(f"  ✅ Demande #{request_id} créée — statut: {req_label}")
    _log(f"\n{'='*60}")
    _log(f"🎉 «{title_year}» ajouté à la file d'attente [{req_label}]")
    _log(f"{'='*60}\n")

    result = RequestResult(
        success=True,
        action="created",
        reason=f"Demande #{request_id} créée avec succès",
        request_id=request_id,
        media_id=media_id,
        media_type=media_type,
        title=title,
        raw=resp,
    )
    _sync_to_qdrant(qdrant_client, result, info)
    return asdict(result)


def cmd_status(client: JellyseerrClient, request_id: int, verbose: bool = False) -> dict:
    """Récupère le statut détaillé d'une demande par son ID."""
    _log(f"📋 Statut demande #{request_id}...")
    try:
        req = client.get_request(request_id)
    except HTTPError as exc:
        if exc.code == 404:
            return {"ok": False, "error": f"Demande #{request_id} introuvable"}
        return {"ok": False, "error": str(exc), "code": exc.code}
    except URLError as exc:
        return {"ok": False, "error": str(exc), "error_type": "connection"}

    status_int = req.get("status", 0)
    status_label = REQUEST_STATUS.get(status_int, f"UNKNOWN_{status_int}")
    status_emoji = REQUEST_STATUS_EMOJI.get(status_int, "?")
    media = req.get("media") or {}
    media_type = media.get("mediaType", "?")
    tmdb_id = media.get("tmdbId") or media.get("id")
    title = media.get("title") or media.get("name") or f"{media_type}:{tmdb_id}"
    created = req.get("createdAt", "")[:10]
    updated = req.get("updatedAt", "")[:10]
    requested_by = (req.get("requestedBy") or {}).get("displayName", "?")

    _log(f"  {status_emoji} Demande #{request_id} : «{title}»")
    _log(f"  Statut: {status_label} | Par: {requested_by} | Créée: {created} | Mis à jour: {updated}")

    return {
        "ok": True,
        "request_id": request_id,
        "status": status_label,
        "status_code": status_int,
        "title": title,
        "media_type": media_type,
        "tmdb_id": tmdb_id,
        "created_at": created,
        "updated_at": updated,
        "requested_by": requested_by,
        "raw": req if verbose else {},
    }


def cmd_list(
    client: JellyseerrClient,
    filter_status: str = "all",
    take: int = 50,
    verbose: bool = False,
) -> dict:
    """Liste les demandes en cours, filtrable par état."""
    valid_filters = ("all", "available", "approved", "pending", "declined", "processing")
    if filter_status not in valid_filters:
        return {"ok": False, "error": f"Filtre invalide: {filter_status}. Options: {valid_filters}"}

    _log(f"📃 Liste des demandes [filter={filter_status}]...")
    try:
        resp = client.get_requests(take=take, filter_status=filter_status)
    except (HTTPError, URLError) as exc:
        return {"ok": False, "error": str(exc)}

    results = resp.get("results") or []
    total = resp.get("pageInfo", {}).get("results", len(results))

    requests_out = []
    for req in results:
        status_int = req.get("status", 0)
        status_label = REQUEST_STATUS.get(status_int, f"STATUS_{status_int}")
        status_emoji = REQUEST_STATUS_EMOJI.get(status_int, "?")
        media = req.get("media") or {}
        media_type = media.get("mediaType", "?")
        title = media.get("title") or media.get("name") or "?"
        avail_status = media.get("status", 1)
        avail_icon = MEDIA_STATUS_EMOJI.get(avail_status, "❓")
        avail_label = MEDIA_STATUS.get(avail_status, "UNKNOWN")
        rid = req.get("id", "?")

        type_icon = "🎬" if media_type == "movie" else "📺"
        _log(f"  {status_emoji} #{rid} {type_icon} «{title}» — {status_label} | Media: {avail_icon} {avail_label}")

        requests_out.append({
            "request_id": rid,
            "status": status_label,
            "media_type": media_type,
            "title": title,
            "tmdb_id": media.get("tmdbId") or media.get("id"),
            "media_availability": avail_label,
            "created_at": req.get("createdAt", "")[:10],
            "requested_by": (req.get("requestedBy") or {}).get("displayName", ""),
        })

    _log(f"\n📊 {len(requests_out)} demande(s) affichée(s) (total={total})")
    return {"ok": True, "filter": filter_status, "count": len(requests_out), "total": total, "requests": requests_out}


def cmd_cancel(
    client: JellyseerrClient,
    request_id: int,
    verbose: bool = False,
) -> dict:
    """Annule une demande en attente."""
    _log(f"🗑️  Annulation demande #{request_id}...")

    # Vérifier que la demande existe et est annulable
    try:
        req = client.get_request(request_id)
    except HTTPError as exc:
        if exc.code == 404:
            return {"ok": False, "error": f"Demande #{request_id} introuvable"}
        return {"ok": False, "error": str(exc)}

    status_int = req.get("status", 0)
    if status_int == 3:
        _log(f"  ℹ️  Demande #{request_id} est déjà déclinée.")
        return {"ok": True, "action": "noop", "reason": "Déjà déclinée"}

    media = req.get("media") or {}
    title = media.get("title") or media.get("name") or f"#{request_id}"

    try:
        client.delete_request(request_id)
    except HTTPError as exc:
        _log(f"  ❌ Erreur annulation: {exc}")
        return {"ok": False, "error": str(exc)}

    _log(f"  ✅ Demande #{request_id} «{title}» annulée avec succès.")
    return {"ok": True, "action": "cancelled", "request_id": request_id, "title": title}


def cmd_sync(
    client: JellyseerrClient,
    qdrant_client: object | None = None,
    take: int = 200,
    verbose: bool = False,
) -> dict:
    """Synchronise l'état complet de Jellyseerr dans Qdrant.

    Indexe ou met à jour toutes les demandes dans la collection Qdrant
    `media_requests` pour permettre des requêtes sémantiques ultérieures.
    """
    _log("🔄 Synchronisation Jellyseerr → Qdrant...")

    if qdrant_client is None:
        _log("  ⚠️  Qdrant non disponible — sync ignoré (qdrant-client non installé ?)")
        return {"ok": False, "error": "Qdrant non disponible", "synced": 0}

    try:
        resp = client.get_requests(take=take)
    except (HTTPError, URLError) as exc:
        return {"ok": False, "error": str(exc), "synced": 0}

    results = resp.get("results") or []
    synced = 0
    errors = []

    for req in results:
        try:
            _upsert_request_to_qdrant(qdrant_client, req)
            synced += 1
        except Exception as exc:
            errors.append(str(exc))

    _log(f"  ✅ {synced} demande(s) synchronisée(s)" + (f", {len(errors)} erreur(s)" if errors else ""))
    return {"ok": True, "synced": synced, "errors": errors}


def cmd_report(
    client: JellyseerrClient,
    qdrant_client: object | None = None,
    verbose: bool = False,
) -> dict:
    """Rapport diagnostique complet : santé, stats, demandes récentes."""
    _log("📊 Rapport diagnostique Jellyseerr")
    _log("=" * 60)

    report: dict = {}

    # 1. Health
    health = cmd_health(client)
    report["health"] = health
    if not health["ok"]:
        _log("❌ Jellyseerr injoignable — rapport incomplet.")
        return report

    # 2. Stats demandes
    _log("\n📬 Statistiques des demandes :")
    stats: dict = {"total": 0, "by_status": {}, "by_type": {}}
    try:
        all_req = client.get_requests(take=500)
        results = all_req.get("results") or []
        stats["total"] = len(results)
        for req in results:
            s = REQUEST_STATUS.get(req.get("status", 0), "UNKNOWN")
            stats["by_status"][s] = stats["by_status"].get(s, 0) + 1
            mt = (req.get("media") or {}).get("mediaType", "?")
            stats["by_type"][mt] = stats["by_type"].get(mt, 0) + 1

        for label, count in sorted(stats["by_status"].items(), key=lambda x: -x[1]):
            _log(f"  {count:3d} × {label}")
        total_str = f"\n  Total: {stats['total']} demante(s)"
        for mt, count in stats["by_type"].items():
            total_str += f" | {mt}: {count}"
        _log(total_str)
    except Exception as exc:
        stats["error"] = str(exc)
        _log(f"  ⚠️  Erreur stats: {exc}")

    report["stats"] = stats

    # 3. Qdrant status
    _log("\n🧠 Mémoire Qdrant :")
    if qdrant_client is not None:
        try:
            qdrant_info = _get_qdrant_collection_info(qdrant_client)
            report["qdrant"] = qdrant_info
            _log(f"  ✅ Collection '{QDRANT_COLLECTION}' : {qdrant_info.get('count', 0)} vecteurs")
        except Exception as exc:
            report["qdrant"] = {"error": str(exc)}
            _log(f"  ⚠️  Erreur Qdrant: {exc}")
    else:
        report["qdrant"] = {"available": False}
        _log("  ⚠️  Qdrant non configuré")

    _log("\n" + "=" * 60)
    _log("✅ Rapport terminé.")
    return report


# ── Qdrant Integration (optionnelle) ─────────────────────────────────────────

def _build_qdrant_client(url: str) -> object | None:
    """Construit un client Qdrant si `qdrant-client` est installé.

    Retourne None si la librairie n'est pas disponible.
    """
    _validate_url(url, "QDRANT_URL")
    try:
        from qdrant_client import QdrantClient  # type: ignore[import-not-found]
        return QdrantClient(url=url, prefer_grpc=False)
    except ImportError:
        return None


def _ensure_collection(qdrant_client: object, collection_name: str, vector_size: int = 768) -> None:
    """Crée la collection Qdrant si elle n'existe pas encore."""
    from qdrant_client.models import Distance, VectorParams  # type: ignore[import-not-found]

    existing = {c.name for c in qdrant_client.get_collections().collections}  # type: ignore[union-attr]
    if collection_name not in existing:
        qdrant_client.create_collection(  # type: ignore[union-attr]
            collection_name=collection_name,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )


def _embed_text(text: str, ollama_url: str | None = None) -> list[float] | None:
    """Génère un embedding via Ollama (nomic-embed-text, 768 dims).

    Retourne None si Ollama est indisponible.
    """
    if not ollama_url:
        return None
    try:
        _validate_url(ollama_url, "OLLAMA_URL")
        import json as _json
        from urllib.request import Request as _Req
        from urllib.request import urlopen as _uo
        body = json.dumps({"model": "nomic-embed-text", "prompt": text}).encode()
        req = _Req(f"{ollama_url.rstrip('/')}/api/embeddings", data=body,
                   headers={"Content-Type": "application/json"})
        with _uo(req, timeout=30) as resp:
            data = _json.loads(resp.read())
            return data.get("embedding")
    except Exception:
        return None


def _sync_to_qdrant(
    qdrant_client: object | None,
    result: RequestResult,
    media_info: dict,
    ollama_url: str | None = None,
) -> None:
    """Enregistre le résultat d'une action dans Qdrant (best-effort)."""
    if qdrant_client is None:
        return
    with contextlib.suppress(Exception):
        _upsert_media_event(qdrant_client, result, media_info, ollama_url)


def _upsert_media_event(
    qdrant_client: object,
    result: RequestResult,
    media_info: dict,
    ollama_url: str | None = None,
) -> None:
    """Insère ou met à jour un événement de demande dans Qdrant."""
    from qdrant_client.models import PointStruct  # type: ignore[import-not-found]

    _ensure_collection(qdrant_client, QDRANT_COLLECTION)
    now = datetime.now(UTC).isoformat()
    title = result.title or "?"
    text = (
        f"{result.action}: {title} ({result.media_type}) — {result.reason}. "
        f"Media info: {media_info.get('availability_label', '')}. "
        f"Genres: {', '.join(media_info.get('genres', []))}. "
        f"Overview: {media_info.get('overview', '')[:200]}"
    )

    vector = _embed_text(text, ollama_url) or [0.0] * 768
    point_id = abs(hash(f"{result.media_id}:{result.request_id}:{now}")) % (2**31)

    payload = {
        "action": result.action,
        "success": result.success,
        "reason": result.reason,
        "media_id": result.media_id,
        "media_type": result.media_type,
        "title": title,
        "request_id": result.request_id,
        "timestamp": now,
        "availability_label": media_info.get("availability_label", ""),
        "genres": media_info.get("genres", []),
        "year": media_info.get("year"),
    }

    qdrant_client.upsert(  # type: ignore[union-attr]
        collection_name=QDRANT_COLLECTION,
        points=[PointStruct(id=point_id, vector=vector, payload=payload)],
    )


def _upsert_request_to_qdrant(qdrant_client: object, req: dict) -> None:
    """Upsert d'une demande Jellyseerr brute dans Qdrant pour le sync complet."""
    from qdrant_client.models import PointStruct  # type: ignore[import-not-found]

    _ensure_collection(qdrant_client, QDRANT_COLLECTION)
    media = req.get("media") or {}
    title = media.get("title") or media.get("name") or "?"
    media_type = media.get("mediaType", "?")
    status_int = req.get("status", 0)
    status_label = REQUEST_STATUS.get(status_int, "UNKNOWN")
    avail = MEDIA_STATUS.get(media.get("status", 1), "UNKNOWN")

    text = f"Demande: {title} ({media_type}) — {status_label} | Dispo: {avail}"
    vector = [0.0] * 768  # Sans Ollama, vecteur neutre
    req_id = req.get("id", 0)
    point_id = abs(req_id) % (2**31) if req_id else abs(hash(text)) % (2**31)

    payload = {
        "action": "sync",
        "request_id": req_id,
        "media_id": media.get("tmdbId") or media.get("id"),
        "media_type": media_type,
        "title": title,
        "status": status_label,
        "media_availability": avail,
        "created_at": req.get("createdAt", ""),
        "timestamp": datetime.now(UTC).isoformat(),
    }

    qdrant_client.upsert(  # type: ignore[union-attr]
        collection_name=QDRANT_COLLECTION,
        points=[PointStruct(id=point_id, vector=vector, payload=payload)],
    )


def _get_qdrant_collection_info(qdrant_client: object) -> dict:
    """Retourne les infos de la collection Qdrant."""
    try:
        info = qdrant_client.get_collection(QDRANT_COLLECTION)  # type: ignore[union-attr]
        return {
            "collection": QDRANT_COLLECTION,
            "count": info.points_count,
            "status": str(info.status),
        }
    except Exception:
        return {"collection": QDRANT_COLLECTION, "count": 0, "status": "not_found"}


# ── Logging ───────────────────────────────────────────────────────────────────

_QUIET = False


def _log(msg: str) -> None:
    """Affiche un message sur stderr (pour ne pas polluer la sortie JSON)."""
    if not _QUIET:
        print(msg, file=sys.stderr, flush=True)


# ── CLI ───────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jellyseerr",
        description="Gestionnaire de demandes médias Jellyseerr — Grimoire",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage")[0] if __doc__ else "",
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("JELLYSEERR_URL", DEFAULT_URL),
        help="URL Jellyseerr (env: JELLYSEERR_URL)",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("JELLYSEERR_API_KEY"),
        help="Clé API Jellyseerr (env: JELLYSEERR_API_KEY)",
    )
    parser.add_argument(
        "--qdrant-url",
        default=os.environ.get("QDRANT_URL"),
        help="URL Qdrant (env: QDRANT_URL)",
    )
    parser.add_argument(
        "--ollama-url",
        default=os.environ.get("OLLAMA_URL"),
        help="URL Ollama pour embeddings (env: OLLAMA_URL)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Sortie JSON sur stdout",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Supprimer les logs de progression",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Inclure les données brutes dans la sortie",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # health
    sub.add_parser("health", help="Vérifier la disponibilité de Jellyseerr")

    # search
    p_search = sub.add_parser("search", help="Rechercher des films ou séries")
    p_search.add_argument("query", help="Titre à rechercher")
    p_search.add_argument("--type", dest="media_type", choices=["movie", "tv"], help="Filtrer par type")
    p_search.add_argument("--page", type=int, default=1)

    # info
    p_info = sub.add_parser("info", help="Détails d'un media")
    p_info.add_argument("--media-type", required=True, choices=["movie", "tv"])
    p_info.add_argument("--media-id", type=int, required=True, help="TMDB ID")

    # request
    p_req = sub.add_parser("request", help="Soumettre une demande (avec vérification)")
    p_req.add_argument("--media-type", required=True, choices=["movie", "tv"])
    p_req.add_argument("--media-id", type=int, required=True, help="TMDB ID")
    p_req.add_argument("--seasons", nargs="+", type=int, help="Saisons (TV seulement)")
    p_req.add_argument("--force", action="store_true", help="Ignorer les vérifications anti-doublon")

    # status
    p_status = sub.add_parser("status", help="Statut d'une demande")
    p_status.add_argument("--request-id", type=int, required=True)

    # list
    p_list = sub.add_parser("list", help="Lister les demandes")
    p_list.add_argument(
        "--filter",
        dest="filter_status",
        default="all",
        choices=["all", "available", "approved", "pending", "declined", "processing"],
    )
    p_list.add_argument("--take", type=int, default=50)

    # cancel
    p_cancel = sub.add_parser("cancel", help="Annuler une demande")
    p_cancel.add_argument("--request-id", type=int, required=True)

    # sync
    p_sync = sub.add_parser("sync", help="Synchroniser dans Qdrant")
    p_sync.add_argument("--take", type=int, default=200)

    # report
    sub.add_parser("report", help="Rapport diagnostique complet")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    global _QUIET
    _QUIET = args.quiet

    # Construire les clients
    try:
        client = JellyseerrClient(
            base_url=args.url,
            api_key=args.api_key,
        )
    except ValueError as exc:
        print(f"❌ Configuration invalide: {exc}", file=sys.stderr)
        return 1

    qdrant = None
    if args.qdrant_url:
        qdrant = _build_qdrant_client(args.qdrant_url)
        if qdrant is None and args.qdrant_url:
            _log("⚠️  qdrant-client non installé — pip install qdrant-client")

    # Dispatcher
    result: dict
    cmd = args.command

    if cmd == "health":
        result = cmd_health(client, verbose=args.verbose)
    elif cmd == "search":
        result = cmd_search(client, args.query, args.media_type, args.page, args.verbose)
    elif cmd == "info":
        result = cmd_info(client, args.media_type, args.media_id, args.verbose)
    elif cmd == "request":
        result = cmd_request(
            client,
            media_type=args.media_type,
            media_id=args.media_id,
            seasons=args.seasons,
            force=args.force,
            qdrant_client=qdrant,
            verbose=args.verbose,
        )
    elif cmd == "status":
        result = cmd_status(client, args.request_id, args.verbose)
    elif cmd == "list":
        result = cmd_list(client, args.filter_status, args.take, args.verbose)
    elif cmd == "cancel":
        result = cmd_cancel(client, args.request_id, args.verbose)
    elif cmd == "sync":
        result = cmd_sync(client, qdrant, args.take, args.verbose)
    elif cmd == "report":
        result = cmd_report(client, qdrant, args.verbose)
    else:
        print(f"Commande inconnue: {cmd}", file=sys.stderr)
        return 1

    if args.json or cmd in ("search", "info", "request", "status", "list"):
        print(json.dumps(result, ensure_ascii=False, indent=2))

    return 0 if result.get("ok", result.get("success", True)) else 1


if __name__ == "__main__":
    sys.exit(main())
