"""Garde « pas de téléchargement » autour du chargement d'un modèle Hugging Face.

faster-whisper prend ``local_files_only`` ; pocket-tts non, il appelle
``hf_hub_download`` directement. ``huggingface_hub`` relit
``constants.HF_HUB_OFFLINE`` à chaque requête (1.x) : le basculer le temps du
chargement suffit, le cache local reste servi, toute requête réseau échoue.
La variable d'environnement couvre un ``huggingface_hub`` pas encore importé.

L'état est celui du processus : un verrou et un compteur de références. Le premier
chargement mémorise l'état d'origine et pose la garde, le dernier à sortir le
rétablit — deux chargements qui se chevauchent ne figent donc jamais le processus
hors ligne. Seule la durée d'un chargement reste hors ligne pour les autres usages.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

_ENV = "HF_HUB_OFFLINE"
_lock = threading.Lock()
_depth = 0
_saved: tuple[str | None, Any, Any] | None = None  # (variable d'environnement, module constants, drapeau)

#: Exceptions de huggingface_hub qui disent « pas dans le cache local / pas de réseau ».
_CACHE_MISS = frozenset(
    {
        "LocalEntryNotFoundError",
        "OfflineModeIsEnabled",
        "EntryNotFoundError",
        "RepositoryNotFoundError",
        "RevisionNotFoundError",
        "FileNotFoundError",
    }
)


def is_cache_miss(exc: BaseException) -> bool:
    """Vrai quand ``exc`` (ou sa cause) est un fichier absent du cache local, pas une autre panne."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if any(cls.__name__ in _CACHE_MISS for cls in type(current).__mro__):
            return True
        current = current.__cause__ or current.__context__
    return False


def _enter() -> None:
    global _depth, _saved
    with _lock:
        _depth += 1
        if _depth > 1:
            return
        previous_env = os.environ.get(_ENV)
        os.environ[_ENV] = "1"
        hub: Any = None
        try:
            from huggingface_hub import constants as hub
        except ImportError:  # pas de huggingface_hub : rien ne téléchargera par lui
            hub = None
        previous_flag = getattr(hub, "HF_HUB_OFFLINE", None)
        if hub is not None:
            hub.HF_HUB_OFFLINE = True
        _saved = (previous_env, hub, previous_flag)


def _exit() -> None:
    global _depth, _saved
    with _lock:
        _depth -= 1
        if _depth > 0 or _saved is None:
            return
        previous_env, hub, previous_flag = _saved
        _saved = None
        if hub is not None:
            hub.HF_HUB_OFFLINE = previous_flag
        if previous_env is None:
            os.environ.pop(_ENV, None)
        else:
            os.environ[_ENV] = previous_env


@contextmanager
def hub_offline(enabled: bool = True) -> Iterator[None]:
    if not enabled:
        yield
        return
    _enter()
    try:
        yield
    finally:
        _exit()
