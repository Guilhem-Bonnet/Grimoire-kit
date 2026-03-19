"""Jellyseerr — client de gestion des demandes médias.

Client SDK pour Jellyseerr v3+ (seerr-team/seerr), intégré dans le
framework Grimoire. Conçu pour un usage sans faux-positifs avec
traçabilité complète dans Qdrant.

Pipeline anti-faux-positifs::

    from pathlib import Path
    from grimoire.tools.jellyseerr import Jellyseerr

    js = Jellyseerr(Path("."))

    # Rechercher
    result = js.run(action="search", query="Inception", media_type="movie")

    # Vérifier avant demande
    info = js.run(action="info", media_type="movie", media_id=27205)
    if not info["is_available"] and not info["has_pending_request"]:
        req = js.run(action="request", media_type="movie", media_id=27205)

    # Audit complet
    report = js.run(action="report")
"""

from __future__ import annotations

import importlib.util as _iutil
import os

# Imports depuis le module framework (stdlib-only)
# On importe les fonctions pour éviter la duplication de logique
import sys as _sys
from pathlib import Path
from typing import Any

from grimoire.tools._common import GrimoireTool, load_yaml


def _load_framework_module() -> Any:
    """Charge dynamiquement framework/tools/jellyseerr.py."""
    root = Path(__file__).resolve()
    for parent in [root, *root.parents]:
        candidate = parent.parent.parent / "framework" / "tools" / "jellyseerr.py"
        if candidate.is_file():
            spec = _iutil.spec_from_file_location("_fw_jellyseerr", candidate)
            if spec and spec.loader:
                mod = _iutil.module_from_spec(spec)
                _sys.modules["_fw_jellyseerr"] = mod
                spec.loader.exec_module(mod)  # type: ignore[union-attr]
                return mod
    return None


_fw = _load_framework_module()


class Jellyseerr(GrimoireTool):
    """Client Jellyseerr avec pipeline anti-faux-positifs et mémoire Qdrant.

    Configuration (par ordre de priorité) :
    1. Paramètres kwargs de ``run()``
    2. Variables d'environnement (``JELLYSEERR_URL``, ``JELLYSEERR_API_KEY``, etc.)
    3. ``project-context.yaml`` (section ``jellyseerr``)

    Le module framework est requis pour les opérations (chargement dynamique).
    """

    def __init__(self, project_root: Path) -> None:
        super().__init__(project_root)
        self._config = self._load_config()

    def _load_config(self) -> dict:
        """Charge la configuration depuis project-context.yaml."""
        ctx_file = self._project_root / "project-context.yaml"
        if not ctx_file.is_file():
            return {}
        try:
            ctx = load_yaml(ctx_file) or {}
            return ctx.get("jellyseerr") or {}
        except Exception:
            return {}

    def _resolve_url(self, override: str | None = None) -> str:
        return (
            override
            or os.environ.get("JELLYSEERR_URL")
            or self._config.get("url")
            or "http://localhost:5055"
        )

    def _resolve_api_key(self, override: str | None = None) -> str | None:
        return (
            override
            or os.environ.get("JELLYSEERR_API_KEY")
            or self._config.get("api_key")
        )

    def _resolve_qdrant_url(self, override: str | None = None) -> str | None:
        return (
            override
            or os.environ.get("QDRANT_URL")
            or self._config.get("qdrant_url")
        )

    def _resolve_ollama_url(self, override: str | None = None) -> str | None:
        return (
            override
            or os.environ.get("OLLAMA_URL")
            or self._config.get("ollama_url")
        )

    def run(self, **kwargs: Any) -> dict[str, Any]:
        """Exécute une action Jellyseerr.

        Args:
            action: "health" | "search" | "info" | "request" | "status" |
                    "list" | "cancel" | "sync" | "report"
            url: URL Jellyseerr (surcharge JELLYSEERR_URL)
            api_key: Clé API (surcharge JELLYSEERR_API_KEY)
            qdrant_url: URL Qdrant optionnel
            ollama_url: URL Ollama optionnel (embeddings)

            # search
            query: str — titre à rechercher
            media_type: "movie" | "tv"
            page: int (défaut: 1)

            # info / request
            media_type: "movie" | "tv"
            media_id: int — TMDB ID
            seasons: list[int] — saisons TV (request uniquement)
            force: bool — ignorer anti-doublon (request uniquement)

            # status / cancel
            request_id: int

            # list
            filter_status: "all" | "approved" | "pending" | "declined" | ...
            take: int (défaut: 50)

        Returns:
            dict avec au moins ``"ok"`` (bool) et les données de l'action.
        """
        if _fw is None:
            return {
                "ok": False,
                "error": "Module framework jellyseerr.py introuvable (vérifier l'installation grimoire)",
            }

        action = kwargs.get("action", "health")
        url = self._resolve_url(kwargs.get("url"))
        api_key = self._resolve_api_key(kwargs.get("api_key"))
        qdrant_url = self._resolve_qdrant_url(kwargs.get("qdrant_url"))
        verbose = kwargs.get("verbose", False)

        # Construire le client HTTP
        try:
            client = _fw.JellyseerrClient(base_url=url, api_key=api_key)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        # Construire le client Qdrant (optionnel)
        qdrant = None
        if qdrant_url:
            qdrant = _fw._build_qdrant_client(qdrant_url)

        # Dispatcher
        if action == "health":
            return _fw.cmd_health(client, verbose=verbose)

        if action == "search":
            return _fw.cmd_search(
                client,
                query=kwargs["query"],
                media_type=kwargs.get("media_type"),
                page=kwargs.get("page", 1),
                verbose=verbose,
            )

        if action == "info":
            return _fw.cmd_info(
                client,
                media_type=kwargs["media_type"],
                media_id=int(kwargs["media_id"]),
                verbose=verbose,
            )

        if action == "request":
            return _fw.cmd_request(
                client,
                media_type=kwargs["media_type"],
                media_id=int(kwargs["media_id"]),
                seasons=kwargs.get("seasons"),
                force=kwargs.get("force", False),
                qdrant_client=qdrant,
                verbose=verbose,
            )

        if action == "status":
            return _fw.cmd_status(client, int(kwargs["request_id"]), verbose=verbose)

        if action == "list":
            return _fw.cmd_list(
                client,
                filter_status=kwargs.get("filter_status", "all"),
                take=kwargs.get("take", 50),
                verbose=verbose,
            )

        if action == "cancel":
            return _fw.cmd_cancel(client, int(kwargs["request_id"]), verbose=verbose)

        if action == "sync":
            return _fw.cmd_sync(client, qdrant, kwargs.get("take", 200), verbose=verbose)

        if action == "report":
            return _fw.cmd_report(client, qdrant, verbose=verbose)

        return {"ok": False, "error": f"Action inconnue: {action}"}
