"""Ce qu'on tire d'un fichier (hash, YAML lu), gardé tant que le fichier n'a pas pu changer.

Mesuré le 2026-11-13 sur un vrai poste (quatre projets) : ``GET /api/workspace/attention`` prenait
4 à 7 s, presque tout en relectures de fichiers immobiles — 290 fichiers du kit hachés à chaque
requête pour l'alignement, et le même ``gates.yaml`` reparsé (avec la construction d'un objet
ruamel, ~25 ms à elle seule) pour chacune des 75 tâches affichées.

La règle de sûreté : un résultat n'est gardé avec (date, taille, inode) que si la date avait déjà
plus de :data:`COARSE_CLOCK_NS` au moment de la lecture. L'horloge du système de fichiers avance par
pas de 2 s au pire (FAT), ~16 ms sous NTFS : passé ce délai, toute réécriture change forcément la
date, donc la signature. Un fichier tout juste écrit est relu à chaque fois tant qu'il est récent.

Une erreur n'est jamais gardée : ``compute`` qui lève laisse l'appel suivant recalculer.
"""

from __future__ import annotations

import contextlib
import contextvars
import copy
import os
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

__all__ = ["COARSE_CLOCK_NS", "MAX_ENTRIES", "clear", "derived", "view_copy", "view_derived", "view_scope"]

#: Délai au-delà duquel une réécriture change forcément la date d'un fichier.
COARSE_CLOCK_NS = 2_000_000_000
#: Résultats gardés au plus ; au-delà, tout est oublié (et recalculé à la demande).
MAX_ENTRIES = 50_000

_lock = threading.Lock()
_entries: dict[tuple[str, str], tuple[int, int, int, object]] = {}


def clear() -> None:
    """Oublie tout (tests)."""
    with _lock:
        _entries.clear()
        _view_entries.clear()


def derived[T](
    path: Path,
    kind: str,
    compute: Callable[[bytes], T],
    *,
    info: os.stat_result | None = None,
) -> T:
    """``compute(contenu de path)``, resservi tant que (date, taille, inode) de *path* tient.

    *kind* distingue deux lectures d'un même fichier (un hash, un YAML). *info* évite un second
    ``stat`` quand l'appelant l'a déjà. ``OSError`` (absent, illisible) remonte à l'appelant.
    """
    stat = info if info is not None else path.stat()
    sig = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
    key = (kind, str(path))
    with _lock:
        known = _entries.get(key)
    if known is not None and known[:3] == sig:
        return known[3]  # type: ignore[return-value]
    value = compute(path.read_bytes())
    # Gardé seulement si la date était déjà vieille : une réécriture dans le même pas d'horloge
    # garderait date et taille, et le résultat gardé mentirait.
    if time.time_ns() - stat.st_mtime_ns > COARSE_CLOCK_NS:
        with _lock:
            if len(_entries) >= MAX_ENTRIES:
                _entries.clear()
            _entries[key] = (*sig, value)
    return value


# ── Lecture d'affichage ──────────────────────────────────────────────────────
#
# ``derived`` sert à qui accepte la mémoire partout. Les lecteurs qui servent aussi des décisions (preuves, verdicts :
# le gate d'une transition) ne doivent jamais la consulter d'eux-mêmes : ils appellent :func:`view_derived`, qui ne
# garde et ne ressert **que dans** un :func:`view_scope`, ouvert par le seul code de lecture du cockpit.

_VIEW_MAX_ENTRIES = 64
_view_active: contextvars.ContextVar[bool] = contextvars.ContextVar("grimoire_file_memo_view", default=False)
_view_entries: dict[tuple[str, str], tuple[tuple[int, int, int, int], object]] = {}
#: Sous Windows ``st_ctime_ns`` est la date de création : la signature ne verrait pas une réécriture en place.
_WINDOWS = os.name == "nt"


@contextlib.contextmanager
def view_scope() -> Iterator[None]:
    """Dans ce bloc, :func:`view_derived` garde et ressert ; hors du bloc, il relit toujours.

    Le scope vit dans une ``ContextVar`` du fil appelant : à ouvrir par un ``with`` simple, autour d'une lecture qui
    se termine. Ne jamais l'ouvrir dans un générateur : suspendu à l'intérieur du ``with``, il laisserait le scope
    actif sur le fil pour le code qui reprend la main, et une lecture de décision pourrait alors être servie de la
    mémoire.
    """
    token = _view_active.set(True)
    try:
        yield
    finally:
        _view_active.reset(token)


def view_derived[T](path: Path, kind: str, compute: Callable[[bytes], T]) -> T:
    """``compute(contenu de path)``, resservi dans un :func:`view_scope` tant que (date, ctime, taille, inode) tiennent.

    Rien n'est gardé si la date de modification ou de changement a moins de :data:`COARSE_CLOCK_NS`, ni sous Windows,
    ni une erreur. Hors d'un ``view_scope``, c'est une lecture simple. ``OSError`` remonte à l'appelant.
    """
    if _WINDOWS or not _view_active.get():
        return compute(path.read_bytes())
    stat = path.stat()
    sig = (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino)
    key = (kind, str(path))
    with _lock:
        known = _view_entries.get(key)
    if known is not None and known[0] == sig:
        return known[1]  # type: ignore[return-value]
    value = compute(path.read_bytes())
    if time.time_ns() - max(stat.st_mtime_ns, stat.st_ctime_ns) > COARSE_CLOCK_NS:
        with _lock:
            if len(_view_entries) >= _VIEW_MAX_ENTRIES:
                _view_entries.clear()
            _view_entries[key] = (sig, value)
    return value


def view_copy[T](value: T) -> T:
    """*value*, copié en profondeur quand il peut venir de la mémoire d'un :func:`view_scope`, tel quel sinon.

    ``view_derived`` ressert le même objet à chaque appel : un appelant qui le modifierait polluerait la mémoire des
    scopes suivants. Les lecteurs en rendent donc une copie, et la font **après** leur filtre (une poignée d'éléments,
    pas tout le fichier). Hors scope, la valeur vient d'être parsée et n'est à personne d'autre : pas de copie.
    """
    if _WINDOWS or not _view_active.get():
        return value
    return copy.deepcopy(value)
