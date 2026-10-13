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
    "is_superseded",
    "search_live",
]

SUPERSEDED_KEY = "superseded_by"
ESTIMATE_BASIS = "chars/4"
_MAX_REFETCH_ROUNDS = 4


def is_superseded(entry: MemoryEntry) -> bool:
    """Vrai quand l'entrée nomme un remplaçant (valeur vide ou blanche : non)."""
    return bool(str((entry.metadata or {}).get(SUPERSEDED_KEY) or "").strip())


def drop_superseded(entries: Iterable[MemoryEntry]) -> list[MemoryEntry]:
    """Les entrées non remplacées, dans l'ordre d'origine."""
    return [entry for entry in entries if not is_superseded(entry)]


def search_live(fetch: Callable[[int], list[MemoryEntry]], limit: int) -> list[MemoryEntry]:
    """Remplit ``limit`` avec des entrées non remplacées.

    Filtrer après un ``limit`` fixe renverrait moins de résultats que demandé
    quand les premiers rangs sont périmés : on redemande plus large (×2, au
    plus quatre fois) tant que le backend rend autant que demandé. Au-delà,
    on rend ce qu'on a — la limite est assumée, pas masquée.
    """
    size = max(limit, 1)
    live: list[MemoryEntry] = []
    for _ in range(_MAX_REFETCH_ROUNDS):
        raw = fetch(size)
        live = drop_superseded(raw)
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
    - ``recall_tokens_p95`` : rang le plus proche, sur les requêtes qui ont
      servi au moins un résultat, du nombre de jetons estimés (``chars/4``).
    """
    served = 0
    stale = 0
    per_query_tokens: list[int] = []
    for query in queries:
        entries = mgr.hybrid_search(query, limit=limit, include_superseded=include_superseded)
        if not entries:
            continue
        served += len(entries)
        stale += sum(1 for entry in entries if is_superseded(entry))
        per_query_tokens.append(sum(_tokens(entry) for entry in entries))
    return {
        "queries": len(queries),
        "limit": limit,
        "include_superseded": include_superseded,
        "results_served": served,
        "stale_served": stale,
        "stale_served_as_fact_rate": (stale / served) if served else None,
        "recall_tokens_p95": _nearest_rank(per_query_tokens, 0.95),
        "tokens_basis": ESTIMATE_BASIS,
    }
