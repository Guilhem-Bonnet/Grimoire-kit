"""Supersession des souvenirs : une décision remplacée n'est plus servie comme un fait.

Un souvenir est *remplacé* quand ``metadata["superseded_by"]`` nomme l'id de
son remplaçant (même clé que ``retention-registry.yaml``, que le standard
contrôle déjà dans ``retention.superseded_without_replacement``). Ce module
porte le prédicat, le filtre appliqué par défaut à toutes les lectures de
:class:`~grimoire.memory.manager.MemoryManager`, et le banc qui mesure ce que
la recherche sert réellement.

Aucun chiffre n'est inventé : un taux sans résultat servi vaut ``None``, et le
nombre de jetons est une estimation déclarée (``chars/4``), pas un décompte
de tokenizer.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from grimoire.memory.backends.base import MemoryEntry
    from grimoire.memory.manager import MemoryManager

__all__ = [
    "SUPERSEDED_KEY",
    "bench_supersession",
    "drop_superseded",
    "has_supersession_mark",
    "is_superseded",
    "search_live",
    "supersession_state",
]

SUPERSEDED_KEY = "superseded_by"
ESTIMATE_BASIS = "chars/4"
_MAX_REFETCH_ROUNDS = 4


RecallFn = Callable[[str], "MemoryEntry | None"]

LIVE = "live"
SUPERSEDED = "superseded"
ORPHAN = "orphan"


def _mark(entry: MemoryEntry) -> str:
    return str((entry.metadata or {}).get(SUPERSEDED_KEY) or "").strip()


def has_supersession_mark(entry: MemoryEntry) -> bool:
    """Vrai quand l'entrée porte une marque non vide (valeur vide ou blanche : non).

    La marque seule ne dit pas que l'entrée est remplacée : le remplaçant peut
    avoir été supprimé, ou la marque posée vers un id qui n'a jamais existé.
    Voir :func:`supersession_state`.
    """
    return bool(_mark(entry))


def supersession_state(entry: MemoryEntry, recall: RecallFn) -> str:
    """``live``, ``superseded`` ou ``orphan``, d'après l'état réel des remplaçants.

    On suit la chaîne ``superseded_by`` à partir de l'entrée, avec un ensemble
    de visités :

    - sans marque : ``live`` ;
    - la chaîne atteint une entrée *servie* autre que l'entrée de départ (sans
      marque, marque vers un id introuvable, ou membre d'un cycle) :
      ``superseded`` — un remplaçant existe et se lit ;
    - la marque de l'entrée de départ pointe vers rien, ou la chaîne revient
      sur elle (cycle) : ``orphan`` — la marque ne désigne aucun remplaçant
      lisible, l'entrée reste servie plutôt que de disparaître sans
      remplaçant.
    """
    if not has_supersession_mark(entry):
        return LIVE
    visited = {entry.id}
    current = entry
    while True:
        target_id = _mark(current)
        if not target_id:
            return SUPERSEDED if current.id != entry.id else LIVE
        if target_id in visited:
            return ORPHAN if target_id == entry.id else SUPERSEDED
        target = recall(target_id)
        if target is None:
            return SUPERSEDED if current.id != entry.id else ORPHAN
        visited.add(target_id)
        current = target


def is_superseded(entry: MemoryEntry, recall: RecallFn) -> bool:
    """Vrai quand un remplaçant existant et lisible prend la place de l'entrée."""
    return supersession_state(entry, recall) == SUPERSEDED


def drop_superseded(entries: Iterable[MemoryEntry], recall: RecallFn) -> list[MemoryEntry]:
    """Les entrées non remplacées, dans l'ordre d'origine."""
    return [entry for entry in entries if not is_superseded(entry, recall)]


def _memoized(recall: RecallFn) -> RecallFn:
    cache: dict[str, MemoryEntry | None] = {}

    def cached(entry_id: str) -> MemoryEntry | None:
        if entry_id not in cache:
            cache[entry_id] = recall(entry_id)
        return cache[entry_id]

    return cached


def search_live(
    fetch: Callable[[int], list[MemoryEntry]], limit: int, recall: RecallFn,
) -> list[MemoryEntry]:
    """Remplit ``limit`` avec des entrées non remplacées.

    Filtrer après un ``limit`` fixe renverrait moins de résultats que demandé
    quand les premiers rangs sont périmés : on redemande plus large (×2, au
    plus quatre fois) tant que le backend rend autant que demandé. Au-delà,
    on rend ce qu'on a — la limite est assumée, pas masquée.
    """
    lookup = _memoized(recall)
    size = max(limit, 1)
    live: list[MemoryEntry] = []
    for _ in range(_MAX_REFETCH_ROUNDS):
        raw = fetch(size)
        live = drop_superseded(raw, lookup)
        if len(live) >= limit or len(raw) < size:
            break
        size *= 2
    return live[:limit]


def _tokens(entry: MemoryEntry) -> int:
    return math.ceil(len(entry.text) / 4)


def _nearest_rank(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(math.ceil(percentile * len(ordered)), 1) - 1]


def bench_supersession(
    mgr: MemoryManager,
    queries: list[str],
    *,
    limit: int = 5,
    include_superseded: bool = False,
) -> dict[str, Any]:
    """Mesure ce que la lecture mémoire sert pour ``queries``.

    - ``stale_served_as_fact_rate`` : part des résultats servis qui sont des
      souvenirs remplacés, présentés comme des faits. ``None`` si rien n'a
      été servi (un taux sur zéro résultat n'est pas zéro).
    - ``orphan_marks_served`` : résultats servis qui portent une marque
      ``superseded_by`` sans remplaçant lisible (supprimé, jamais existé,
      cycle) : ils sont servis comme des faits, mais la marque est à
      réparer. ``None`` si rien n'a été servi.
    - ``recall_tokens_p95`` : rang le plus proche, sur les requêtes qui ont
      servi au moins un résultat, du nombre de jetons estimés (``chars/4``).
    """
    served = 0
    stale = 0
    orphans = 0
    per_query_tokens: list[int] = []
    for query in queries:
        entries = mgr.hybrid_search(query, limit=limit, include_superseded=include_superseded)
        if not entries:
            continue
        served += len(entries)
        states = [supersession_state(entry, mgr.recall) for entry in entries]
        stale += states.count(SUPERSEDED)
        orphans += states.count(ORPHAN)
        per_query_tokens.append(sum(_tokens(entry) for entry in entries))
    return {
        "queries": len(queries),
        "limit": limit,
        "include_superseded": include_superseded,
        "results_served": served,
        "stale_served": stale,
        "stale_served_as_fact_rate": (stale / served) if served else None,
        "orphan_marks_served": orphans if served else None,
        "recall_tokens_p95": _nearest_rank(per_query_tokens, 0.95),
        "tokens_basis": ESTIMATE_BASIS,
    }
