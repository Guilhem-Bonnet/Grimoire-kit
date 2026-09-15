"""Tests du lien projet <-> BDD mémoire (brique B1)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from grimoire.tools import memory_link as ml


class TestBackendCatalogue:
    def test_catalogue_matches_cli_known_backends(self) -> None:
        from grimoire.cli.cmd_init import KNOWN_BACKENDS

        ids = {b["id"] for b in ml.BACKEND_CATALOGUE}
        assert ids == set(KNOWN_BACKENDS)

    def test_payload_shape(self) -> None:
        cat = ml.backend_catalogue()
        assert cat["schemaVersion"] == ml.MEMORY_LINK_SCHEMA_VERSION
        for b in cat["backends"]:
            assert b["id"] and b["label"] and b["detail"]
            assert b["kind"] in ("local", "server")


class TestMemoryLinkStatus:
    def test_uninitialized_project(self, tmp_path: Path) -> None:
        status = ml.memory_link_status(tmp_path)
        assert status["state"] == "uninitialized"
        assert status["configuredBackend"] is None
        assert status["available"] is False

    def test_initialized_local_backend(self, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text(
            "project:\n  name: demo\nmemory:\n  backend: local\n",
            encoding="utf-8",
        )
        # probe=True : on veut ici la sonde fraîche, pas le mode rapide par
        # défaut (voir TestFastMode plus bas pour ce dernier).
        status = ml.memory_link_status(tmp_path, probe=True)
        assert status["configuredBackend"] == "local"
        assert status["state"] == "ok"
        assert status["available"] is True
        assert status["resolvedBackend"] == "local"
        assert isinstance(status["entries"], int)
        assert status["probed"] is True
        assert status["stale"] is False
        assert status["probedAt"] is not None

    def test_no_tree_walk_to_parent_config(self, tmp_path: Path) -> None:
        # Un parent initialisé ne doit PAS contaminer un sous-dossier vierge.
        (tmp_path / "project-context.yaml").write_text(
            "project:\n  name: parent\nmemory:\n  backend: local\n",
            encoding="utf-8",
        )
        child = tmp_path / "sub"
        child.mkdir()
        assert ml.memory_link_status(child)["state"] == "uninitialized"

    def test_broken_config_reports_error(self, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text(
            ":: not yaml ::", encoding="utf-8"
        )
        status = ml.memory_link_status(tmp_path)
        assert status["state"] in ("uninitialized", "unavailable")
        assert status["error"]


class TestLayerContract:
    """La route de statut porte le contrat Memory OS, pas des heuristiques."""

    def test_layers_are_exposed_for_an_initialized_project(self, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text(
            "project:\n  name: demo\nmemory:\n  backend: local\n", encoding="utf-8"
        )
        status = ml.memory_link_status(tmp_path, probe=True)
        assert len(status["layers"]) == 7
        assert {layer["id"] for layer in status["layers"]} == {
            "short_term", "semantic_memory", "semantic_knowledge",
            "memory_graph", "code_graph", "task_memory", "visualization",
        }
        assert all(layer["state"] in {"ready", "partial", "planned", "disabled"}
                   for layer in status["layers"])

    def test_layers_survive_a_dead_backend(self, tmp_path: Path) -> None:
        """Le contrat vient de la config : il reste lisible backend mort."""
        (tmp_path / "project-context.yaml").write_text(
            "project:\n  name: demo\nmemory:\n  backend: mempalace\n"
            "  mempalace_path: /nonexistent/palace\n",
            encoding="utf-8",
        )
        status = ml.memory_link_status(tmp_path, probe=True)
        assert len(status["layers"]) == 7

    def test_uninitialized_project_has_empty_contract(self, tmp_path: Path) -> None:
        status = ml.memory_link_status(tmp_path)
        assert status["layers"] == []
        assert status["parity"] == {}


class TestStoreGraphParity:
    def test_no_graph_is_not_drift(self) -> None:
        manager = MagicMock()
        manager.memory_graph = None
        assert ml._store_graph_parity(manager, 42) == {}

    def test_aligned_counts(self) -> None:
        manager = MagicMock()
        manager.memory_graph.stats.return_value = {"memories": 42, "weaviate_objects": 42}
        parity = ml._store_graph_parity(manager, 42)
        assert parity["ok"] is True
        assert parity["drift"] == 0

    def test_drift_is_reported(self) -> None:
        manager = MagicMock()
        manager.memory_graph.stats.return_value = {"memories": 3700, "weaviate_objects": 3700}
        parity = ml._store_graph_parity(manager, 3701)
        assert parity["ok"] is False
        assert parity["drift"] == 1
        assert parity["graphVectorObjects"] == 3700

    def test_vector_reference_gap_is_drift(self) -> None:
        """Store et graphe alignés, mais des références vecteur manquantes."""
        manager = MagicMock()
        manager.memory_graph.stats.return_value = {"memories": 42, "weaviate_objects": 40}
        assert ml._store_graph_parity(manager, 42)["ok"] is False

    def test_unreachable_graph_reports_instead_of_raising(self) -> None:
        manager = MagicMock()
        manager.memory_graph.stats.side_effect = RuntimeError("connection refused")
        assert "connection refused" in ml._store_graph_parity(manager, 42)["error"]


class TestFastMode:
    """Mode rapide (``probe=False``, le défaut) : jamais de réseau.

    Avant le cache — issue de perf du cockpit — chaque lecture de
    ``/api/memory/status`` relançait ``MemoryManager.health_check()`` (trois
    sondes réseau), mesuré à 0,88s cumulés sur un projet réel dont les
    services distants sont éteints. Le mode rapide ne sonde jamais ; seul
    ``probe=True`` le fait, et remplit le cache pour les lectures suivantes.
    """

    @staticmethod
    def _init(tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text(
            "project:\n  name: demo\nmemory:\n  backend: local\n",
            encoding="utf-8",
        )

    def test_fast_mode_never_probes_when_nothing_cached(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._init(tmp_path)

        def _boom(*_a: object, **_k: object) -> None:
            raise AssertionError("le mode rapide ne doit jamais sonder")

        monkeypatch.setattr(ml, "_probe_memory_link_status", _boom)
        status = ml.memory_link_status(tmp_path)  # probe=False, défaut
        assert status["configuredBackend"] == "local"
        assert status["probed"] is False
        assert status["stale"] is True
        assert status["probedAt"] is None

    def test_probe_true_populates_cache_for_subsequent_fast_reads(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._init(tmp_path)
        calls = {"n": 0}
        real_probe = ml._probe_memory_link_status

        def _counting_probe(root: Path, cfg: object) -> dict[str, object]:
            calls["n"] += 1
            return real_probe(root, cfg)

        monkeypatch.setattr(ml, "_probe_memory_link_status", _counting_probe)

        probed = ml.memory_link_status(tmp_path, probe=True)
        assert calls["n"] == 1
        assert probed["probed"] is True
        assert probed["stale"] is False

        # Deux lectures rapides à la suite : aucune ne resonde le réseau.
        fast_1 = ml.memory_link_status(tmp_path)
        fast_2 = ml.memory_link_status(tmp_path)
        assert calls["n"] == 1
        for fast in (fast_1, fast_2):
            assert fast["probed"] is True
            assert fast["stale"] is False
            assert fast["resolvedBackend"] == probed["resolvedBackend"]
            assert fast["entries"] == probed["entries"]

    def test_stale_flag_set_once_ttl_elapsed_without_reprobing(
        self, tmp_path: Path
    ) -> None:
        from grimoire.memory import health_cache

        self._init(tmp_path)
        probed = ml.memory_link_status(tmp_path, probe=True)
        assert probed["stale"] is False

        # Recule artificiellement l'horodatage de la sonde, au-delà du TTL —
        # sans dormir dans un test et sans resonder le réseau.
        entry = health_cache.get(tmp_path)
        assert entry is not None
        backdated = health_cache.CacheEntry(
            status=entry.status,
            probed_at=entry.probed_at - health_cache.PROBE_TTL_SECONDS - 1,
        )
        with health_cache._lock:
            health_cache._cache[str(tmp_path.resolve())] = backdated

        stale = ml.memory_link_status(tmp_path)  # toujours probe=False
        assert stale["probed"] is True
        assert stale["stale"] is True

    def test_invalidation_clears_cached_status(self, tmp_path: Path) -> None:
        from grimoire.memory import health_cache

        self._init(tmp_path)
        ml.memory_link_status(tmp_path, probe=True)
        assert health_cache.get(tmp_path) is not None

        health_cache.invalidate(tmp_path)
        assert health_cache.get(tmp_path) is None

        # Plus rien en cache : le mode rapide redevient « jamais sondé »,
        # jamais une valeur potentiellement obsolète.
        status = ml.memory_link_status(tmp_path)
        assert status["probed"] is False
        assert status["stale"] is True
