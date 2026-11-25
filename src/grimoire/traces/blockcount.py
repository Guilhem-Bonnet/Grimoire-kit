"""Comptage d'occurrences d'une séquence d'octets dans un fichier, par blocs (#1277)."""

from __future__ import annotations

from pathlib import Path

#: Taille des blocs lus par :func:`count_occurrences` : borne la mémoire, pas le coût (linéaire).
COUNT_BLOCK_BYTES = 1 << 20


def count_occurrences(path: Path, needle: bytes) -> int:
    """Occurrences non chevauchantes de *needle* dans *path*, lues par blocs (mémoire bornée).

    Identique à ``path.read_bytes().count(needle)``. Les ``len(needle) - 1`` derniers
    octets d'un bloc sont reportés sur le suivant pour compter une occurrence à
    cheval ; ce report est trop court pour contenir une occurrence entière, donc
    rien n'est compté deux fois.
    """
    keep = len(needle) - 1
    total = 0
    tail = b""
    with open(path, "rb") as fh:
        while block := fh.read(COUNT_BLOCK_BYTES):
            data = tail + block
            total += data.count(needle)
            tail = data[-keep:] if keep else b""
    return total
