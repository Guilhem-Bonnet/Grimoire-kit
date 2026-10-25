"""Unified retrieval — reciprocal rank fusion across memory backends.

Single entry point for hybrid search: each configured backend (lexical BM25,
vector similarity, …) produces its own ranking; :func:`rrf_fuse` merges them
by entry id with reciprocal rank fusion so that neither scoring scale
dominates.  Backend failures are tolerated and surfaced via
:meth:`HybridRetriever.issues`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any, cast

from grimoire.memory.backends.base import MemoryBackend, MemoryEntry
from grimoire.memory.supersession import search_live

if TYPE_CHECKING:
    from grimoire.memory.manager import MemoryManager

_RRF_K = 60


def rrf_fuse(rankings: list[list[MemoryEntry]], *, k: int = _RRF_K, limit: int = 5) -> list[MemoryEntry]:
    """Merge rankings with reciprocal rank fusion.

    Each entry scores ``sum(1 / (k + rank))`` over the rankings it appears
    in (rank is 1-based).  Entries are deduplicated by id, keeping the
    first occurrence's payload; the fused score replaces the backend score.
    """
    fused: dict[str, tuple[MemoryEntry, float]] = {}
    for ranking in rankings:
        for rank, entry in enumerate(ranking, start=1):
            kept, score = fused.get(entry.id, (entry, 0.0))
            fused[entry.id] = (kept, score + 1.0 / (k + rank))
    ordered = sorted(fused.values(), key=lambda item: item[1], reverse=True)
    return [replace(entry, score=score) for entry, score in ordered[:limit]]


class HybridRetriever:
    """Fuses search results from several named backends via RRF.

    Usage::

        retriever = HybridRetriever([("vector", qdrant), ("lexical", fts)])
        results = retriever.search("harmonisation", limit=5)
    """

    def __init__(self, backends: list[tuple[str, MemoryBackend]], *, k: int = _RRF_K) -> None:
        if not backends:
            raise ValueError("HybridRetriever requires at least one backend")
        self._backends = backends
        self._k = k
        self._issues: list[str] = []
        self._lanes: dict[str, list[tuple[str, float]]] = {}

    @property
    def lanes(self) -> dict[str, list[tuple[str, float]]]:
        """Per-backend ``(entry id, backend score)`` rankings of the last search, before fusion."""
        return {name: list(ranking) for name, ranking in self._lanes.items()}

    @property
    def issues(self) -> list[str]:
        """Backend failures recorded during the last search."""
        return list(self._issues)

    def sources(self) -> list[str]:
        """Names of the configured backends."""
        return [name for name, _ in self._backends]

    def search(self, query: str, *, user_id: str = "", limit: int = 5) -> list[MemoryEntry]:
        """Search every backend and fuse the rankings.

        A failing backend is skipped (issue recorded); the search only
        raises when every backend fails.
        """
        self._issues = []
        self._lanes = {}
        rankings: list[list[MemoryEntry]] = []
        # Over-fetch per backend so fusion has enough candidates to reorder.
        fetch = max(limit * 3, limit)
        for name, backend in self._backends:
            try:
                ranking = backend.search(query, user_id=user_id, limit=fetch)
                rankings.append(ranking)
                self._lanes[name] = [(entry.id, entry.score) for entry in ranking]
            except Exception as exc:
                self._issues.append(f"{name}: {exc}")
        if not rankings:
            raise RuntimeError(f"All retrieval backends failed: {'; '.join(self._issues)}")
        return rrf_fuse(rankings, k=self._k, limit=limit)


class CooldownGuardedBackend:
    """Adapte ``MemoryManager._guarded_backend_search`` à l'interface ``.search()``
    attendue par :class:`~grimoire.memory.retrieval.HybridRetriever`.

    ``hybrid_search`` construit un ``HybridRetriever`` qui appelle
    ``backend.search(...)`` directement sur chaque backend qu'on lui donne —
    sans ce petit adaptateur, le refroidissement de :meth:`MemoryManager.search`
    ne s'appliquerait qu'au chemin sans compagnon lexical, alors que c'est le
    chemin hybride (le seul qui compose vecteur + lexical) qui est le plus
    courant en lecture (``grimoire_memory_search`` MCP, cockpit). Même
    refroidissement, même identifiant, une seule politique.
    """

    __slots__ = ("_manager",)

    def __init__(self, manager: MemoryManager) -> None:
        self._manager = manager

    def search(self, query: str, *, user_id: str = "", limit: int = 5) -> list[MemoryEntry]:
        return self._manager._guarded_backend_search(query, user_id=user_id, limit=limit)


class LiveOnlySearch:
    """Écarte les souvenirs remplacés d'un classement avant la fusion RRF.

    Filtrer avant la fusion (et non après) évite qu'une entrée périmée
    consomme un rang de l'un des deux classements.
    """

    __slots__ = ("_include_retracted", "_include_superseded", "_inner", "_recall", "_verify_primary")

    def __init__(
        self, inner: Any, recall: Callable[[str], MemoryEntry | None],
        *, include_superseded: bool = False, include_retracted: bool = False, verify_primary: bool = False,
    ) -> None:
        self._verify_primary = verify_primary
        self._inner = inner
        self._recall = recall
        self._include_superseded = include_superseded
        self._include_retracted = include_retracted

    def search(self, query: str, *, user_id: str = "", limit: int = 5) -> list[MemoryEntry]:
        return search_live(
            lambda size: cast(list[MemoryEntry], self._inner.search(query, user_id=user_id, limit=size)),
            limit,
            self._recall,
            include_superseded=self._include_superseded,
            include_retracted=self._include_retracted,
            verify_primary=self._verify_primary,
        )
