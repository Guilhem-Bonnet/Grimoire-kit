"""Retrait des séquences ANSI d'une sortie de programme.

``ollama run`` écrit des séquences ANSI même quand sa sortie n'est pas un
terminal : ``…une Asser\\x1b[5D\\x1b[KAssertionError…``. Laissées en place,
elles polluent le texte rendu à l'agent (le ``\\x1b[5D`` déplace le curseur
dans un terminal, mais reste du bruit dans un fichier) et peuvent faire
échouer une recherche littérale d'un message d'erreur.

Le retrait est volontairement **littéral et borné aux séquences de contrôle** :
CSI (``ESC [ … finale``, couleurs et déplacements de curseur) et OSC
(``ESC ] … BEL`` ou ``ESC \\``, titres de fenêtre). Aucun autre caractère du
texte n'est réécrit — un message d'erreur doit rester cité mot pour mot.
"""

from __future__ import annotations

import re

__all__ = ["strip_ansi"]

#: CSI : ``ESC [`` suivi de paramètres, d'intermédiaires puis d'un octet final ``@``–``~``.
_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
#: OSC : ``ESC ]`` jusqu'au ``BEL`` ou au ``ST`` (``ESC \\``). Une OSC non terminée reste en place.
_OSC = re.compile(r"\x1b\][^\x07]*(?:\x07|\x1b\\)")


def strip_ansi(text: str) -> str:
    """Rend *text* sans ses séquences CSI et OSC ; le reste est inchangé."""
    return _OSC.sub("", _CSI.sub("", text))
