"""``POST /api/projects/create`` — nouveau projet depuis le portefeuille (#172).

Contrairement à `/api/setup` (issue #171), cette route ne dépend pas du
projet de lancement direct (`_HOME_SLUG`) : le chemin cible est explicite
dans le corps de la requête, exactement comme `/api/projects/add`. Elle
donne naissance à un projet — un dossier qui EST DÉJÀ un projet, ou un
chemin hors des racines permises, est refusé ; jamais de mutation d'un
projet existant que l'utilisateur ne fait que regarder.
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
from grimoire.tools import project_registry


@pytest.fixture(autouse=True)
def _cockpit_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "ck"
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(home))
    return home


def _served_project(tmp_path: Path) -> Path:
    p = tmp_path / "served"
    (p / ".git").mkdir(parents=True)
    return p


def _post(port: int, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


@pytest.fixture
def api_server(tmp_path: Path):  # type: ignore[no-untyped-def]
    """Un cockpit réel, avec un projet déjà enregistré sous ``tmp_path``.

    Le projet « served » n'est pas la cible des tests : il n'est là que pour
    que ``tmp_path`` (son dossier parent) devienne une racine permise
    (`project_registry.allowed_roots`), condition que doit remplir tout
    chemin de création passé dans ces tests.
    """
    proj = _served_project(tmp_path)
    project_registry.save_registry([{"name": "Served", "path": str(proj), "slug": "served"}])
    httpd = cmd_cockpit.ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(cmd_cockpit._CockpitHandler, directory=str(tmp_path))
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


class TestCreateProject:
    def test_scaffolds_and_registers_a_new_project(self, api_server: int, tmp_path: Path) -> None:
        target = tmp_path / "nouveau-projet"
        status, body = _post(
            api_server, "/api/projects/create",
            {"path": str(target), "name": "Nouveau", "user": "guilhem", "backend": "local"},
        )
        assert status == 200, body
        assert body["executed"] is True
        assert body["slug"], body
        assert (target / "project-context.yaml").is_file()
        registered = {p["path"] for p in project_registry.load_registry()}
        assert str(target.resolve()) in registered

    def test_refuses_an_already_existing_project(self, api_server: int, tmp_path: Path) -> None:
        target = tmp_path / "deja-un-projet"
        (target / ".git").mkdir(parents=True)
        status, body = _post(api_server, "/api/projects/create", {"path": str(target)})
        assert status == 409, body
        assert body["ok"] is False

    def test_refuses_a_path_outside_allowed_roots(self, api_server: int) -> None:
        status, body = _post(api_server, "/api/projects/create", {"path": "/etc/nouveau-projet"})
        assert status == 403, body
        assert body["ok"] is False

    def test_refuses_an_unknown_archetype_before_writing(self, api_server: int, tmp_path: Path) -> None:
        target = tmp_path / "refuse-archetype"
        status, body = _post(
            api_server, "/api/projects/create",
            {"path": str(target), "archetype": "pas-un-archetype"},
        )
        assert status == 400, body
        assert not target.exists()

    def test_second_create_on_the_same_path_now_refuses(self, api_server: int, tmp_path: Path) -> None:
        target = tmp_path / "deux-fois"
        first = _post(api_server, "/api/projects/create", {"path": str(target), "backend": "local"})
        assert first[0] == 200, first[1]
        second = _post(api_server, "/api/projects/create", {"path": str(target), "backend": "local"})
        assert second[0] == 409, second[1]
