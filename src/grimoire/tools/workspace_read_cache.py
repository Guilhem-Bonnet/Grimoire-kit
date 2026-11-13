"""Le cache des lectures lourdes du cockpit (P79) : un calcul par changement, pas un par onglet ni par page.

Le 2026-10-27, huit workers actifs faisaient bouger l'empreinte du pouls plusieurs fois par seconde ; chaque
onglet ouvert relisait alors ``tasks``, ``attention`` et ``sessions``, et le serveur recalculait tout à chaque
fois. Seules, ces lectures coûtent ~0,6 s ; à plusieurs onglets, le serveur saturait (``attention`` 7,6 s, une
lecture lourde voisine 67 s au lieu de 0,35 s). Depuis GAO-perf-cockpit-002, l'ouverture de page et la navigation
passent par le même cache : c'est là que l'utilisateur sent la lenteur, pas seulement lors d'un rechargement du
direct.

Deux règles, sans changer ce que la page reçoit :

- **même empreinte, même réponse** : la réponse est gardée avec l'empreinte du pouls
  (:func:`grimoire.tools.workspace_pulse.fingerprint_of`, des ``stat`` seulement) ; tant qu'elle ne bouge pas,
  elle est resservie. Une réponse n'a jamais plus de :data:`MAX_AGE_SECONDS` : ce qui dépend de l'horloge
  (vivacité d'une session, « depuis ») reste vrai. Jamais de délai minimal : une page qui relit après un
  changement doit le voir, elle ne redemandera pas ;
- **un seul calcul en vol par clé** : deux onglets qui demandent la même lecture au même moment attendent le
  même calcul. Les rafales, elles, sont regroupées par la page (``web/workspace/live.js``).

Le cache est borné deux fois : par le nombre d'entrées (:data:`MAX_ENTRIES`) **et** par la taille totale des
réponses gardées (:data:`MAX_TOTAL_BYTES`). Le nombre seul ne suffisait pas : 64 réponses de board annoté
pèsent ~60 Mo mesurés (audit ``docs/audits/cockpit-memoire-2026-11.md``). Quand la taille dépasse la borne,
l'entrée la plus ancienne sort — même la réponse qui vient d'être calculée si elle est à elle seule plus
grosse que la borne : elle n'est alors simplement pas gardée.

Une erreur n'est jamais gardée : l'appel suivant recalcule.
"""

from __future__ import annotations

import sys
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "MAX_AGE_SECONDS",
    "MAX_ENTRIES",
    "MAX_TOTAL_BYTES",
    "cacheable_read",
    "cached_read",
    "clear",
    "stats",
]

#: Âge au-delà duquel une réponse est recalculée, empreinte immobile ou non.
MAX_AGE_SECONDS = 15.0
#: Clés gardées au plus (route, projet, paramètres) ; la plus ancienne sort.
MAX_ENTRIES = 64
#: Octets gardés au plus, toutes réponses confondues (estimation récursive : conteneurs, chaînes, scalaires).
#: 48 Mio laissent tenir le board annoté de quatre projets enrôlés (~1 Mo chacun) et bornent le reste.
MAX_TOTAL_BYTES = 48 * 1024 * 1024


def _first(query: Mapping[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def cacheable_read(route: str, query: Mapping[str, list[str]], *, live_routes: frozenset[str]) -> bool:
    """Vrai si cette lecture peut être gardée (:func:`cached_read`), pour la route et la requête données.

    Le portefeuille (``scope=portfolio``) agrège des projets que ce serveur n'écrit pas — l'empreinte est celle
    du projet servi, pas la leur : il n'est jamais gardé. Un ``project=`` est, lui, résolu par l'hôte *avant* la
    route (``_CockpitHandler.do_GET``) et la lecture porte alors sur cette racine, dont l'empreinte couvre les
    fichiers : il n'est donc pas « étranger ». Une sonde explicitement demandée (``probe=1``) force un calcul,
    sinon « Réessayer » resservirait la sonde qu'il devait remplacer.
    """
    return (
        route in live_routes
        and _first(query, "scope") != "portfolio"
        and _first(query, "probe") in (None, "", "0", "false")
    )


@dataclass(slots=True)
class _Entry:
    fingerprint: str
    at: float
    value: Any
    size: int = 0


def _size_of(value: Any, seen: set[int] | None = None) -> int:
    """Taille récursive approximative d'une réponse JSON-like, en octets.

    ``sys.getsizeof`` seul ne voit pas ce que retiennent les conteneurs : un board de 300 cartes ne pèse que
    quelques octets pour son ``dict`` racine. On descend donc dans les dictionnaires et séquences partagés par
    les réponses du cockpit, en coupant les cycles. Une même chaîne référencée deux fois est comptée deux fois :
    l'estimation reste une borne haute du poids des valeurs gardées, jamais une sous-estimation.
    """
    if seen is None:
        seen = set()
    if id(value) in seen:
        return 0
    seen.add(id(value))
    total = sys.getsizeof(value)
    if isinstance(value, dict):
        for key, item in value.items():
            total += _size_of(key, seen) + _size_of(item, seen)
    elif isinstance(value, list | tuple | set | frozenset):
        for item in value:
            total += _size_of(item, seen)
    return total


_lock = threading.Lock()
_entries: OrderedDict[tuple[str, ...], _Entry] = OrderedDict()
_flights: dict[tuple[str, ...], threading.Lock] = {}
#: Somme des :attr:`_Entry.size` gardés — maintenue sous :data:`_lock`.
_total_bytes = 0
#: Monte à chaque :func:`clear` : un calcul commencé avant (une lecture en vol pendant une écriture du cockpit) rend
#: sa réponse à son appelant mais ne la garde pas ; sinon l'état d'avant l'écriture serait resservi.
_generation = 0


def clear() -> None:
    """Oublie toutes les réponses gardées (tests, rechargement du serveur)."""
    global _generation, _total_bytes
    with _lock:
        _entries.clear()
        _flights.clear()
        _total_bytes = 0
        _generation += 1


def stats() -> dict[str, int]:
    """Ce que le cache retient : nombre d'entrées et octets estimés gardés (observabilité, tests)."""
    with _lock:
        return {"entries": len(_entries), "bytes": _total_bytes}


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
    global _total_bytes
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
        size = _size_of(value)
        with _lock:
            if started != _generation:
                return value  # une écriture a vidé le cache pendant le calcul : cette réponse peut la précéder
            previous = _entries.pop(key, None)
            if previous is not None:
                _total_bytes -= previous.size
            _entries[key] = _Entry(mark, time.monotonic(), value, size)
            _total_bytes += size
            _entries.move_to_end(key)
            # Deux bornes : le nombre d'entrées et les octets gardés. L'entrée la plus ancienne sort d'abord ;
            # une réponse seule plus grosse que la borne n'est jamais gardée (elle resservirait à chaque appel
            # en occupant à elle seule plus que le budget).
            while _entries and (len(_entries) > MAX_ENTRIES or _total_bytes > MAX_TOTAL_BYTES):
                old, old_entry = _entries.popitem(last=False)
                _total_bytes -= old_entry.size
                _flights.pop(old, None)
        return value
