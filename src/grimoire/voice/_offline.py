"""Garde « pas de téléchargement » autour du chargement d'un modèle Hugging Face.

faster-whisper prend ``local_files_only`` ; pocket-tts non, il appelle
``hf_hub_download`` directement. ``huggingface_hub`` relit
``constants.HF_HUB_OFFLINE`` à chaque requête (1.x) : le basculer le temps du
chargement suffit, le cache local reste servi, toute requête réseau échoue.
La variable d'environnement couvre un ``huggingface_hub`` pas encore importé.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

_ENV = "HF_HUB_OFFLINE"


@contextmanager
def hub_offline(enabled: bool = True) -> Iterator[None]:
    if not enabled:
        yield
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
    try:
        yield
    finally:
        if hub is not None:
            hub.HF_HUB_OFFLINE = previous_flag
        if previous_env is None:
            os.environ.pop(_ENV, None)
        else:
            os.environ[_ENV] = previous_env
