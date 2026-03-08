"""
Grimoire Memory Backends — Factory

Sélectionne le backend mémoire selon project-context.yaml et les variables
d'environnement. Priorité : ENV vars > config fichier > auto-détection > local.

Backends disponibles :
  local          — JSON fichier, zéro dépendance (défaut)
  qdrant-local   — Qdrant en process, pip install qdrant-client required
  qdrant-server  — Qdrant distant (URL), circuit breaker intégré
  ollama         — Ollama embeddings + Qdrant (local ou distant)
"""

from __future__ import annotations
import os
import sys
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class MemoryBackend(Protocol):
    """Contrat minimal que tout backend doit respecter."""

    def add(self, text: str, user_id: str = "", metadata: dict = None) -> dict: ...
    def search(self, query: str, user_id: str = "", limit: int = 5) -> list[dict]: ...
    def get_all(self, user_id: str = "") -> list[dict]: ...
    def count(self) -> int: ...
    def status(self) -> dict: ...


def _load_project_context() -> dict:
    """Cherche project-context.yaml depuis le répertoire courant vers la racine."""
    try:
        import yaml
    except ImportError:
        return {}
    for parent in [Path.cwd(), Path.cwd().parent, Path.cwd().parent.parent]:
        f = parent / "project-context.yaml"
        if f.exists():
            with open(f, encoding="utf-8") as fh:
                return yaml.safe_load(fh) or {}
    return {}


def get_backend(config_override: dict | None = None) -> tuple:
    """
    Retourne (backend_instance, backend_name).

    Ordre de priorité :
    1. ENV Grimoire_OLLAMA_URL → ollama
    2. ENV Grimoire_QDRANT_URL → qdrant-server
    3. project-context.yaml memory.backend
    4. Auto-détection
    5. Fallback local
    """
    ctx = config_override or _load_project_context()
    mem_cfg = ctx.get("memory", {})

    # ENV vars priment toujours
    env_ollama = os.environ.get("Grimoire_OLLAMA_URL", "")
    env_qdrant = os.environ.get("Grimoire_QDRANT_URL", "")

    backend_name = mem_cfg.get("backend", "auto")

    if env_ollama:
        backend_name = "ollama"
    elif env_qdrant:
        backend_name = "qdrant-server"

    # Résoudre "auto"
    if backend_name == "auto":
        backend_name = _auto_detect(mem_cfg)

    # Instancier
    return _instantiate(backend_name, mem_cfg, env_ollama, env_qdrant)


def _auto_detect(mem_cfg: dict) -> str:
    """Détection automatique du meilleur backend disponible."""
    import urllib.request
    import urllib.error

    # 1. Qdrant distant configuré ?
    qdrant_url = mem_cfg.get("qdrant_url", os.environ.get("Grimoire_QDRANT_URL", ""))
    ollama_url = mem_cfg.get("ollama_url", os.environ.get("Grimoire_OLLAMA_URL", "http://localhost:11434"))

    # 2. Ollama accessible avec nomic-embed-text ?
    try:
        req = urllib.request.urlopen(f"{ollama_url.rstrip('/')}/api/tags", timeout=1)
        data = req.read().decode()
        if "nomic-embed-text" in data:
            return "ollama"
    except Exception:
        pass

    # 3. Qdrant accessible ?
    if qdrant_url:
        try:
            urllib.request.urlopen(f"{qdrant_url.rstrip('/')}/health", timeout=1)
            return "qdrant-server"
        except Exception:
            pass

    # 4. qdrant-client Python installé ?
    try:
        import qdrant_client  # noqa: F401
        try:
            import sentence_transformers  # noqa: F401
            return "qdrant-local"
        except ImportError:
            pass
    except ImportError:
        pass

    return "local"


def _instantiate(backend_name: str, mem_cfg: dict, env_ollama: str, env_qdrant: str) -> tuple:
    """Instancie le backend avec fallback sur local en cas d'erreur."""
    ollama_url = env_ollama or mem_cfg.get("ollama_url", "http://localhost:11434")
    qdrant_url = env_qdrant or mem_cfg.get("qdrant_url", "")
    embedding_model = mem_cfg.get("embedding_model", "nomic-embed-text")
    collection = mem_cfg.get("collection_prefix", "grimoire")

    if backend_name == "ollama":
        try:
            from .backend_ollama import OllamaBackend
            b = OllamaBackend(
                ollama_url=ollama_url,
                qdrant_url=qdrant_url,
                embedding_model=embedding_model,
                collection=collection,
            )
            return b, "ollama"
        except ImportError:
            _warn_install("ollama", "qdrant-client")
        except Exception as e:
            _warn_connection("ollama", ollama_url, e)

    if backend_name == "qdrant-server":
        try:
            from .backend_qdrant_server import QdrantServerBackend
            b = QdrantServerBackend(
                qdrant_url=qdrant_url or "http://localhost:6333",
                embedding_model=embedding_model,
                collection=collection,
            )
            return b, "qdrant-server"
        except ImportError:
            _warn_install("qdrant-server", "qdrant-client sentence-transformers")
        except Exception as e:
            _warn_connection("qdrant-server", qdrant_url, e)

    if backend_name == "qdrant-local":
        try:
            from .backend_qdrant_local import QdrantLocalBackend
            b = QdrantLocalBackend(embedding_model=embedding_model, collection=collection)
            return b, "qdrant-local"
        except ImportError:
            _warn_install("qdrant-local", "qdrant-client sentence-transformers")
        except Exception as e:
            print(f"⚠️  Backend qdrant-local échoué ({e}) → fallback local JSON")

    # Fallback
    from .backend_local import LocalBackend
    return LocalBackend(), "local"


def _warn_install(backend: str, packages: str) -> None:
    print(f"❌ Backend {backend} : dépendances manquantes")
    print(f"   → pip install {packages}")
    print(f"   → Fallback backend local JSON (fonctionnel, recherche par mots-clés)")


def _warn_connection(backend: str, url: str, err: Exception) -> None:
    print(f"⚠️  Backend {backend} inaccessible ({err})")
    print(f"   → URL tentée : {url}")
    print(f"   → Vérifier Grimoire_OLLAMA_URL / Grimoire_QDRANT_URL ou lancer le service")
    print(f"   → Fallback backend local JSON (fonctionnel, recherche par mots-clés)")
