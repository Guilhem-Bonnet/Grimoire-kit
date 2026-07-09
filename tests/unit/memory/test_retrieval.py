"""Tests for grimoire.memory.retrieval — RRF fusion and HybridRetriever."""

from __future__ import annotations

from typing import Any

import pytest

from grimoire.memory.backends.base import BackendStatus, MemoryBackend, MemoryEntry
from grimoire.memory.retrieval import HybridRetriever, rrf_fuse


def _entry(entry_id: str, text: str = "", score: float = 0.0) -> MemoryEntry:
    return MemoryEntry(id=entry_id, text=text or entry_id, score=score)


class FakeBackend(MemoryBackend):
    """Returns a fixed ranking; optionally raises."""

    def __init__(self, ranking: list[MemoryEntry], *, fail: bool = False) -> None:
        self._ranking = ranking
        self._fail = fail

    def search(self, query: str, *, user_id: str = "", limit: int = 5) -> list[MemoryEntry]:
        if self._fail:
            raise RuntimeError("backend down")
        return self._ranking[:limit]

    # Unused contract methods.
    def store(self, text: str, *, user_id: str = "", tags: tuple[str, ...] = (), metadata: dict[str, Any] | None = None) -> MemoryEntry:
        raise NotImplementedError

    def recall(self, entry_id: str) -> MemoryEntry | None:
        raise NotImplementedError

    def get_all(self, *, user_id: str = "", offset: int = 0, limit: int | None = None) -> list[MemoryEntry]:
        raise NotImplementedError

    def count(self) -> int:
        raise NotImplementedError

    def health_check(self) -> BackendStatus:
        raise NotImplementedError

    def consolidate(self) -> int:
        raise NotImplementedError


# ── rrf_fuse ──────────────────────────────────────────────────────────────────

class TestRrfFuse:
    def test_empty(self) -> None:
        assert rrf_fuse([]) == []
        assert rrf_fuse([[], []]) == []

    def test_single_ranking_preserves_order(self) -> None:
        ranking = [_entry("a"), _entry("b"), _entry("c")]
        fused = rrf_fuse([ranking], limit=3)
        assert [e.id for e in fused] == ["a", "b", "c"]

    def test_consensus_wins(self) -> None:
        # "b" is ranked by both backends, "a" and "c" only once each.
        fused = rrf_fuse([
            [_entry("a"), _entry("b")],
            [_entry("b"), _entry("c")],
        ], limit=3)
        assert fused[0].id == "b"

    def test_scores_are_rrf_sums(self) -> None:
        fused = rrf_fuse([[_entry("a")], [_entry("a")]], k=60, limit=1)
        assert fused[0].score == pytest.approx(2 / 61)

    def test_dedup_keeps_first_payload(self) -> None:
        fused = rrf_fuse([
            [_entry("a", text="from lexical")],
            [_entry("a", text="from vector")],
        ], limit=1)
        assert fused[0].text == "from lexical"

    def test_respects_limit(self) -> None:
        ranking = [_entry(str(i)) for i in range(10)]
        assert len(rrf_fuse([ranking], limit=4)) == 4


# ── HybridRetriever ───────────────────────────────────────────────────────────

class TestHybridRetriever:
    def test_requires_backends(self) -> None:
        with pytest.raises(ValueError):
            HybridRetriever([])

    def test_fuses_two_backends(self) -> None:
        vector = FakeBackend([_entry("v1"), _entry("shared")])
        lexical = FakeBackend([_entry("shared"), _entry("l1")])
        retriever = HybridRetriever([("vector", vector), ("lexical", lexical)])
        results = retriever.search("query", limit=3)
        assert results[0].id == "shared"
        assert {e.id for e in results} == {"shared", "v1", "l1"}
        assert retriever.issues == []

    def test_tolerates_one_failing_backend(self) -> None:
        vector = FakeBackend([], fail=True)
        lexical = FakeBackend([_entry("l1")])
        retriever = HybridRetriever([("vector", vector), ("lexical", lexical)])
        results = retriever.search("query")
        assert [e.id for e in results] == ["l1"]
        assert len(retriever.issues) == 1
        assert "vector" in retriever.issues[0]

    def test_raises_when_all_backends_fail(self) -> None:
        retriever = HybridRetriever([
            ("a", FakeBackend([], fail=True)),
            ("b", FakeBackend([], fail=True)),
        ])
        with pytest.raises(RuntimeError, match="All retrieval backends failed"):
            retriever.search("query")

    def test_sources(self) -> None:
        retriever = HybridRetriever([("vector", FakeBackend([])), ("lexical", FakeBackend([]))])
        assert retriever.sources() == ["vector", "lexical"]
