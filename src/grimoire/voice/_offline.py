"""Distinguer « absent du cache local » des autres pannes de chargement.

Aucun état global n'est basculé ici : ``HF_HUB_OFFLINE`` est partagé par tout le
processus (le modèle d'embedding de la mémoire s'en sert aussi), et aucun verrou ne
l'isole des autres threads. L'absence du cache se prouve donc avant le chargement
(``local_files_only`` pour faster-whisper, contrôle du cache pour pocket-tts), et
se reconnaît après coup à l'exception précise de huggingface_hub.
"""

from __future__ import annotations

#: Seules exceptions de huggingface_hub qui disent « pas dans le cache local, et pas de réseau ».
_CACHE_MISS = frozenset({"LocalEntryNotFoundError", "OfflineModeIsEnabled"})


def is_cache_miss(exc: BaseException) -> bool:
    """Vrai quand ``exc`` est une absence du cache local, ou en a été levée explicitement (``raise … from``).

    ``__context__`` est ignoré : une autre panne survenue dans un ``except`` n'est pas une absence du cache.
    Ni ``FileNotFoundError`` (fichier local), ni ``RepositoryNotFoundError`` (dépôt inexistant, même en ligne).
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if any(cls.__name__ in _CACHE_MISS for cls in type(current).__mro__):
            return True
        current = current.__cause__
    return False
