"""Découper une ligne de commande comme le shell du système, sous Linux, macOS et Windows."""

from __future__ import annotations

import os
import shlex

__all__ = ["split_command"]


def split_command(text: str, *, posix: bool | None = None) -> tuple[str, ...]:
    """Découpe une ligne de commande comme le shell du système : sous Windows, l'antislash n'est pas un échappement.

    ``shlex`` en mode POSIX lit ``C:\\tools\\python.exe`` comme ``C:toolspython.exe`` (CI Windows du 2026-10-28) ;
    hors POSIX, il garde les antislashs mais laisse les guillemets autour d'un argument, retirés ici.
    """
    posix = os.name != "nt" if posix is None else posix
    parts = shlex.split(text, posix=posix)
    if not posix:
        parts = [p[1:-1] if len(p) >= 2 and p[0] == p[-1] == '"' else p for p in parts]
    return tuple(parts)
