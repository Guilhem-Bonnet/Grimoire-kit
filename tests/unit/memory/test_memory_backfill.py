"""Tests de la rétro-projection des souvenirs vers Neo4j.

La projection d'un souvenir se fait à l'écriture. Quand le graphe est
indisponible à ce moment-là, le vecteur atterrit et le nœud non — et jusqu'ici
rien ne rattrapait l'écart.
"""

from __future__ import annotations

from typing import Any

from grimoire.memory.backends.base import MemoryEntry
from grimoire.memory.projections import sync_memory_projection


class _Graph:
    """Graphe factice qui enregistre les upserts, et peut en refuser certains."""

    def __init__(self, refuse: set[str] | None = None) -> None:
        self.upserted: list[str] = []
        self._refuse = refuse or set()

    def upsert_memory(self, entry: MemoryEntry) -> None:
        if entry.id in self._refuse:
            raise RuntimeError(f"refus simulé pour {entry.id}")
        self.upserted.append(entry.id)


def _entries(*ids: str) -> list[MemoryEntry]:
    return [MemoryEntry(id=i, text=f"souvenir {i}") for i in ids]


class TestSyncMemoryProjection:
    def test_projects_every_entry(self) -> None:
        graph: Any = _Graph()
        stats = sync_memory_projection(graph, _entries("a", "b", "c"))
        assert stats == {"projected": 3, "failed": 0}
        assert graph.upserted == ["a", "b", "c"]

    def test_empty_store_is_not_an_error(self) -> None:
        graph: Any = _Graph()
        assert sync_memory_projection(graph, []) == {"projected": 0, "failed": 0}

    def test_one_bad_entry_does_not_stop_the_backfill(self) -> None:
        """Un rattrapage qui s'arrête à la première erreur ne rattrape rien."""
        graph: Any = _Graph(refuse={"b"})
        stats = sync_memory_projection(graph, _entries("a", "b", "c"))
        assert stats == {"projected": 2, "failed": 1}
        assert graph.upserted == ["a", "c"]

    def test_accepts_any_iterable(self) -> None:
        graph: Any = _Graph()
        stats = sync_memory_projection(graph, iter(_entries("a", "b")))
        assert stats["projected"] == 2

    def test_replaying_is_safe(self) -> None:
        """upsert_memory fait un MERGE : rejouer ne doit pas être un cas spécial."""
        graph: Any = _Graph()
        entries = _entries("a", "b")
        sync_memory_projection(graph, entries)
        stats = sync_memory_projection(graph, entries)
        assert stats == {"projected": 2, "failed": 0}
        assert graph.upserted == ["a", "b", "a", "b"]
