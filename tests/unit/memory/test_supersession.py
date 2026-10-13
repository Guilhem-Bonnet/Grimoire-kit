"""W6-06 — une mémoire remplacée n'est plus servie comme un fait.

Avant ce chantier, ``superseded_by`` n'était lu que par le contrôle de
rétention du standard (``controls.py``) : la recherche mémoire servait
l'ancienne décision et la nouvelle avec le même rang, sans distinction.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.memory.backends.local import LocalMemoryBackend
from grimoire.memory.manager import MemoryManager
from grimoire.memory.supersession import bench_supersession, is_superseded

runner = CliRunner()

OLD = "Décision : le backend vectoriel retenu est Weaviate."
NEW = "Décision : le backend vectoriel retenu est Qdrant, Weaviate est abandonné."
QUERY = "backend vectoriel retenu"


def _seeded(tmp_path: Path, *, companion: bool = False) -> MemoryManager:
    primary = LocalMemoryBackend(tmp_path / "primary.json")
    lexical = LocalMemoryBackend(tmp_path / "lexical.json") if companion else None
    mgr = MemoryManager(primary, lexical_companion=lexical)
    mgr.upsert("old", OLD, metadata={"note": "ancienne"})
    mgr.upsert("new", NEW)
    # Marquage par l'API existante : le défaut ne dépend pas de la nouvelle.
    _mark(mgr, "old", "new")
    return mgr


def _mark(mgr: MemoryManager, old_id: str, new_id: str) -> None:
    """Marquage par l'API existante (``update``), sans passer par ``supersede``.

    La provenance exigée par la politique d'écriture vient de l'entrée.
    """
    existing = mgr.recall(old_id)
    assert existing is not None
    mgr.update(old_id, metadata={**existing.metadata, "superseded_by": new_id})


def _ids(entries: list) -> list[str]:  # type: ignore[type-arg]
    return [e.id for e in entries]


class TestSearchExcludesSuperseded:
    def test_search_hides_superseded_by_default(self, tmp_path: Path) -> None:
        assert _ids(_seeded(tmp_path).search(QUERY)) == ["new"]

    def test_search_can_include_superseded(self, tmp_path: Path) -> None:
        got = _ids(_seeded(tmp_path).search(QUERY, include_superseded=True))
        assert set(got) == {"old", "new"}

    def test_hybrid_hides_superseded_in_both_rankings(self, tmp_path: Path) -> None:
        mgr = _seeded(tmp_path, companion=True)
        assert _ids(mgr.hybrid_search(QUERY)) == ["new"]
        assert set(_ids(mgr.hybrid_search(QUERY, include_superseded=True))) == {"old", "new"}

    def test_taxonomy_search_hides_superseded(self, tmp_path: Path) -> None:
        assert _ids(_seeded(tmp_path).search_taxonomy(QUERY)) == ["new"]

    def test_limit_is_filled_with_live_entries(self, tmp_path: Path) -> None:
        mgr = MemoryManager(LocalMemoryBackend(tmp_path / "m.json"))
        for i in range(6):
            mgr.upsert(f"stale-{i}", f"backend vectoriel retenu version {i} backend backend")
            _mark(mgr, f"stale-{i}", "live-0")
        mgr.upsert("live-0", "backend vectoriel retenu")
        mgr.upsert("live-1", "backend vectoriel retenu autre")
        assert set(_ids(mgr.search(QUERY, limit=2))) == {"live-0", "live-1"}

    def test_blank_marker_is_not_a_supersession(self, tmp_path: Path) -> None:
        mgr = MemoryManager(LocalMemoryBackend(tmp_path / "m.json"))
        mgr.upsert("a", OLD)
        _mark(mgr, "a", "  ")
        assert _ids(mgr.search(QUERY)) == ["a"]
        entry = mgr.recall("a")
        assert entry is not None
        assert is_superseded(entry) is False


class TestSupersedeApi:
    def test_supersede_marks_old_and_keeps_its_metadata(self, tmp_path: Path) -> None:
        mgr = MemoryManager(LocalMemoryBackend(tmp_path / "m.json"))
        mgr.upsert("old", OLD, metadata={"note": "x"})
        mgr.upsert("new", NEW)
        entry = mgr.supersede("old", "new")
        assert entry is not None
        assert entry.metadata["superseded_by"] == "new"
        assert entry.metadata["note"] == "x"
        assert _ids(mgr.search(QUERY)) == ["new"]

    def test_supersede_refuses_unknown_replacement(self, tmp_path: Path) -> None:
        from grimoire.core.exceptions import GrimoireMemoryError

        mgr = MemoryManager(LocalMemoryBackend(tmp_path / "m.json"))
        mgr.upsert("old", OLD)
        with pytest.raises(GrimoireMemoryError):
            mgr.supersede("old", "ghost")
        assert _ids(mgr.search(QUERY)) == ["old"]

    def test_supersede_refuses_self_reference(self, tmp_path: Path) -> None:
        from grimoire.core.exceptions import GrimoireMemoryError

        mgr = MemoryManager(LocalMemoryBackend(tmp_path / "m.json"))
        mgr.upsert("old", OLD)
        with pytest.raises(GrimoireMemoryError):
            mgr.supersede("old", "old")

    def test_supersede_unknown_old_returns_none(self, tmp_path: Path) -> None:
        mgr = MemoryManager(LocalMemoryBackend(tmp_path / "m.json"))
        mgr.upsert("new", NEW)
        assert mgr.supersede("ghost", "new") is None


class TestBench:
    def test_default_path_serves_no_stale_fact(self, tmp_path: Path) -> None:
        report = bench_supersession(_seeded(tmp_path), [QUERY])
        assert report["stale_served_as_fact_rate"] == 0.0
        assert report["results_served"] == 1

    def test_include_superseded_exposes_the_rate(self, tmp_path: Path) -> None:
        report = bench_supersession(_seeded(tmp_path), [QUERY], include_superseded=True)
        assert report["stale_served_as_fact_rate"] == 0.5
        assert report["stale_served"] == 1

    def test_recall_tokens_p95_is_nearest_rank_and_labelled_estimate(self, tmp_path: Path) -> None:
        report = bench_supersession(_seeded(tmp_path), [QUERY, "inconnu zzz"])
        assert report["recall_tokens_p95"] == -(-len(NEW) // 4)
        assert report["tokens_basis"] == "chars/4"

    def test_nothing_measured_is_null_not_zero(self, tmp_path: Path) -> None:
        report = bench_supersession(_seeded(tmp_path), ["inconnu zzz"])
        assert report["stale_served_as_fact_rate"] is None
        assert report["recall_tokens_p95"] is None
        report = bench_supersession(_seeded(tmp_path), [])
        assert report["queries"] == 0
        assert report["stale_served_as_fact_rate"] is None


class TestCli:
    @pytest.fixture()
    def mgr(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MemoryManager:
        manager = _seeded(tmp_path)
        monkeypatch.setattr("grimoire.cli.cmd_memory._load_manager", lambda *a, **k: manager)
        return manager

    def test_search_hides_then_includes(self, mgr: MemoryManager) -> None:
        out = runner.invoke(app, ["-o", "json", "memory", "search", QUERY, "--no-hybrid"])
        assert out.exit_code == 0, out.output
        assert [e["id"] for e in json.loads(out.output)] == ["new"]
        out = runner.invoke(app, ["-o", "json", "memory", "search", QUERY, "--no-hybrid", "--include-superseded"])
        assert {e["id"] for e in json.loads(out.output)} == {"old", "new"}

    def test_bench_command_reports_json(self, mgr: MemoryManager) -> None:
        out = runner.invoke(app, ["-o", "json", "memory", "bench", QUERY])
        assert out.exit_code == 0, out.output
        data = json.loads(out.output)
        assert data["stale_served_as_fact_rate"] == 0.0
        assert data["queries"] == 1
