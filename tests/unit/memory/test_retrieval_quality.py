"""Retrieval quality evals — gold set with recall@k regression guards.

A small fixed corpus of French/English memories with queries whose expected
results are known.  Guards the retrieval ladder ordering:

- lexical (FTS5 BM25) must not regress below the naive keyword backend
- tantivy (stemming) must resolve morphology the others cannot
- hybrid RRF fusion must recover results that either single ranking misses
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.memory.backends.base import MemoryBackend
from grimoire.memory.backends.lexical import LexicalMemoryBackend, fts5_available
from grimoire.memory.backends.local import LocalMemoryBackend
from grimoire.memory.retrieval import HybridRetriever

# ── Gold set ──────────────────────────────────────────────────────────────────

CORPUS: dict[str, str] = {
    "mem-qdrant": "Décision d'architecture : Qdrant retenu comme backend vectoriel du Memory OS.",
    "mem-evenement": "L'évènement de release est réservé aux mainteneurs du dépôt.",
    "mem-harmonisation": "Harmonisation des grimoires : fusion des worktrees nested et bridge.",
    "mem-python-dense": "Python Python tutorial — apprendre python rapidement.",
    "mem-python-long": "Ce texte mentionne python une seule fois parmi beaucoup d'autres sujets sans rapport direct.",
    "mem-ci": "La pipeline CI exécute ruff, mypy et pytest sur chaque pull request.",
    "mem-docs": "Documentation companions : chaque livrable inclut DOC-TECHNIQUE et GUIDE d'utilisation.",
    "mem-release": "Le processus de release publie le wheel sur PyPI après le tag de version.",
}

# query -> ids attendus dans le top-k (recall@k)
GOLD_LEXICAL: dict[str, set[str]] = {
    "qdrant vectoriel": {"mem-qdrant"},
    "evenement reserve": {"mem-evenement"},  # diacritiques
    "pipeline pytest": {"mem-ci"},
    "release wheel pypi": {"mem-release"},
}

GOLD_STEMMING: dict[str, set[str]] = {
    "harmonisé": {"mem-harmonisation"},  # FR : harmonisé -> harmonisation
    "documenté": {"mem-docs"},           # FR : documenté -> documentation
}

K = 5


def _populate(backend: MemoryBackend) -> None:
    for entry_id, text in CORPUS.items():
        backend.upsert(entry_id, text)


def _recall_at_k(backend_search: MemoryBackend, gold: dict[str, set[str]], k: int = K) -> float:
    """Fraction of expected ids found in the top-k across all gold queries."""
    hits = 0
    expected_total = 0
    for query, expected in gold.items():
        found = {entry.id for entry in backend_search.search(query, limit=k)}
        hits += len(expected & found)
        expected_total += len(expected)
    return hits / expected_total if expected_total else 0.0


# ── Ladder guards ─────────────────────────────────────────────────────────────

@pytest.mark.skipif(not fts5_available(), reason="SQLite build lacks FTS5")
class TestLexicalLadder:
    @pytest.fixture()
    def local(self, tmp_path: Path) -> LocalMemoryBackend:
        backend = LocalMemoryBackend(tmp_path / "local.json")
        _populate(backend)
        return backend

    @pytest.fixture()
    def lexical(self, tmp_path: Path) -> LexicalMemoryBackend:
        backend = LexicalMemoryBackend(tmp_path / "lexical.sqlite3")
        _populate(backend)
        return backend

    def test_lexical_recall_not_below_local(self, local: LocalMemoryBackend, lexical: LexicalMemoryBackend) -> None:
        assert _recall_at_k(lexical, GOLD_LEXICAL) >= _recall_at_k(local, GOLD_LEXICAL)

    def test_lexical_full_recall_on_gold_set(self, lexical: LexicalMemoryBackend) -> None:
        assert _recall_at_k(lexical, GOLD_LEXICAL) == 1.0

    def test_diacritics_query_beats_local(self, local: LocalMemoryBackend, lexical: LexicalMemoryBackend) -> None:
        gold = {"evenement reserve": {"mem-evenement"}}
        assert _recall_at_k(lexical, gold) == 1.0
        assert _recall_at_k(local, gold) == 0.0  # naive keyword match is accent-sensitive

    def test_bm25_ranks_dense_python_doc_first(self, lexical: LexicalMemoryBackend) -> None:
        results = lexical.search("python", limit=2)
        assert results[0].id == "mem-python-dense"


class TestStemmingLadder:
    def test_tantivy_resolves_french_morphology(self, tmp_path: Path) -> None:
        tantivy = pytest.importorskip("tantivy")
        del tantivy
        from grimoire.memory.backends.tantivy_local import TantivyMemoryBackend

        backend = TantivyMemoryBackend(tmp_path / "tantivy-index")
        _populate(backend)
        assert _recall_at_k(backend, GOLD_STEMMING) == 1.0
        assert _recall_at_k(backend, GOLD_LEXICAL) == 1.0

    @pytest.mark.skipif(not fts5_available(), reason="SQLite build lacks FTS5")
    def test_stemming_queries_are_the_fts5_gap(self, tmp_path: Path) -> None:
        """Documents why tantivy exists: FTS5 has no French stemmer."""
        backend = LexicalMemoryBackend(tmp_path / "lexical.sqlite3")
        _populate(backend)
        assert _recall_at_k(backend, GOLD_STEMMING) == 0.0


@pytest.mark.skipif(not fts5_available(), reason="SQLite build lacks FTS5")
class TestHybridLadder:
    def test_rrf_fusion_recovers_both_sides(self, tmp_path: Path) -> None:
        """Hybrid recall covers what each single ranking misses."""
        lexical = LexicalMemoryBackend(tmp_path / "lexical.sqlite3")
        _populate(lexical)
        # Simulated vector backend: knows the semantic match that lexical misses.
        semantic = LocalMemoryBackend(tmp_path / "vector.json")
        semantic.store("Harmonisation des grimoires : fusion des worktrees nested et bridge.")

        class SemanticStub(LocalMemoryBackend):
            def search(self, query: str, *, user_id: str = "", limit: int = 5):  # type: ignore[override]
                return self.get_all(limit=limit)  # always returns its semantic hit

        stub = SemanticStub(tmp_path / "vector.json")
        retriever = HybridRetriever([("vector", stub), ("lexical", lexical)])

        results = retriever.search("qdrant vectoriel", limit=K)
        found = {entry.id for entry in results}
        texts = {entry.text for entry in results}
        assert "mem-qdrant" in found  # lexical side
        assert any("Harmonisation" in text for text in texts)  # vector side
