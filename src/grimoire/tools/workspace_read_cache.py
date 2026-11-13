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
  (vivacité d'une session, « depuis ») reste vrai. Les lectures dont la source n'est pas dans l'empreinte du
  pouls — journaux de session et sonde d'hôte de ``fleet/live``, sonde et horloge de ``capacity``, board et
  gates de ``evidence`` — sont bornées plus court, à :data:`FRESH_MAX_AGE_SECONDS`
  (:func:`max_age_seconds`) : une session morte ne reste pas « vivante » quinze secondes. Jamais de délai
  minimal : une page qui relit après un changement doit le voir, elle ne redemandera pas ;
- **un seul calcul en vol par clé** : deux onglets qui demandent la même lecture au même moment attendent le
  même calcul, et reçoivent la même réponse — ou la même erreur. Les rafales, elles, sont regroupées par la
  page (``web/workspace/live.js``).

Une erreur n'est jamais gardée : l'appel suivant recalcule.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "FRESH_MAX_AGE_SECONDS",
    "MAX_AGE_SECONDS",
    "MAX_ENTRIES",
    "cacheable_read",
    "cached_read",
    "clear",
    "max_age_seconds",
]

#: Âge au-delà duquel une réponse est recalculée, empreinte immobile ou non.
MAX_AGE_SECONDS = 15.0
#: Âge des lectures qui suivent l'instant et l'hôte, pas seulement le disque : ce que lisent ``fleet/live``
#: (journaux de session de l'hôte, sonde des agents vivants), ``capacity`` (sonde et horloge) et ``evidence``
#: (board et gates) n'est pas dans l'empreinte du pouls. Sans borne courte, une session morte resterait
#: « vivante » jusqu'à :data:`MAX_AGE_SECONDS`.
FRESH_MAX_AGE_SECONDS = 3.0
#: Suffixes des routes ci-dessus : la route complète porte le préfixe de l'API.
FRESH_ROUTE_SUFFIXES = ("/fleet/live", "/capacity", "/evidence")
#: Clés gardées au plus (route, projet, paramètres) ; la plus ancienne sort.
MAX_ENTRIES = 64


def max_age_seconds(route: str) -> float:
    """L'âge maximal d'une réponse de *route* : court si elle suit l'instant, :data:`MAX_AGE_SECONDS` sinon."""
    if any(route.endswith(suffix) for suffix in FRESH_ROUTE_SUFFIXES):
        return FRESH_MAX_AGE_SECONDS
    return MAX_AGE_SECONDS


def _first(query: Mapping[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def cacheable_read(route: str, query: Mapping[str, list[str]], *, live_routes: frozenset[str]) -> bool:
    """Vrai si cette lecture peut être gardée (:func:`cached_read`), pour la route et la requête données.

    Le portefeuille (``scope=portfolio``) agrège des projets que ce serveur n'écrit pas — l'empreinte est celle
    du projet servi, pas la leur : il n'est jamais gardé. Un ``project=`` est, lui, résolu par l'hôte *avant* la
    route (``_CockpitHandler.do_GET``) et la lecture porte alors sur cette racine, dont l'empreinte couvre les
    fichiers : il n'est donc pas « étranger ». La seule exception est ``fleet/live`` avec ``project=*``, qui
    agrège tout le registre sous l'empreinte d'un seul projet — même piège que le portefeuille. Une sonde
    explicitement demandée (``probe=1``) force un calcul, sinon « Réessayer » resservirait la sonde qu'il devait
    remplacer.
    """
    return (
        route in live_routes
        and _first(query, "scope") != "portfolio"
        and _first(query, "probe") in (None, "", "0", "false")
        and not (route.endswith("/fleet/live") and _first(query, "project") == "*")
    )


@dataclass(slots=True)
class _Entry:
    fingerprint: str
    at: float
    value: Any


@dataclass(slots=True)
class _Flight:
    """Un calcul en cours pour une clé : les appelants simultanés attendent sa fin.

    La réponse — ou l'erreur — est publiée à tous ceux qui ont rejoint le vol, puis le vol disparaît :
    une erreur n'est jamais gardée, l'appel suivant recalcule.
    """

    done: threading.Event = field(default_factory=threading.Event)
    value: Any = None
    error: BaseException | None = None


_lock = threading.Lock()
_entries: OrderedDict[tuple[str, ...], _Entry] = OrderedDict()
_flights: dict[tuple[str, ...], _Flight] = {}
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


def _fresh(entry: _Entry | None, fingerprint: str, now: float, max_age: float) -> bool:
    return entry is not None and entry.fingerprint == fingerprint and now - entry.at < max_age


def _compute_and_store(
    key: tuple[str, ...],
    compute: Callable[[], Any],
    fingerprint: Callable[[], str],
    max_age: float,
    clock: Callable[[], float],
) -> Any:
    """Le calcul du vol : sert l'entrée fraîche s'il y en a une, sinon calcule puis la garde."""
    mark = fingerprint()
    with _lock:
        entry = _entries.get(key)
        if _fresh(entry, mark, clock(), max_age):
            _entries.move_to_end(key)
            return entry.value  # type: ignore[union-attr]
        started = _generation
    value = compute()
    with _lock:
        if started != _generation:
            return value  # une écriture a vidé le cache pendant le calcul : cette réponse peut la précéder
        _entries[key] = _Entry(mark, clock(), value)
        _entries.move_to_end(key)
        while len(_entries) > MAX_ENTRIES:
            _entries.popitem(last=False)  # les vols s'effacent seuls : ne pas casser celui d'un calcul en cours
    return value


def cached_read(
    route: str,
    project_root: Path,
    query: dict[str, list[str]],
    compute: Callable[[], Any],
    fingerprint: Callable[[], str],
    *,
    clock: Callable[[], float] | None = None,
) -> Any:
    """La réponse de *route* pour *project_root* et *query* : gardée si l'empreinte n'a pas bougé, sinon *compute*.

    L'âge maximal dépend de la route (:func:`max_age_seconds`) : :data:`FRESH_MAX_AGE_SECONDS` pour ce qui suit
    l'instant et l'hôte, :data:`MAX_AGE_SECONDS` pour le reste. *clock* est injectable — les tests ne
    dépendent jamais de l'horloge réelle ; par défaut ``time.monotonic``, résolu à l'appel.

    Un seul vol par clé : les appelants arrivés pendant le calcul attendent sa réponse — ou son erreur, la même
    pour tous — au lieu de recalculer chacun de leur côté. Le vol n'est jamais une entrée du cache : une erreur
    n'est pas gardée, le prochain appel rouvre un calcul.
    """
    key = (route, str(project_root), *(f"{k}={','.join(v)}" for k, v in sorted(query.items())))
    with _lock:
        flight = _flights.get(key)
        if flight is None:
            flight = _Flight()
            _flights[key] = flight
            leader = True
        else:
            leader = False
    if not leader:
        flight.done.wait()  # le vol publie sa réponse ou son erreur, puis s'efface
        if flight.error is not None:
            raise flight.error
        return flight.value
    try:
        value = _compute_and_store(
            key, compute, fingerprint, max_age_seconds(route), clock if clock is not None else time.monotonic
        )
        flight.value = value
        return value
    except BaseException as exc:
        flight.error = exc
        raise
    finally:
        with _lock:
            if _flights.get(key) is flight:
                _flights.pop(key, None)
        flight.done.set()
