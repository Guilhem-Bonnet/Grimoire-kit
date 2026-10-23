"""Agrégation mémoire multi-projets (#172, dernier volet de « Cockpit — du
générateur statique au portefeuille actif », Refs #468).

`memory_link_status()` (`grimoire.tools.memory_link`) reste mono-projet ;
`grimoire.tools.workspace_memory` l'appelle une fois par projet du registre et
agrège côté serveur — ce que la Flotte de Piloter faisait jusqu'ici en
boucle côté navigateur (`Promise.allSettled`). Un projet illisible doit
apparaître avec sa raison, jamais être compté comme un store vide en
silence (leçon #264).
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from grimoire.memory.backends.base import BackendStatus
from grimoire.memory.manager import MemoryManager, store_identity
from grimoire.tools import project_registry as reg
from grimoire.tools import workspace_memory as wm


def _init_project(root: Path, name: str, *, backend: str = "local") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "project-context.yaml").write_text(
        f"project:\n  name: {name}\nmemory:\n  backend: {backend}\n", encoding="utf-8",
    )


def _store(root: Path, text: str) -> None:
    from grimoire.core.config import GrimoireConfig
    from grimoire.memory.manager import MemoryManager

    cfg = GrimoireConfig.from_yaml(root / "project-context.yaml")
    mgr = MemoryManager.from_config(cfg, project_root=root)
    mgr.store(text)


@pytest.fixture(autouse=True)
def _cockpit_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "cockpit-home"))


class TestMemoryOverview:
    def test_two_readable_projects_and_one_unreadable(self, tmp_path: Path) -> None:
        a = tmp_path / "projet-a"
        b = tmp_path / "projet-b"
        _init_project(a, "Alpha")
        _init_project(b, "Beta")
        _store(a, "une décision importante sur le cache")
        reg.save_registry([
            {"slug": "projet-a", "name": "Alpha", "path": str(a)},
            {"slug": "projet-b", "name": "Beta", "path": str(b)},
            {"slug": "projet-c", "name": "Gamma", "path": str(tmp_path / "n-existe-pas")},
        ])

        overview = wm.memory_overview(a, "all")

        by_slug = {p["slug"]: p for p in overview["projects"]}
        assert by_slug["projet-a"]["entries"] == 1
        assert by_slug["projet-a"]["state"] == "ok"
        assert by_slug["projet-a"]["lastWrite"]
        assert by_slug["projet-a"]["lexicalIndex"] == "absent"
        assert by_slug["projet-b"]["entries"] == 0
        assert by_slug["projet-b"]["reason"] is None
        assert by_slug["projet-c"]["state"] == "unreadable"
        assert by_slug["projet-c"]["reason"]
        assert overview["summary"]["count"] == 3
        assert overview["summary"]["readable"] == 2
        assert overview["summary"]["totalEntries"] == 1

    def test_default_scope_is_this_project_only(self, tmp_path: Path) -> None:
        a = tmp_path / "projet-a"
        b = tmp_path / "projet-b"
        _init_project(a, "Alpha")
        _init_project(b, "Beta")
        reg.save_registry([
            {"slug": "projet-a", "name": "Alpha", "path": str(a)},
            {"slug": "projet-b", "name": "Beta", "path": str(b)},
        ])

        overview = wm.memory_overview(a, None)

        assert [p["slug"] for p in overview["projects"]] == ["projet-a"]

    def test_explicit_slug_list_ignores_the_rest_of_the_registry(self, tmp_path: Path) -> None:
        a = tmp_path / "projet-a"
        b = tmp_path / "projet-b"
        _init_project(a, "Alpha")
        _init_project(b, "Beta")
        reg.save_registry([
            {"slug": "projet-a", "name": "Alpha", "path": str(a)},
            {"slug": "projet-b", "name": "Beta", "path": str(b)},
        ])

        overview = wm.memory_overview(a, "projet-b")

        assert [p["slug"] for p in overview["projects"]] == ["projet-b"]

    def test_uninitialized_project_names_its_own_reason(self, tmp_path: Path) -> None:
        a = tmp_path / "projet-a"
        a.mkdir()
        reg.save_registry([{"slug": "projet-a", "name": "Alpha", "path": str(a)}])

        overview = wm.memory_overview(a, "all")

        row = overview["projects"][0]
        assert row["state"] == "uninitialized"
        assert "non initialisé" in row["reason"]

    def test_no_project_matches_yields_empty_list_not_an_error(self, tmp_path: Path) -> None:
        a = tmp_path / "projet-a"
        _init_project(a, "Alpha")
        reg.save_registry([{"slug": "projet-a", "name": "Alpha", "path": str(a)}])

        overview = wm.memory_overview(a, "projet-jamais-enregistre")

        assert overview["projects"] == []
        assert overview["summary"]["count"] == 0


def _server_project(root: Path, name: str, *, collection: str = "flotte") -> None:
    """Projet qui déclare une collection Qdrant sur un serveur partagé — le
    serveur n'est jamais contacté : ``from_config`` est remplacé par un faux."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "project-context.yaml").write_text(
        f"project:\n  name: {name}\nmemory:\n  backend: qdrant-server\n"
        f"  qdrant_url: http://qdrant.lan:6333/\n  collection_prefix: {collection}\n",
        encoding="utf-8",
    )


class _FakeManager:
    """Le strict nécessaire de ``MemoryManager`` pour l'aperçu de flotte."""

    memory_graph = None
    prefers_hybrid = False

    def __init__(self, entries: int = 0, *, before_probe: Any = None) -> None:
        self._entries = entries
        self._before_probe = before_probe

    def health_check(self, **_: Any) -> BackendStatus:
        if self._before_probe is not None:
            self._before_probe()
        return BackendStatus(backend="qdrant-server", healthy=True, entries=self._entries)

    def get_all(self, **_: Any) -> list[Any]:
        return []


def _patch_from_config(monkeypatch: pytest.MonkeyPatch, make: Any) -> Counter[str]:
    """Remplace ``MemoryManager.from_config`` et compte les ouvertures par projet."""
    opened: Counter[str] = Counter()

    def _from_config(cls: type, cfg: Any, *, project_root: Path | None = None) -> Any:
        opened[str(project_root)] += 1
        return make(cfg)

    monkeypatch.setattr(MemoryManager, "from_config", classmethod(_from_config))
    return opened


class _SlowBackend:
    """Backend dont la sonde dépasse le délai du manager."""

    def health_check(self) -> BackendStatus:
        time.sleep(0.6)
        return BackendStatus(backend="qdrant-server", healthy=True, entries=42)


class TestFailedReadIsNotZero:
    """Leçon #264 : une sonde expirée n'est pas un store vide."""

    def test_timed_out_probe_names_the_backend_not_the_project(self) -> None:
        mgr = MemoryManager(_SlowBackend(), project_name="Alpha", backend_id="qdrant-server")  # type: ignore[arg-type]

        status = mgr.health_check(timeout=0.05)

        assert status.healthy is False
        assert status.backend == "qdrant-server"
        assert "expirée" in status.detail["reason"]

    def test_timed_out_probe_without_backend_id_still_avoids_the_project_name(self) -> None:
        mgr = MemoryManager(_SlowBackend(), project_name="Alpha")  # type: ignore[arg-type]

        assert mgr.health_check(timeout=0.05).backend != "Alpha"

    def test_overview_row_has_null_entries_and_the_reason(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        a = tmp_path / "projet-a"
        _server_project(a, "Alpha")
        reg.save_registry([{"slug": "projet-a", "name": "Alpha", "path": str(a)}])
        _patch_from_config(
            monkeypatch,
            lambda cfg: MemoryManager(_SlowBackend(), project_name=cfg.project.name, backend_id="qdrant-server"),  # type: ignore[arg-type]
        )

        overview = wm.memory_overview(a, "all")

        row = overview["projects"][0]
        assert row["entries"] is None
        assert "expirée" in row["reason"]
        assert row["state"] == "unavailable"
        assert row["resolvedBackend"] == "qdrant-server"
        assert row["resolvedBackend"] not in {"Alpha", "projet-a"}
        assert overview["summary"]["totalEntries"] == 0


class TestSharedStoreCountsOnce:
    def test_five_projects_on_one_collection_count_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = []
        for i in range(5):
            root = tmp_path / f"projet-{i}"
            _server_project(root, f"P{i}")
            rows.append({"slug": f"projet-{i}", "name": f"P{i}", "path": str(root)})
        autre = tmp_path / "projet-autre"
        _server_project(autre, "Autre", collection="autre")
        rows.append({"slug": "projet-autre", "name": "Autre", "path": str(autre)})
        reg.save_registry(rows)
        _patch_from_config(
            monkeypatch,
            lambda cfg: _FakeManager(3 if cfg.memory.collection_prefix == "autre" else 7),
        )

        overview = wm.memory_overview(tmp_path, "all")

        assert [p["entries"] for p in overview["projects"]] == [7, 7, 7, 7, 7, 3]
        assert overview["summary"]["totalEntries"] == 7 + 3

    def test_store_identity_is_url_plus_collection(self, tmp_path: Path) -> None:
        from grimoire.core.config import GrimoireConfig

        def identity(root: Path) -> tuple[str, str]:
            return store_identity(GrimoireConfig.from_yaml(root / "project-context.yaml"), root)

        _server_project(tmp_path / "a", "A")
        _server_project(tmp_path / "b", "B")
        _server_project(tmp_path / "c", "C", collection="autre")
        _init_project(tmp_path / "d", "D")
        _init_project(tmp_path / "e", "E")

        assert identity(tmp_path / "a") == identity(tmp_path / "b") == ("http://qdrant.lan:6333", "flotte")
        assert identity(tmp_path / "c") != identity(tmp_path / "a")
        assert identity(tmp_path / "d") != identity(tmp_path / "e")


class TestParallelOverview:
    def test_one_manager_per_project_and_probes_run_in_parallel(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = []
        for i in range(4):
            root = tmp_path / f"projet-{i}"
            _init_project(root, f"P{i}")
            rows.append({"slug": f"projet-{i}", "name": f"P{i}", "path": str(root)})
        reg.save_registry(rows)
        # Preuve indépendante de la machine : la barrière ne s'ouvre que si
        # les quatre sondes attendent en même temps. Exécutées l'une après
        # l'autre, la première casse la barrière et sa ligne tombe en échec.
        barrier = threading.Barrier(4, timeout=5)

        def _rendezvous() -> None:
            barrier.wait()
            time.sleep(0.5)

        opened = _patch_from_config(monkeypatch, lambda _cfg: _FakeManager(1, before_probe=_rendezvous))

        started = time.perf_counter()
        overview = wm.memory_overview(tmp_path, "all")
        elapsed = time.perf_counter() - started

        assert [p["state"] for p in overview["projects"]] == ["ok"] * 4
        assert sorted(opened.values()) == [1, 1, 1, 1]
        assert elapsed < 1.5
        assert [p["slug"] for p in overview["projects"]] == [f"projet-{i}" for i in range(4)]


class TestMemorySearch:
    def test_cross_project_search_labels_each_result(self, tmp_path: Path) -> None:
        a = tmp_path / "projet-a"
        b = tmp_path / "projet-b"
        _init_project(a, "Alpha")
        _init_project(b, "Beta")
        _store(a, "le cache redis a un ticket ouvert")
        _store(b, "rien a voir ici")
        reg.save_registry([
            {"slug": "projet-a", "name": "Alpha", "path": str(a)},
            {"slug": "projet-b", "name": "Beta", "path": str(b)},
        ])

        result = wm.memory_search(a, "redis", "all")

        assert result["count"] == 1
        assert result["results"][0]["projectSlug"] == "projet-a"
        assert result["results"][0]["projectName"] == "Alpha"

    def test_empty_query_returns_no_results_without_calling_any_backend(self, tmp_path: Path) -> None:
        a = tmp_path / "projet-a"
        _init_project(a, "Alpha")
        reg.save_registry([{"slug": "projet-a", "name": "Alpha", "path": str(a)}])

        result = wm.memory_search(a, "   ", "all")

        assert result["count"] == 0
        assert result["results"] == []
        assert result["projects"] == []

    def test_order_is_stable_across_registry_order(self, tmp_path: Path) -> None:
        """Deux projets à égalité de score : le tri par slug rend l'ordre
        indépendant de l'ordre du registre, pas d'un hasard de dict."""
        a = tmp_path / "projet-a"
        b = tmp_path / "projet-b"
        _init_project(a, "Alpha")
        _init_project(b, "Beta")
        _store(a, "budget projet")
        _store(b, "budget projet")
        reg.save_registry([
            {"slug": "projet-b", "name": "Beta", "path": str(b)},
            {"slug": "projet-a", "name": "Alpha", "path": str(a)},
        ])

        result = wm.memory_search(a, "budget", "all")

        assert [r["projectSlug"] for r in result["results"]] == ["projet-a", "projet-b"]

    def test_unreadable_project_is_named_not_silently_skipped(self, tmp_path: Path) -> None:
        a = tmp_path / "projet-a"
        _init_project(a, "Alpha")
        reg.save_registry([
            {"slug": "projet-a", "name": "Alpha", "path": str(a)},
            {"slug": "projet-c", "name": "Gamma", "path": str(tmp_path / "n-existe-pas")},
        ])

        result = wm.memory_search(a, "budget", "all")

        by_slug = {p["slug"]: p for p in result["projects"]}
        assert by_slug["projet-c"]["reason"]
        assert by_slug["projet-c"]["count"] == 0
