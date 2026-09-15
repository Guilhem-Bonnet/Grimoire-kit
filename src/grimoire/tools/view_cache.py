"""Cache process générique, invalidé par mtime — pour les vues du cockpit qui
relisent tout un dossier à chaque appel.

``agents_view()`` (agents/skills/overrides) et ``proposals_view()``
(propositions) relisaient et re-résolvaient l'intégralité de leur surface à
chaque requête, sans jamais mettre en cache — mesuré à 1,58s pour la première
sur un projet réel. Rien n'y change entre deux clics rapprochés dans le
cockpit : la resignature (``stat`` sur chaque fichier, pas de lecture ni de
parsing) coûte un ordre de grandeur de moins que le recalcul complet, et
l'invalidation est naturelle — un fichier ajouté, retiré ou modifié change sa
signature, qui déclenche le recalcul au prochain appel.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

__all__ = ["cached", "invalidate", "path_signature"]

_lock = threading.Lock()
_cache: dict[str, tuple[Any, Any]] = {}


def path_signature(paths: Iterable[Path]) -> tuple[tuple[str, int, int], ...]:
    """Signature bon marché — ``(chemin, mtime_ns, taille)`` de chaque fichier
    sous *paths*. Un chemin absent contribue rien ; un fichier contribue une
    entrée directe, un dossier ses fichiers récursifs. Aucun contenu n'est lu."""
    entries: list[tuple[str, int, int]] = []
    for p in paths:
        if p.is_file():
            with contextlib.suppress(OSError):
                st = p.stat()
                entries.append((str(p), st.st_mtime_ns, st.st_size))
            continue
        if not p.is_dir():
            continue
        for dirpath, _dirnames, filenames in p.walk():
            for name in filenames:
                fp = dirpath / name
                try:
                    st = fp.stat()
                except OSError:
                    continue
                entries.append((str(fp), st.st_mtime_ns, st.st_size))
    entries.sort()
    return tuple(entries)


def cached[T](key: str, signature: object, compute: Callable[[], T]) -> T:
    """Rend ``compute()``, mis en cache tant que *signature* ne change pas."""
    with _lock:
        hit = _cache.get(key)
        if hit is not None and hit[0] == signature:
            return hit[1]  # type: ignore[no-any-return]
    result = compute()
    with _lock:
        _cache[key] = (signature, result)
    return result


def invalidate(key: str) -> None:
    """Oublie l'entrée *key* — le prochain appel recalcule inconditionnellement."""
    with _lock:
        _cache.pop(key, None)
