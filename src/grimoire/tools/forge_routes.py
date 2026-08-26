"""Table de routage des lectures de l'API locale.

Extrait de :mod:`grimoire.tools.forge_server` pour que la même surface de
lecture serve deux hôtes :

- ``grimoire blueprint serve`` — un projet, l'atelier ;
- ``grimoire cockpit serve`` — N projets du registre, résolus par ``?project=``.

Seules les **lectures** vivent ici. Les mutations restent dans le serveur de
l'atelier : elles portent une garde anti-CSRF et une trace gouvernée qui ne se
transposent pas telles quelles à un hôte multi-projet.

Ce module est une feuille : il ne connaît le serveur que par le protocole
:class:`ReadableForgeAPI`, pour qu'aucun cycle d'import ne relie la feuille à
son hub.
"""

from __future__ import annotations

from typing import Any, Protocol

from grimoire.tools.memory_link import backend_catalogue

__all__ = ["API_GET_UNHANDLED", "ReadableForgeAPI", "api_get"]

# Sentinelle : distingue « route inconnue » d'une route qui répond ``None``.
API_GET_UNHANDLED = object()


class ReadableForgeAPI(Protocol):
    """Surface de lecture attendue par la table de routage.

    Le contrat exact que ``ForgeAPI`` doit honorer — le déclarer ici plutôt que
    d'importer la classe garde la dépendance à sens unique.
    """

    def status(self) -> dict[str, Any]: ...
    def setup_view(self) -> dict[str, Any]: ...
    def archetypes(self) -> list[dict[str, Any]]: ...
    def extensions_view(self) -> dict[str, Any]: ...
    def blueprints_list(self) -> list[dict[str, Any]]: ...
    def events_log(self, limit: int = 200) -> dict[str, Any]: ...
    def stigmergy_view(self) -> dict[str, Any]: ...
    def features_view(self) -> list[dict[str, Any]]: ...
    def cost_model_view(self, model: str | None = None) -> dict[str, Any]: ...
    def otel_export(self, limit: int = 200) -> dict[str, Any]: ...
    def primitives_view(self) -> dict[str, Any]: ...
    def memory_link_view(self) -> dict[str, Any]: ...
    def blueprint_get(self, bp_id: str) -> dict[str, Any]: ...
    def blueprint_diff(self, bp_id: str, ref: str = "HEAD") -> dict[str, Any]: ...


def _exact(api: ReadableForgeAPI, path: str, query: dict[str, list[str]]) -> Any:
    if path == "/api/status":
        return api.status()
    if path == "/api/setup":
        return api.setup_view()
    if path == "/api/archetypes":
        return api.archetypes()
    if path == "/api/extensions":
        return api.extensions_view()
    if path == "/api/blueprints":
        return api.blueprints_list()
    if path == "/api/events/log":
        return api.events_log()
    if path == "/api/stigmergy":
        return api.stigmergy_view()
    if path == "/api/features":
        return api.features_view()
    if path == "/api/cost-model":
        return api.cost_model_view(query.get("model", [None])[0])
    if path == "/api/otel":
        return api.otel_export()
    if path == "/api/primitives":
        return api.primitives_view()
    if path == "/api/backends":
        return backend_catalogue()
    if path == "/api/memory/status":
        return api.memory_link_view()
    return API_GET_UNHANDLED


def api_get(api: ReadableForgeAPI, path: str, query: dict[str, list[str]]) -> Any:
    """Résout une lecture d'API.

    Renvoie la charge utile, ou :data:`API_GET_UNHANDLED` si le chemin ne
    correspond à aucune route de lecture — à l'appelant de décider du repli
    (fichier statique, flux SSE, 404).
    """
    payload = _exact(api, path, query)
    if payload is not API_GET_UNHANDLED:
        return payload
    if path.startswith("/api/blueprints/"):
        # ``ForgeAPI`` valide l'identifiant avant de toucher au disque.
        if path.endswith("/diff"):
            return api.blueprint_diff(path.split("/")[3])
        return api.blueprint_get(path.rsplit("/", 1)[1])
    return API_GET_UNHANDLED
