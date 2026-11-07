"""Sondes mémoire parallèles et délai court en boucle locale (#954).

Sous Windows, un port local fermé n'est pas refusé tout de suite (relances
SYN, ~2 s). Les sondes de statut mémoire payaient ce délai à chaque service
absent, en série. Ces tests fixent le contrat :

- 0,3 s de délai de connexion pour 127.0.0.1/localhost, 0,5 s ailleurs ;
- plusieurs services sondés de front : durée = la plus lente, pas la somme ;
- un délai dépassé reste « inconnu », jamais « arrêté ».
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from grimoire.memory.backends.base import BackendStatus, MemoryBackend
from grimoire.memory.manager import MemoryManager
from grimoire.tools import memory_setup as ms
from grimoire.tools.memory_link import _store_graph_parity, memory_link_status


class TestLoopbackConnectTimeout:
    """Le délai court est posé sur les hôtes de boucle locale, nulle part ailleurs."""

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("http://127.0.0.1:8080", ms._LOOPBACK_CONNECT_TIMEOUT),
            ("http://localhost:6333", ms._LOOPBACK_CONNECT_TIMEOUT),
            ("http://[::1]:7687", ms._LOOPBACK_CONNECT_TIMEOUT),
            ("http://qdrant.lan:6333", ms._SOCKET_TIMEOUT),
        ],
    )
    def test_service_state_borne_le_delai_selon_l_hote(
        self, monkeypatch: pytest.MonkeyPatch, url: str, expected: float
    ) -> None:
        seen: list[float | None] = []

        def _refuse(_addr: object, timeout: float | None = None, **_kw: object) -> None:
            seen.append(timeout)
            raise ConnectionRefusedError

        monkeypatch.setattr(ms.socket, "create_connection", _refuse)
        assert ms.service_state("qdrant", url) == ms.SERVICE_STOPPED
        assert seen == [expected]

    def test_un_delai_explicite_gagne_toujours(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[float | None] = []

        def _refuse(_addr: object, timeout: float | None = None, **_kw: object) -> None:
            seen.append(timeout)
            raise ConnectionRefusedError

        monkeypatch.setattr(ms.socket, "create_connection", _refuse)
        ms.service_state("qdrant", "http://127.0.0.1:6333", timeout=5.0)
        assert seen == [5.0]

    def test_une_sonde_expiree_reste_inconnue_jamais_arretee(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _timeout(_addr: object, timeout: float | None = None, **_kw: object) -> None:
            raise TimeoutError("connect timed out")

        monkeypatch.setattr(ms.socket, "create_connection", _timeout)
        assert ms.service_state("neo4j", "bolt://127.0.0.1:7687") == ms.SERVICE_UNKNOWN

    def test_tcp_reachable_suit_le_meme_delai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[float | None] = []

        def _refuse(_addr: object, timeout: float | None = None, **_kw: object) -> None:
            seen.append(timeout)
            raise ConnectionRefusedError

        monkeypatch.setattr(ms.socket, "create_connection", _refuse)
        assert ms._tcp_reachable("http://localhost:8080", 8080) is False
        assert seen == [ms._LOOPBACK_CONNECT_TIMEOUT]


class TestParallelProbes:
    """Trois sondes lentes prennent la durée de la plus lente, pas leur somme."""

    SERVICES = (
        ("qdrant", "http://127.0.0.1:6333"),
        ("neo4j", "bolt://127.0.0.1:7687"),
        ("redis", "redis://127.0.0.1:6379/0"),
    )

    def test_service_states_sonde_les_services_de_front(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # La barrière ne se libère que si les trois sondes sont en vol en même
        # temps : une implémentation en série y resterait bloquée jusqu'au
        # dépassement, donc ce test échoue de façon déterministe sur `main`.
        barrier = threading.Barrier(len(self.SERVICES), timeout=2.0)

        def _slow(service: str, url: str, *, timeout: float | None = None) -> str:
            barrier.wait()
            time.sleep(0.15)
            return ms.SERVICE_STOPPED

        monkeypatch.setattr(ms, "service_state", _slow)

        started = time.perf_counter()
        states = ms.service_states(self.SERVICES)
        elapsed = time.perf_counter() - started

        assert states == {service: ms.SERVICE_STOPPED for service, _url in self.SERVICES}
        assert elapsed < 0.15 * 2  # ~0,15 s de front, ~0,45 s en série

    def test_probe_services_sonde_les_services_de_front(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        three = {"qdrant": ("qdrant", ()), "neo4j": ("neo4j", ()), "redis": ("redis", ())}
        monkeypatch.setattr(ms, "_EXTRA_MODULES", three)
        barrier = threading.Barrier(len(three), timeout=2.0)

        def _slow(url: str, port: int, *, timeout: float | None = None) -> bool:
            barrier.wait()
            time.sleep(0.15)
            return False

        monkeypatch.setattr(ms, "_tcp_reachable", _slow)

        started = time.perf_counter()
        probes = ms.probe_services()
        elapsed = time.perf_counter() - started

        assert set(probes) == set(three)
        assert elapsed < 0.15 * 2

    def test_memory_link_status_sonde_les_trois_services_de_front(
        self, tmp_path: Path
    ) -> None:
        """La sonde de statut garde la parallélisation de ``health_check()``."""
        (tmp_path / "project-context.yaml").write_text(
            "project:\n  name: demo\nmemory:\n  backend: local\n", encoding="utf-8"
        )

        def _slow(result: object) -> object:
            def _call() -> object:
                time.sleep(0.2)
                return result

            return _call

        backend = MagicMock(spec=MemoryBackend)
        backend.health_check.side_effect = _slow(
            BackendStatus(backend="mock", healthy=True, entries=0)
        )
        hot = MagicMock()
        hot.health_check.side_effect = _slow(MagicMock(to_dict=lambda: {"healthy": True}))
        graph = MagicMock()
        graph.health_check.side_effect = _slow(MagicMock(healthy=True, detail={}))
        graph.stats.return_value = {"memories": 0, "weaviate_objects": 0}
        manager = MemoryManager(backend, hot_memory=hot, memory_graph=graph)

        started = time.perf_counter()
        status = memory_link_status(tmp_path, probe=True, manager=manager)
        elapsed = time.perf_counter() - started

        assert status["state"] == "ok"
        # Trois sondes de 0,2 s en parallèle : ~0,2 s. En série (ou avec des
        # attentes cumulées), le test dépasserait 0,5 s.
        assert elapsed < 0.5


class TestGraphParityReusesParallelVerdict:
    """Le verdict du graphe, déjà rendu en parallèle, n'est pas repayé en série."""

    def test_un_graphe_deja_en_erreur_n_est_pas_resonde(self) -> None:
        graph = MagicMock()
        graph.stats.side_effect = AssertionError("stats() ne doit pas être rappelé")
        manager = SimpleNamespace(memory_graph=graph)
        health = SimpleNamespace(
            detail={
                "neo4j_graph_sync": "error",
                "neo4j_graph_sync_detail": {"reason": "sonde Neo4j expirée après 0.3s"},
            }
        )

        parity = _store_graph_parity(manager, 42, backend_status=health)

        assert "expirée" in parity["error"]

    def test_un_graphe_prêt_rend_toujours_la_parité(self) -> None:
        graph = MagicMock()
        graph.stats.return_value = {"memories": 42, "weaviate_objects": 42}
        manager = SimpleNamespace(memory_graph=graph)
        health = SimpleNamespace(detail={"neo4j_graph_sync": "ready"})

        assert _store_graph_parity(manager, 42, backend_status=health)["ok"] is True

        # Sans verdict de sonde (appel historique), le comportement ne change pas.
        assert _store_graph_parity(manager, 42)["ok"] is True
