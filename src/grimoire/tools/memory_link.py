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


def memory_link_status(project_root: Path) -> dict[str, Any]:
    """Statut du lien projet ↔ BDD mémoire, best-effort.

    États : ``uninitialized`` (pas de config projet), ``unavailable``
    (backend configuré mais injoignable/non installé), ``ok``.
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
    try:
        from grimoire.memory.manager import MemoryManager

        mgr = MemoryManager.from_config(cfg, project_root=project_root)
        health = mgr.health_check()
        status["resolvedBackend"] = health.backend
        status["available"] = health.healthy
        status["entries"] = health.entries
        status["state"] = "ok" if health.healthy else "unavailable"
    except (GrimoireMemoryError, ImportError, OSError, ValueError) as exc:
        status["state"] = "unavailable"
        status["error"] = str(exc)
    return status
