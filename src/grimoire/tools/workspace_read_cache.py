"""Le cache des lectures que le flux en direct relance (P79) : un calcul par changement, pas un par onglet.

Le 2026-10-27, huit workers actifs faisaient bouger l'empreinte du pouls plusieurs fois par seconde ; chaque
onglet ouvert relisait alors ``tasks``, ``attention`` et ``sessions``, et le serveur recalculait tout à chaque
fois. Seules, ces lectures coûtent ~0,6 s ; à plusieurs onglets, le serveur saturait (``attention`` 7,6 s, une
lecture lourde voisine 67 s au lieu de 0,35 s).

Deux règles, sans changer ce que la page reçoit :

- **même empreinte, même réponse** : la réponse est gardée avec l'empreinte du pouls
  (:func:`grimoire.tools.workspace_pulse.fingerprint_of`, des ``stat`` seulement) ; tant qu'elle ne bouge pas,
  elle est resservie. Une réponse n'a jamais plus de :data:`MAX_AGE_SECONDS` : ce qui dépend de l'horloge
  (vivacité d'une session, « depuis ») reste vrai. Jamais de délai minimal : une page qui relit après un
  changement doit le voir, elle ne redemandera pas ;
- **un seul calcul en vol par clé** : deux onglets qui demandent la même lecture au même moment attendent le
  même calcul. Les rafales, elles, sont regroupées par la page (``web/workspace/live.js``).

Une erreur n'est jamais gardée : l'appel suivant recalcule.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["MAX_AGE_SECONDS", "MAX_ENTRIES", "cached_read", "clear"]

#: Âge au-delà duquel une réponse est recalculée, empreinte immobile ou non.
MAX_AGE_SECONDS = 15.0
#: Clés gardées au plus (route, projet, paramètres) ; la plus ancienne sort.
MAX_ENTRIES = 64


@dataclass(slots=True)
class _Entry:
    fingerprint: str
    at: float
    value: Any


_lock = threading.Lock()
_entries: OrderedDict[tuple[str, ...], _Entry] = OrderedDict()
_flights: dict[tuple[str, ...], threading.Lock] = {}
#: Monte à chaque :func:`clear` : un calcul commencé avant (une lecture en vol pendant une écriture du cockpit) rend
#: sa réponse à son appelant mais ne la garde pas ; sinon l'état d'avant l'écriture serait resservi.
_generation = 0


def clear() -> None:
    """Oublie toutes les réponses gardées (tests, rechargement du serveur)."""
    global _generation
    with _lock:
        _entries.clear()
        _flights.clear()
        _generation += 1


def _fresh(entry: _Entry | None, fingerprint: str, now: float) -> bool:
    return entry is not None and entry.fingerprint == fingerprint and now - entry.at < MAX_AGE_SECONDS


def cached_read(
    route: str,
    project_root: Path,
    query: dict[str, list[str]],
    compute: Callable[[], Any],
    fingerprint: Callable[[], str],
) -> Any:
    """La réponse de *route* pour *project_root* et *query* : gardée si l'empreinte n'a pas bougé, sinon *compute*."""
    key = (route, str(project_root), *(f"{k}={','.join(v)}" for k, v in sorted(query.items())))
    with _lock:
        flight = _flights.setdefault(key, threading.Lock())
    with flight:  # un seul calcul par clé ; les autres attendent puis lisent sa réponse
        mark = fingerprint()
        with _lock:
            entry = _entries.get(key)
            if _fresh(entry, mark, time.monotonic()):
                _entries.move_to_end(key)
                return entry.value  # type: ignore[union-attr]
            started = _generation
        value = compute()
        with _lock:
            if started != _generation:
                return value  # une écriture a vidé le cache pendant le calcul : cette réponse peut la précéder
            _entries[key] = _Entry(mark, time.monotonic(), value)
            _entries.move_to_end(key)
            while len(_entries) > MAX_ENTRIES:
                old, _ = _entries.popitem(last=False)
                _flights.pop(old, None)
        return value
