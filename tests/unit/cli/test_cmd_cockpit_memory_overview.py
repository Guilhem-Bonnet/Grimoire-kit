"""``GET /api/workspace/memory/overview`` et ``.../search`` à travers un vrai
cockpit (#172, dernier volet de « Cockpit — du générateur statique au
portefeuille actif », Refs #468).

``tests/unit/tools/test_workspace_memory.py`` couvre déjà l'agrégation en
isolation (module pur, sans transport) ; ce module prouve que le cockpit sert
bien ces deux routes par HTTP, avec le contrat query-string documenté
(``projects=all|<slugs>``), sur un registre réel à trois entrées — deux
lisibles, une dont le chemin n'existe plus.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from grimoire.cli import cmd_cockpit
from grimoire.tools import project_registry as reg


@pytest.fixture(autouse=True)
def _cockpit_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "ck"
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(home))
    return home


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


def _get(port: int, path: str) -> tuple[int, dict[str, Any]]:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 — loopback de test
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


@pytest.fixture
def fleet_server(tmp_path: Path):  # type: ignore[no-untyped-def]
    """Un cockpit réel sur un registre à trois entrées : deux projets
    lisibles (``projet-a`` porte une entrée mémoire, ``projet-b`` aucune) et
    un troisième dont le chemin a disparu du disque."""
    a = tmp_path / "projet-a"
    b = tmp_path / "projet-b"
    _init_project(a, "Alpha")
    _init_project(b, "Beta")
    _store(a, "le cache redis a un ticket ouvert")
    reg.save_registry([
        {"slug": "projet-a", "name": "Alpha", "path": str(a)},
        {"slug": "projet-b", "name": "Beta", "path": str(b)},
        {"slug": "projet-c", "name": "Gamma", "path": str(tmp_path / "n-existe-pas")},
    ])
    cmd_cockpit._API_CACHE.clear()
    serve_dir = tmp_path / "serve"
    serve_dir.mkdir()
    handler = partial(cmd_cockpit._CockpitHandler, directory=str(serve_dir))
    httpd = cmd_cockpit.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()
    cmd_cockpit._API_CACHE.clear()


class TestOverviewRoute:
    def test_all_projects_lists_the_three_registry_entries(self, fleet_server: int) -> None:
        status, body = _get(fleet_server, "/api/workspace/memory/overview?project=projet-a&projects=all")

        assert status == 200, body
        slugs = {p["slug"] for p in body["projects"]}
        assert slugs == {"projet-a", "projet-b", "projet-c"}
        by_slug = {p["slug"]: p for p in body["projects"]}
        assert by_slug["projet-a"]["entries"] == 1
        assert by_slug["projet-c"]["reason"]

    def test_default_scope_is_only_the_served_project(self, fleet_server: int) -> None:
        status, body = _get(fleet_server, "/api/workspace/memory/overview?project=projet-a")

        assert status == 200, body
        assert [p["slug"] for p in body["projects"]] == ["projet-a"]

    def test_a_comma_separated_subset_is_honored(self, fleet_server: int) -> None:
        status, body = _get(
            fleet_server, "/api/workspace/memory/overview?project=projet-a&projects=projet-a,projet-b",
        )

        assert status == 200, body
        assert {p["slug"] for p in body["projects"]} == {"projet-a", "projet-b"}


class TestSearchRoute:
    def test_cross_project_search_labels_the_result_by_project(self, fleet_server: int) -> None:
        status, body = _get(
            fleet_server, "/api/workspace/memory/search?project=projet-a&projects=all&q=redis",
        )

        assert status == 200, body
        assert body["count"] == 1
        assert body["results"][0]["projectSlug"] == "projet-a"

    def test_missing_query_is_a_200_with_no_results(self, fleet_server: int) -> None:
        status, body = _get(fleet_server, "/api/workspace/memory/search?project=projet-a&projects=all")

        assert status == 200, body
        assert body["results"] == []

    def test_a_term_absent_from_every_project_returns_nothing(self, fleet_server: int) -> None:
        status, body = _get(
            fleet_server,
            "/api/workspace/memory/search?project=projet-a&projects=all&q=aucunmotquisoittrouve",
        )

        assert status == 200, body
        assert body["results"] == []
