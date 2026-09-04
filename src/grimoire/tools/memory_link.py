"""Lien projet ↔ base de données mémoire — surface produit (brique B1).

Un projet Grimoire déclare son backend mémoire dans sa config (``memory:
backend``) ; ce module rend ce lien **visible et pilotable** : catalogue des
backends connus (avec descriptions humaines pour le wizard) et statut du lien
pour un projet donné (backend configuré, backend résolu, disponibilité,
volumétrie) — en **best-effort** : l'API ne doit jamais casser parce qu'un
serveur vectoriel est éteint ou qu'un projet n'est pas initialisé.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

MEMORY_LINK_SCHEMA_VERSION = "grimoire-memory-link/v1"

# Catalogue des backends mémoire (source CLI : cmd_init.KNOWN_BACKENDS) avec
# les descriptions humaines que le wizard web affiche. `local` = zéro
# dépendance ; les backends serveur exigent un service qui tourne.
BACKEND_CATALOGUE: tuple[dict[str, str], ...] = (
    {
        "id": "auto",
        "label": "Auto (recommandé)",
        "detail": "Choisit le meilleur backend local disponible — lexical "
        "(FTS5 BM25) si SQLite le supporte, sinon JSON local.",
        "kind": "local",
    },
    {
        "id": "lexical",
        "label": "Lexical (SQLite FTS5)",
        "detail": "Recherche BM25 insensible aux diacritiques, zéro dépendance.",
        "kind": "local",
    },
    {
        "id": "local",
        "label": "JSON local",
        "detail": "Store JSON minimal, sans index — le repli le plus simple.",
        "kind": "local",
    },
    {
        "id": "tantivy-local",
        "label": "Tantivy (embarqué)",
        "detail": "Moteur full-text Rust (classe Lucene), stemming fr+en — "
        "corpus volumineux. Extra : pip install grimoire-kit[search].",
        "kind": "local",
    },
    {
        "id": "qdrant-local",
        "label": "Qdrant embarqué",
        "detail": "Vecteurs sémantiques sans serveur (qdrant-client local).",
        "kind": "local",
    },
    {
        "id": "qdrant-server",
        "label": "Qdrant (serveur)",
        "detail": "Vecteurs sémantiques sur un serveur Qdrant qui tourne.",
        "kind": "server",
    },
    {
        "id": "weaviate-server",
        "label": "Weaviate (serveur)",
        "detail": "Vecteurs sémantiques sur un serveur Weaviate qui tourne.",
        "kind": "server",
    },
    {
        "id": "mempalace",
        "label": "MemPalace (ChromaDB)",
        "detail": "Palais de mémoire compatible MemPalace (chromadb).",
        "kind": "local",
    },
    {
        "id": "ollama",
        "label": "Ollama (embeddings locaux)",
        "detail": "Embeddings via une instance Ollama locale.",
        "kind": "server",
    },
)


def backend_catalogue() -> dict[str, Any]:
    """Charge utile de ``/api/backends`` — le choix offert au wizard."""
    return {
        "schemaVersion": MEMORY_LINK_SCHEMA_VERSION,
        "backends": list(BACKEND_CATALOGUE),
    }


def _store_graph_parity(manager: Any, entries: int) -> dict[str, Any]:
    """Dérive store ↔ graphe : le signal qu'une projection cassée produit.

    Trois COUNT sur Neo4j — assez léger pour une route de statut, là où
    ``memory graph verify`` reconstruit tout le code graph. Dict vide quand
    aucun graphe n'est câblé : une absence de graphe n'est pas une dérive.
    """
    graph = getattr(manager, "memory_graph", None)
    if graph is None:
        return {}
    try:
        stats = graph.stats()
        graph_memories = int(stats.get("memories", 0))
        vector_objects = int(stats.get("weaviate_objects", 0))
    except Exception as exc:  # une sonde de statut ne casse jamais la route
        return {"error": str(exc)}
    return {
        "storeEntries": entries,
        "graphMemories": graph_memories,
        "graphVectorObjects": vector_objects,
        "drift": entries - graph_memories,
        "ok": entries == graph_memories == vector_objects,
    }


def memory_link_status(project_root: Path) -> dict[str, Any]:
    """Statut du lien projet ↔ BDD mémoire, best-effort.

    États : ``uninitialized`` (pas de config projet), ``unavailable``
    (backend configuré mais injoignable/non installé), ``ok``.

    Porte aussi le contrat de couches du Memory OS (``layers``) et la dérive
    store ↔ graphe (``parity``), pour que le cockpit affiche l'état réel des
    sept couches au lieu de le déduire d'heuristiques de fichiers.
    """
    from grimoire.core.config import GrimoireConfig
    from grimoire.core.exceptions import GrimoireConfigError, GrimoireMemoryError

    status: dict[str, Any] = {
        "schemaVersion": MEMORY_LINK_SCHEMA_VERSION,
        "projectRoot": str(project_root),
        "state": "uninitialized",
        "configuredBackend": None,
        "resolvedBackend": None,
        "available": False,
        "entries": None,
        "error": None,
        "detail": {},
        "layers": [],
        "parity": {},
    }
    # Lecture stricte au root servi (pas de remontée d'arborescence : un projet
    # non initialisé ne doit pas hériter de la config d'un parent).
    config_path = project_root / "project-context.yaml"
    if not config_path.is_file():
        return status
    try:
        cfg = GrimoireConfig.from_yaml(config_path)
    except (GrimoireConfigError, OSError) as exc:
        status["error"] = str(exc)
        return status
    status["configuredBackend"] = cfg.memory.backend
    health = None
    try:
        from grimoire.memory.manager import MemoryManager

        mgr = MemoryManager.from_config(cfg, project_root=project_root)
        health = mgr.health_check()
        status["resolvedBackend"] = health.backend
        status["available"] = health.healthy
        status["entries"] = health.entries
        status["detail"] = dict(health.detail)
        status["state"] = "ok" if health.healthy else "unavailable"
        status["parity"] = _store_graph_parity(mgr, health.entries)
    except (GrimoireMemoryError, ImportError, OSError, ValueError) as exc:
        status["state"] = "unavailable"
        status["error"] = str(exc)

    # Le contrat de couches se calcule depuis la config : il reste disponible
    # même quand le backend est mort — c'est précisément là qu'on en a besoin.
    try:
        from grimoire.memory.architecture import build_memory_architecture_status

        architecture = build_memory_architecture_status(
            cfg, project_root=project_root, backend_status=health
        )
        status["layers"] = [layer.to_dict() for layer in architecture.layers]
        status["layerProfile"] = architecture.profile
    except (ImportError, OSError, ValueError) as exc:
        status["error"] = status["error"] or str(exc)
    return status
