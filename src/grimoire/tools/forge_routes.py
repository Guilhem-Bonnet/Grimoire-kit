"""Table de routage des lectures projet de l'API locale.

Extraite de :mod:`grimoire.tools.forge_server` pour que la même surface serve
deux hôtes :

- ``grimoire blueprint serve`` — un projet, l'atelier ;
- ``grimoire cockpit serve`` — N projets du registre, résolus par ``?project=``.

Périmètre volontairement étroit : **les lectures qui ont un sens sur un projet
quelconque**. Les routes blueprint et toutes les mutations restent dans le
serveur de l'atelier — les premières parce qu'un cockpit multi-projet en
lecture seule n'édite pas de blueprint, les secondes parce que leur garde
anti-CSRF et leur trace gouvernée ne se transposent pas telles quelles.

Ce module est une feuille : il ne connaît le serveur que par le protocole
:class:`ReadableForgeAPI`, pour qu'aucun cycle d'import ne relie la feuille à
son hub.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from grimoire.tools.memory_link import backend_catalogue
from grimoire.tools.workspace_routes import PREFIX as WORKSPACE_PREFIX
from grimoire.tools.workspace_routes import WORKSPACE_UNHANDLED, workspace_get

__all__ = ["API_GET_UNHANDLED", "ReadableForgeAPI", "api_get"]

# Sentinelle : distingue « route inconnue » d'une route qui répond ``None``.
API_GET_UNHANDLED = object()


class ReadableForgeAPI(Protocol):
    """Contrat que ``ForgeAPI`` doit honorer pour être routable.

    Le déclarer ici plutôt que d'importer la classe garde la dépendance à sens
    unique, et rend explicite ce que la table consomme vraiment.
    """

    #: Racine du projet servi. L'atelier n'en a qu'une ; le cockpit en construit
    #: une ``ForgeAPI`` par projet résolu. C'est cet attribut, et lui seul, que
    #: les lectures de la vue de travail consomment.
    project_root: Path

    def status(self) -> dict[str, Any]:
        """État du projet servi."""

    def setup_view(self) -> dict[str, Any]:
        """Artefacts installés, par surface."""

    def archetypes(self) -> list[dict[str, Any]]:
        """Catalogue des archétypes."""

    def needs_view(self) -> dict[str, Any]:
        """Catalogue des needs + suggestions pour le projet servi."""

    def setup_run(self) -> dict[str, Any]:
        """Dernier journal d'exécution du wizard (``_grimoire/setup-run.json``)."""

    def extensions_view(self) -> dict[str, Any]:
        """Extensions disponibles et installées."""

    def blueprints_list(self) -> list[dict[str, Any]]:
        """Inventaire des blueprints du projet."""

    def events_log(self, limit: int = 200) -> dict[str, Any]:
        """Journal d'événements du runtime."""

    def stigmergy_view(self) -> dict[str, Any]:
        """Tableau phéromonique."""

    def features_view(self) -> list[dict[str, Any]]:
        """Drapeaux de fonctionnalités."""

    def cost_model_view(self, model: str | None = None) -> dict[str, Any]:
        """Modèle de coût, éventuellement pour un modèle donné."""

    def otel_export(self, limit: int = 200) -> dict[str, Any]:
        """Spans OTel dérivés du journal."""

    def primitives_view(self) -> dict[str, Any]:
        """Primitives déclarées."""

    def memory_link_view(self) -> dict[str, Any]:
        """Lien projet ↔ backend mémoire."""

    def health_view(self) -> dict[str, Any]:
        """Alignement kit, flows et activité réelle du projet."""


def api_get(api: ReadableForgeAPI, path: str, query: dict[str, list[str]]) -> Any:
    """Résout une lecture projet.

    Renvoie la charge utile, ou :data:`API_GET_UNHANDLED` si le chemin ne
    correspond à aucune route — à l'appelant de décider du repli (route propre
    à l'atelier, fichier statique, flux SSE, 404).
    """
    if path == "/api/status":
        return api.status()
    if path == "/api/setup":
        return api.setup_view()
    if path == "/api/archetypes":
        return api.archetypes()
    if path == "/api/needs":
        return api.needs_view()
    if path == "/api/setup/run":
        return api.setup_run()
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
    if path == "/api/health":
        return api.health_view()
    # La vue de travail (web/workspace/) ajoute sa surface sous un préfixe à
    # elle : une seule délégation ici, et les deux hôtes la servent — c'est ce
    # qui rend vraie la clause « la même coque, deux cibles » de la spec.
    #
    # Le test de préfixe précède la lecture de `project_root` à dessein : une
    # route qui n'est pas la nôtre ne doit rien exiger de l'appelant, pas même
    # un attribut.
    if path.startswith(WORKSPACE_PREFIX):
        payload = workspace_get(api.project_root, path, query)
        if payload is not WORKSPACE_UNHANDLED:
            return payload
    return API_GET_UNHANDLED
