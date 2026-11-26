"""Chauffe de la Flotte du cockpit : la première ouverture est servie à chaud.

``fleet/live`` à froid relit les journaux de session de l'hôte (de l'ordre de 6 s sur le poste de la Forge) ; chaud,
il se sert en quelques millisecondes depuis les caches (lecture mémorisée par empreinte, parcours incrémental des
journaux). La chauffe passe par la même route que la page, donc remplit les mêmes caches et rien d'autre : aucune
écriture sur le disque. Elle est appelée par le fil de chauffe du cockpit (:func:`grimoire.cli.cockpit_http.warm_sheet`),
après les étapes de la fiche, à priorité réduite, et ne s'y exécute qu'une fois à la fois.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable
from pathlib import Path

__all__ = ["FLEET_BUDGET_S", "warm_fleet"]

#: Temps au-delà duquel la chauffe ne commence plus de vue : la borne s'applique entre deux vues (une vue en cours
#: se termine), comme l'annulation.
FLEET_BUDGET_S = 60.0

_FLEET_PATH = "/api/workspace/fleet/live"
#: Ce que la page demande à l'ouverture : le projet courant, puis la vue globale (``project=*``).
_FLEET_QUERIES: tuple[dict[str, list[str]], ...] = ({}, {"project": ["*"]})

_ONE_AT_A_TIME = threading.Lock()


def warm_fleet(
    root: Path,
    stop: threading.Event | None = None,
    *,
    budget_s: float = FLEET_BUDGET_S,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Chauffe la Flotte de *root* puis la vue globale ; rend le nombre de vues chauffées.

    Rien si une autre chauffe de la Flotte est en cours (pas de double chauffe), si *stop* est levé, ou une fois
    *budget_s* écoulé. Un échec d'une vue ne coûte que cette vue.
    """
    if not _ONE_AT_A_TIME.acquire(blocking=False):
        return 0
    try:
        from grimoire.tools import workspace_routes

        deadline = clock() + budget_s
        warmed = 0
        for query in _FLEET_QUERIES:
            if (stop is not None and stop.is_set()) or clock() >= deadline:
                break
            with contextlib.suppress(Exception):
                workspace_routes.workspace_get(root, _FLEET_PATH, query)
                warmed += 1
        return warmed
    finally:
        _ONE_AT_A_TIME.release()
