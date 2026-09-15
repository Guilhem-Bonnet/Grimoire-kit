"""``PUT /api/blueprints/<id>`` sur le cockpit — issue #535.

Constat : ``_CockpitHandler`` (``cmd_cockpit.py``) ne définissait aucun
``do_PUT``. ``http.server`` répond alors 501 (méthode non gérée) avant même
d'atteindre une garde — y compris sur l'atelier (``grimoire serve``, dont
``readOnly`` vaut ``false``), qui délègue à ce même handler depuis #351
(``cmd_serve.py`` appelle ``cmd_cockpit.serve``). Le bouton « Enregistrer »
de Concevoir restait donc sans effet réel quel que soit le câblage côté
client — la moitié serveur du bug, distincte de la moitié client
(``addNode`` n'appelait jamais ``blueprintPut``) que
``tests/e2e/test_workspace_concevoir.py`` couvre.

Même garde que ``/api/setup``/``do_POST`` du même module : seul le projet de
lancement direct (``_HOME_SLUG``) accepte l'écriture, un projet du registre
qu'on ne fait que regarder via le cockpit reste en lecture seule (#356).
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


def _put(port: int, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="PUT",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 — loopback de test
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"raw": body.decode("utf-8", "replace")}


def _project(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    (p / ".git").mkdir(parents=True)
    (p / "_grimoire" / "blueprints").mkdir(parents=True)
    return p


@pytest.fixture(autouse=True)
def _cockpit_home_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "ck"))


@pytest.fixture
def duo_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Deux projets enregistrés ; ``home`` est ``_HOME_SLUG``, ``foreign`` ne
    l'est pas — comme la Flotte, ou un ``?project=`` explicite vers un projet
    qu'on ne fait que regarder."""
    home, foreign = _project(tmp_path, "home"), _project(tmp_path, "foreign")
    project_registry.save_registry(
        [
            {"name": "Home", "path": str(home), "slug": "home"},
            {"name": "Foreign", "path": str(foreign), "slug": "foreign"},
        ]
    )
    monkeypatch.setattr(cmd_cockpit, "_HOME_SLUG", "home")
    cmd_cockpit._API_CACHE.clear()
    httpd = cmd_cockpit.ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(cmd_cockpit._CockpitHandler, directory=str(tmp_path))
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1], home, foreign
    httpd.shutdown()
    httpd.server_close()
    cmd_cockpit._API_CACHE.clear()


def test_put_writes_the_blueprint_on_the_home_project(duo_server: Any) -> None:
    """Le contrat que ce module existe pour prouver : sans `do_PUT`, cet
    appel échouait 501 avant même d'atteindre la garde `_HOME_SLUG`."""
    port, home, _foreign = duo_server
    blueprint = {"id": "demo", "nodes": [], "edges": []}

    status, body = _put(port, "/api/blueprints/demo?project=home", blueprint)

    assert status == 200
    assert body["saved"] == "demo"
    saved = json.loads((home / "_grimoire" / "blueprints" / "demo.blueprint.json").read_text())
    assert saved == blueprint


def test_put_is_refused_on_a_non_home_registry_project(duo_server: Any) -> None:
    """Écrire un blueprint sur un AUTRE projet du registre — qu'on ne fait
    que regarder via le cockpit — doit rester 403, comme le reste des
    écritures de la vue de travail (#356)."""
    port, _home, foreign = duo_server
    blueprint = {"id": "demo", "nodes": [], "edges": []}

    status, body = _put(port, "/api/blueprints/demo?project=foreign", blueprint)

    assert status == 403
    assert body["ok"] is False
    assert not (foreign / "_grimoire" / "blueprints" / "demo.blueprint.json").exists()


def test_put_refuses_a_structurally_invalid_body_with_400(duo_server: Any) -> None:
    """Issue #535 : un corps dont `nodes` n'est pas une liste levait une
    `AttributeError` non rattrapée (`blueprint_put`) — 400 explicite désormais."""
    port, _home, _foreign = duo_server

    status, body = _put(port, "/api/blueprints/demo?project=home", {"nodes": "x", "edges": []})

    assert status == 400
    assert body["ok"] is False


def test_put_on_an_unknown_route_is_a_404(duo_server: Any) -> None:
    port, _home, _foreign = duo_server

    status, body = _put(port, "/api/other?project=home", {})

    assert status == 404
    assert body["ok"] is False
