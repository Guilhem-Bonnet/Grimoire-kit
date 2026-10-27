"""Premier lancement (P36) : ce que le guide détecte sur la machine.

Docker, les services mémoire, l'embedding et le registre sont rejoués par des
sondes factices (:class:`GuideProbes`) : aucun test ne demande Docker ni un
service. Ce qui est prouvé : un profil impossible l'est avec sa raison, une
sonde ratée donne « inconnu » (jamais un faux non), et la route est une
lecture sans écriture.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from grimoire.tools.memory_setup import ServiceProbe
from grimoire.tools.workspace_routes import GET_ROUTES, PREFIX, workspace_get
from grimoire.tools.workspace_setup_guide import SETUP_GUIDE_SCHEMA_VERSION, GuideProbes, setup_guide

ROUTE = f"{PREFIX}setup/guide"
EXTRAS = {"weaviate": "weaviate", "qdrant": "qdrant", "neo4j": "neo4j", "redis": "redis", "ollama": "ollama"}


@pytest.fixture(autouse=True)
def _cockpit_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "cockpit-home"))


def _services(up: set[str] = frozenset(), missing_extras: set[str] = frozenset()) -> dict[str, ServiceProbe]:
    return {
        sid: ServiceProbe(id=sid, url=f"http://localhost/{sid}", reachable=sid in up, extra=extra,
                          extra_installed=sid not in missing_extras)
        for sid, extra in EXTRAS.items()
    }


def _raise(message: str) -> Callable[..., Any]:
    def boom(*_args: object) -> Any:
        raise OSError(message)
    return boom


def _probes(
    *,
    binaries: set[str] = frozenset({"docker", "uv", "claude"}),
    daemon: Callable[[], bool] = lambda: True,
    services: Callable[[], dict[str, ServiceProbe]] = _services,
    embedding: Callable[[dict[str, ServiceProbe]], bool] = lambda _s: True,
    registry: Callable[[], list[dict[str, str]]] = lambda: [{"slug": "a", "path": "/a"}],
    python: Callable[[], str] = lambda: "3.12.4",
) -> GuideProbes:
    return GuideProbes(
        which=lambda name: f"/usr/bin/{name}" if name in binaries else None,
        docker_daemon=daemon,
        services=services,
        embedding=embedding,
        registry=registry,
        python_version=python,
        extras_hint=lambda extras: "pip install 'grimoire-kit[" + ",".join(extras) + "]'",
    )


def _by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {row["id"]: row for row in rows}


def test_machine_complete_tous_les_profils_possibles_et_complet_recommande(tmp_path: Path) -> None:
    guide = setup_guide(tmp_path, _probes(services=lambda: _services(up={"redis"})))
    assert guide["schemaVersion"] == SETUP_GUIDE_SCHEMA_VERSION
    assert guide["configured"] is False
    checks = _by_id(guide["checks"])
    assert checks["docker"]["state"] == "ok"
    assert checks["python"]["word"] == "3.12.4"
    assert checks["installer"]["word"] == "uv"
    assert checks["egress"]["state"] == "unknown", "le réseau sortant n'est jamais sondé"
    assert checks["services"]["word"] == "Redis"
    assert checks["hosts"]["word"] == "Claude Code"
    assert checks["projects"]["word"] == "1 projet"
    profiles = _by_id(guide["profiles"])
    assert all(p["possible"] is True for p in profiles.values())
    assert guide["recommended"]["id"] == "complet"
    assert profiles["complet"]["recommended"] is True
    # Redis répond déjà : partagé, pas démarré ; Weaviate et Neo4j seraient démarrés.
    assert profiles["complet"]["shared"] == ["Redis"]
    assert profiles["complet"]["start"] == ["Weaviate", "Neo4j"]
    init = [c["command"] for c in profiles["complet"]["commands"]["init"]]
    assert init == ["grimoire init . -y --memory-profile complet", "grimoire up .", "grimoire doctor ."]
    assert profiles["complet"]["commands"]["memory"][1]["command"] == "grimoire memory up --profile complet --start --apply"
    assert profiles["lexical"]["commands"]["memory"][1]["command"] == "grimoire memory up --profile lexical --apply"
    assert guide["writes"][0]["path"] == "project-context.yaml"


def test_sans_docker_graphe_et_complet_sont_impossibles_avec_leur_raison(tmp_path: Path) -> None:
    guide = setup_guide(tmp_path, _probes(binaries=set()))
    checks = _by_id(guide["checks"])
    assert checks["docker"]["state"] == "missing"
    assert checks["installer"]["state"] == "warn"
    assert checks["hosts"]["state"] == "warn"
    profiles = _by_id(guide["profiles"])
    for pid in ("graphe", "complet"):
        assert profiles[pid]["possible"] is False
        assert profiles[pid]["reasons"][0] == "demande Docker : introuvable sur ce poste"
    assert profiles["lexical"]["possible"] is True
    assert profiles["standard"]["possible"] is True
    assert guide["recommended"]["id"] == "standard"


def test_un_demon_docker_muet_se_dit_et_bloque_les_profils_serveur(tmp_path: Path) -> None:
    guide = setup_guide(tmp_path, _probes(daemon=lambda: False))
    assert _by_id(guide["checks"])["docker"]["state"] == "warn"
    graphe = _by_id(guide["profiles"])["graphe"]
    assert graphe["possible"] is False
    assert graphe["reasons"] == ["demande Docker : le démon ne répond pas"]


def test_docker_illisible_donne_inconnu_jamais_impossible(tmp_path: Path) -> None:
    guide = setup_guide(tmp_path, _probes(daemon=_raise("docker info a expiré")))
    docker = _by_id(guide["checks"])["docker"]
    assert docker["state"] == "unknown"
    assert "docker info a expiré" in docker["detail"]
    profiles = _by_id(guide["profiles"])
    assert profiles["graphe"]["possible"] is None
    assert profiles["complet"]["possible"] is None
    assert "on ne sait pas" in profiles["graphe"]["reasons"][0]


def test_services_illisibles_et_registre_illisible_restent_inconnus(tmp_path: Path) -> None:
    guide = setup_guide(
        tmp_path,
        _probes(services=_raise("sonde TCP refusée"), registry=_raise("registre verrouillé"), python=_raise("x")),
    )
    checks = _by_id(guide["checks"])
    assert checks["services"]["state"] == "unknown"
    assert checks["embedding"]["state"] == "unknown"
    assert checks["projects"]["state"] == "unknown"
    assert checks["python"]["state"] == "unknown"
    assert guide["services"] == []


def test_sans_moteur_d_embedding_lexical_est_recommande_et_les_extras_sont_nommes(tmp_path: Path) -> None:
    guide = setup_guide(
        tmp_path,
        _probes(embedding=lambda _s: False, services=lambda: _services(missing_extras={"qdrant", "neo4j"})),
    )
    assert _by_id(guide["checks"])["embedding"]["state"] == "missing"
    assert guide["recommended"]["id"] == "lexical"
    profiles = _by_id(guide["profiles"])
    assert "sans moteur d'embedding, la recherche reste lexicale" in profiles["standard"]["caveats"]
    assert profiles["standard"]["extras"] == ["qdrant"]
    assert profiles["standard"]["extrasHint"] == "pip install 'grimoire-kit[qdrant]'"
    assert profiles["graphe"]["extras"] == ["neo4j"]
    assert profiles["lexical"]["extras"] == []


def test_redis_sans_docker_rend_complet_impossible_pour_deux_raisons(tmp_path: Path) -> None:
    guide = setup_guide(tmp_path, _probes(binaries={"pipx"}))
    complet = _by_id(guide["profiles"])["complet"]
    assert complet["possible"] is False
    assert len(complet["reasons"]) == 2
    assert any("Redis" in reason for reason in complet["reasons"])


def test_un_projet_configure_est_dit_configure(tmp_path: Path) -> None:
    (tmp_path / "project-context.yaml").write_text("project:\n  name: x\n", encoding="utf-8")
    assert setup_guide(tmp_path, _probes())["configured"] is True


def test_la_route_est_une_lecture_declaree(tmp_path: Path) -> None:
    assert ROUTE in GET_ROUTES
    project = tmp_path / "projet"
    project.mkdir()
    payload = workspace_get(project, ROUTE, {})
    assert payload["schemaVersion"] == SETUP_GUIDE_SCHEMA_VERSION
    assert {p["id"] for p in payload["profiles"]} == {"lexical", "standard", "graphe", "complet"}
    # Lecture seule : la route n'a rien écrit dans le dossier servi.
    assert list(project.iterdir()) == []
