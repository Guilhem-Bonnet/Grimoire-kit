"""Ouverture d'un fichier ORDINAIRE, sans suivre de lien et sans jamais bloquer.

Un chemin que lit le cockpit peut être un lien sortant (fuite d'un fichier étranger), une FIFO (la lecture
bloquerait le tour) ou un périphérique (flux infini). Ce module est l'unique point d'ouverture de ces
fichiers : ``lstat`` + ``S_ISREG``, ouverture ``O_NOFOLLOW | O_NONBLOCK``, puis ``fstat`` + ``S_ISREG`` sur le
descripteur ouvert (la course entre le contrôle et l'ouverture est ainsi fermée). Là où ``O_NOFOLLOW`` ou
``O_NONBLOCK`` n'existent pas (Windows), le drapeau vaut 0 : les deux contrôles ``S_ISREG`` restent.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import BinaryIO

_FLAGS = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)


def open_regular(path: Path) -> BinaryIO:
    """Le fichier ordinaire ``path`` ouvert en lecture binaire.

    ``FileNotFoundError`` s'il n'existe pas ; ``ValueError`` s'il n'est pas ordinaire (lien, FIFO, périphérique,
    dossier) ou ne s'ouvre pas ; rien n'est lu avant ces contrôles.
    """
    try:
        before = path.lstat()
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise ValueError(f"{path.name} illisible ({exc.__class__.__name__})") from exc
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"{path.name} n'est pas un fichier ordinaire : refusé")
    try:
        fd = os.open(path, _FLAGS)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise ValueError(f"{path.name} illisible ({exc.__class__.__name__})") from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f"{path.name} n'est pas un fichier ordinaire : refusé")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise
