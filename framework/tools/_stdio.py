"""Sortie console en UTF-8, quelle que soit la console.

Quarante-cinq outils de ce dossier impriment des filets de tableau, des flèches
et des coches — des caractères qu'une console Windows par défaut, en cp1252, ne
sait pas encoder. Chacun mourait sur une simple commande de lecture. Le
correctif est de classe : un appel en tête de ``main()``, et le flux accepte
tout ce que l'outil écrit, en remplaçant ce que la console ne peut pas montrer
plutôt qu'en levant.

Poser ``PYTHONIOENCODING`` dans la CI aurait rendu la matrice verte en laissant
le bug intact chez l'utilisateur.
"""

from __future__ import annotations

import sys
from typing import IO


def force_utf8(*streams: IO[str]) -> None:
    """Reconfigure ``stdout`` et ``stderr`` (ou *streams*) en UTF-8, erreurs remplacées.

    Un flux sans ``reconfigure`` — un ``StringIO`` capturé par un test, un tube
    déjà en binaire — est laissé tel quel : il n'a pas d'encodage à corriger.
    """
    for stream in streams or (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            # Flux fermé ou non reconfigurable : rien à corriger, rien à casser.
            continue
