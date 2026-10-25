"""Un store mémoire existe-t-il déjà ? — sans le créer.

Les constructeurs de backends créent ce qu'ils ouvrent (collection Qdrant ou
Weaviate, fichier SQLite, répertoire). Pour un aperçu, ou pour refuser une
source mal orthographiée, il faut savoir si le store existe **avant** de
construire le backend : une faute de frappe dans ``--source-collection``
créerait sinon une source vide et afficherait « 0 souvenir ».
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING
from urllib import error, parse, request

from grimoire.memory.manager import store_identity

if TYPE_CHECKING:
    from grimoire.core.config import GrimoireConfig

__all__ = ["store_exists"]

_PROBE_TIMEOUT_SECONDS = 3.0


def store_exists(config: GrimoireConfig, project_root: Path) -> bool | None:
    """``True``/``False`` quand on peut le savoir sans rien créer, ``None`` sinon (backend embarqué, serveur muet)."""
    mem = config.memory
    backend = mem.backend
    location, collection = store_identity(config, project_root)
    if backend in ("local", "tantivy-local", "mempalace"):
        return Path(location).exists()
    if backend == "lexical":
        return Path(location).exists() or Path(location).with_name(f"{collection}.json").exists()
    if backend in ("qdrant-server", "ollama") and location.startswith(("http://", "https://")):
        return _qdrant_collection_exists(location, collection)
    if backend == "weaviate-server":
        return _weaviate_class_exists(location, collection)
    return None


def _qdrant_collection_exists(url: str, collection: str) -> bool | None:
    try:
        from qdrant_client import QdrantClient

        client = QdrantClient(url=url, api_key=os.environ.get("GRIMOIRE_QDRANT_API_KEY") or None, timeout=int(_PROBE_TIMEOUT_SECONDS))
        return bool(client.collection_exists(collection))
    except Exception:  # serveur injoignable ou client absent : on ne sait pas, le constructeur le dira
        return None


def _weaviate_class_exists(url: str, collection: str) -> bool | None:
    from grimoire.memory.backends.weaviate import normalize_weaviate_collection

    target = f"{url.rstrip('/')}/v1/schema/{parse.quote(normalize_weaviate_collection(collection))}"
    if not target.startswith(("http://", "https://")):
        return None
    try:
        with request.urlopen(target, timeout=_PROBE_TIMEOUT_SECONDS) as resp:  # noqa: S310 - schéma http(s) vérifié
            return bool(resp.status == 200)
    except error.HTTPError as exc:
        return False if exc.code == 404 else None
    except Exception:
        return None
