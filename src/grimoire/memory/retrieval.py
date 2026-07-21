"""Unified retrieval — reciprocal rank fusion across memory backends.

Single entry point for hybrid search: each configured backend (lexical BM25,
vector similarity, …) produces its own ranking; :func:`rrf_fuse` merges them
by entry id with reciprocal rank fusion so that neither scoring scale
dominates.  Backend failures are tolerated and surfaced via
:meth:`HybridRetriever.issues`.
"""

from __future__ import annotations

from dataclasses import replace

from grimoire.memory.backends.base import MemoryBackend, MemoryEntry

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
        rankings: list[list[MemoryEntry]] = []
        # Over-fetch per backend so fusion has enough candidates to reorder.
        fetch = max(limit * 3, limit)
        for name, backend in self._backends:
            try:
                rankings.append(backend.search(query, user_id=user_id, limit=fetch))
            except Exception as exc:
                self._issues.append(f"{name}: {exc}")
        if not rankings:
            raise RuntimeError(f"All retrieval backends failed: {'; '.join(self._issues)}")
        return rrf_fuse(rankings, k=self._k, limit=limit)
