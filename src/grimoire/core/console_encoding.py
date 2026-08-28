"""Sortie console lisible même quand la console ne parle pas UTF-8.

Le kit imprime des filets (``─``), des flèches (``→``) et des marqueurs d'état
dans presque toutes ses sorties. Sur une console Windows par défaut — cp1252 —
`print` lève ``UnicodeEncodeError`` et la commande meurt, y compris sur une
simple lecture.

Ce n'est pas un défaut de l'ère shell : Rich échoue de la même façon, et
suggère lui-même ``PYTHONIOENCODING=utf-8``. Le chemin supporté, la CLI
``grimoire``, avait donc exactement le même angle mort que les outils gelés de
``framework/tools/`` — trouvé par la matrice Windows ajoutée en #191.

Corriger à l'entrée plutôt qu'à chaque site d'impression : les points d'entrée
sont trois, les sites d'impression des milliers. ``errors="replace"`` garantit
que la dégradation reste une dégradation — un caractère non représentable
devient ``?``, il n'interrompt jamais la commande.
"""

from __future__ import annotations

import contextlib
import os
import sys
from typing import IO, Any


def _reconfigure(stream: IO[Any] | None) -> None:
    """Passe *stream* en UTF-8 tolérant, s'il sait le faire."""
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    # Flux détaché, redirigé vers un objet exotique, ou déjà fermé : la sortie
    # reste ce qu'elle était. Échouer ici transformerait un confort d'affichage
    # en panne de démarrage.
    with contextlib.suppress(OSError, ValueError):
        reconfigure(encoding="utf-8", errors="replace")


def enable_utf8_output() -> None:
    """Rend ``stdout``/``stderr`` sûrs pour les caractères que le kit imprime.

    Appelée par chaque point d'entrée. Idempotente, et sans effet observable
    sur une console déjà en UTF-8.

    ``PYTHONIOENCODING`` est posé pour les sous-processus : les outils de
    ``framework/`` s'appellent entre eux par chemin de fichier, et la variable
    est le seul canal qui traverse ce genre d'invocation.
    """
    _reconfigure(sys.stdout)
    _reconfigure(sys.stderr)
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
