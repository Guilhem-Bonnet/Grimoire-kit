"""Cache process du statut mémoire, par projet — évite de sonder à chaque lecture.

``MemoryManager.health_check()`` sonde jusqu'à trois services réseau (backend
vectoriel, Redis, Neo4j). Sur un projet dont ces services tournent, la sonde
est rapide ; sur un projet dont un ou plusieurs sont éteints ou absents (le
cas courant), chaque sonde ratée ne coûtait rien de moins que son propre
délai — mesuré à 0,88s cumulés sur trois sondes séquentielles avant ce module
(voir l'issue de perf du cockpit).

Ce module ne sonde jamais lui-même : il mémorise le dernier statut obtenu par
un appelant qui a explicitement sondé (``probe=True`` côté
:func:`grimoire.tools.memory_link.memory_link_status`), avec un TTL court, et
l'exhibe aux lectures suivantes en le marquant ``stale`` une fois le TTL
dépassé. Le mode par défaut (``probe=False``) ne consulte jamais le réseau —
c'est ``memory_link_status`` qui décide.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["PROBE_TTL_SECONDS", "CacheEntry", "get", "invalidate", "set_status"]

#: Durée pendant laquelle un statut sondé est considéré frais.
PROBE_TTL_SECONDS = 25.0

_lock = threading.Lock()
_cache: dict[str, CacheEntry] = {}


@dataclass(frozen=True)
class CacheEntry:
    """Un statut sondé, horodaté."""

    status: dict[str, Any]
    probed_at: float  # time.time() epoch seconds

    def is_fresh(self, ttl: float = PROBE_TTL_SECONDS) -> bool:
        return (time.time() - self.probed_at) < ttl


def _key(project_root: Path) -> str:
    return str(project_root.resolve())


def get(project_root: Path) -> CacheEntry | None:
    """Le dernier statut sondé pour *project_root*, ou ``None`` si jamais sondé
    (ou invalidé depuis)."""
    with _lock:
        return _cache.get(_key(project_root))


def set_status(project_root: Path, status: dict[str, Any]) -> CacheEntry:
    """Mémorise *status* comme le dernier statut sondé pour *project_root*."""
    entry = CacheEntry(status=dict(status), probed_at=time.time())
    with _lock:
        _cache[_key(project_root)] = entry
    return entry


def invalidate(project_root: Path) -> None:
    """Oublie le statut caché — la prochaine lecture rapide n'affiche plus une
    donnée potentiellement obsolète ; elle attend une nouvelle sonde explicite."""
    with _lock:
        _cache.pop(_key(project_root), None)
