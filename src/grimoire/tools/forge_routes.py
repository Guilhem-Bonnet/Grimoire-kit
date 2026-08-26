"""Table de routage des lectures de l'API locale.

Extrait de :mod:`grimoire.tools.forge_server` pour que la même surface de
lecture serve deux hôtes :

- ``grimoire blueprint serve`` — un projet, l'atelier ;
- ``grimoire cockpit serve`` — N projets du registre, résolus par ``?project=``.

Seules les **lectures** vivent ici. Les mutations restent dans le serveur de
l'atelier : elles portent une garde anti-CSRF et une trace gouvernée qui ne se
transposent pas telles quelles à un hôte multi-projet.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from grimoire.tools.memory_link import backend_catalogue

if TYPE_CHECKING:
    from collections.abc import Callable

    from grimoire.tools.forge_server import ForgeAPI

__all__ = ["API_GET_UNHANDLED", "api_get"]

# Sentinelle : distingue « route inconnue » d'une route qui répond ``None``.
API_GET_UNHANDLED = object()


def _exact_routes() -> dict[str, Callable[[ForgeAPI, dict[str, list[str]]], Any]]:
    return {
        "/api/status": lambda api, _q: api.status(),
        "/api/setup": lambda api, _q: api.setup_view(),
        "/api/archetypes": lambda api, _q: api.archetypes(),
        "/api/extensions": lambda api, _q: api.extensions_view(),
        "/api/blueprints": lambda api, _q: api.blueprints_list(),
        "/api/events/log": lambda api, _q: api.events_log(),
        "/api/stigmergy": lambda api, _q: api.stigmergy_view(),
        "/api/features": lambda api, _q: api.features_view(),
        "/api/cost-model": lambda api, q: api.cost_model_view(q.get("model", [None])[0]),
        "/api/otel": lambda api, _q: api.otel_export(),
        "/api/primitives": lambda api, _q: api.primitives_view(),
        "/api/backends": lambda _api, _q: backend_catalogue(),
        "/api/memory/status": lambda api, _q: api.memory_link_view(),
    }


def api_get(api: ForgeAPI, path: str, query: dict[str, list[str]]) -> Any:
    """Résout une lecture d'API.

    Renvoie la charge utile, ou :data:`API_GET_UNHANDLED` si le chemin ne
    correspond à aucune route de lecture — à l'appelant de décider du repli
    (fichier statique, flux SSE, 404).
    """
    route = _exact_routes().get(path)
    if route is not None:
        return route(api, query)
    if path.startswith("/api/blueprints/"):
        if path.endswith("/diff"):
            return api.blueprint_diff(path.split("/")[3])
        return api.blueprint_get(path.rsplit("/", 1)[1])
    return API_GET_UNHANDLED
