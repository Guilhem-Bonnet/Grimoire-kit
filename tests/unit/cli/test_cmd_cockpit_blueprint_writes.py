"""``POST /api/blueprints/<id>/{compile,validate,simulate}`` sur le cockpit — issue #546.

En corrigeant #535 (``PUT`` absent de ``_CockpitHandler``, #543), la matrice
documentait ``/compile`` comme « atelier seulement » sans jamais l'avoir
vérifié contre un vrai ``_CockpitHandler`` — le même défaut qui a touché
``PUT``. Ce module le prouve avec un handler HTTP réel (pas un mock) :

- ``/compile`` manquait effectivement (``do_POST`` n'avait aucune branche
  pour lui, retour 404 « route inconnue ») ; câblé ici même garde que
  ``/api/setup`` et ``PUT`` (#543) — seul le projet de lancement direct
  (``_HOME_SLUG``) peut compiler, une vraie écriture disque (artefact
  ``.prompt.md`` + section ``compiled`` persistée dans le blueprint).
- ``/validate`` et ``/simulate`` étaient déjà câblés et volontairement SANS
  garde ``_HOME_SLUG`` (calcul pur, jamais d'écriture — voir le commentaire
  de ``do_POST``) : ce module le confirme avec le même test paramétré,
  plutôt que de le supposer, pour ne pas découvrir un défaut équivalent
  route par route.
- ``grimoire blueprint evals`` (commande CLI, ``cmd_blueprint.py``) n'a
  jamais été une route HTTP, ici ni sur l'atelier (``forge_http.py``) : rien
  à câbler, la matrice le documente comme tel plutôt que « non couvert ».
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

_BLUEPRINT = {"id": "demo", "nodes": [], "edges": []}


def _post(port: int, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
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
    l'est pas — même montage que ``test_cmd_cockpit_blueprint_put.py``
    (issue #535/#543)."""
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


# ── 200 sur le projet home, blueprint fourni dans le corps ─────────────────


@pytest.mark.parametrize("route", ["compile", "validate", "simulate"])
def test_route_succeeds_on_the_home_project(duo_server: Any, route: str) -> None:
    port, _home, _foreign = duo_server

    status, body = _post(port, f"/api/blueprints/demo/{route}?project=home", _BLUEPRINT)

    assert status == 200, body
    assert body.get("ok") is not False


# ── 404 sur un blueprint inconnu (ni corps, ni fichier sur disque) ─────────


@pytest.mark.parametrize("route", ["compile", "validate", "simulate"])
def test_route_is_a_404_on_an_unknown_blueprint(duo_server: Any, route: str) -> None:
    port, _home, _foreign = duo_server

    status, body = _post(port, f"/api/blueprints/introuvable/{route}?project=home", {})

    assert status == 404
    assert body["ok"] is False


# ── `/compile` écrit réellement : garde `_HOME_SLUG`, comme `/api/setup`/`PUT` ─


def test_compile_is_refused_on_a_non_home_registry_project(duo_server: Any) -> None:
    """Régression #546 : jusqu'ici ``/compile`` n'avait AUCUNE branche dans
    ``do_POST`` (404 « route inconnue » avant même d'atteindre une garde,
    exactement comme ``PUT`` avant #543) — jamais un 403 nommé."""
    port, _home, foreign = duo_server

    status, body = _post(port, "/api/blueprints/demo/compile?project=foreign", _BLUEPRINT)

    assert status == 403
    assert body["ok"] is False
    assert not (foreign / "_grimoire" / "blueprints" / "demo.blueprint.json").exists()
    assert not list((foreign / ".github").rglob("*.prompt.md")) if (foreign / ".github").exists() else True


def test_compile_writes_the_artifact_on_the_home_project(duo_server: Any) -> None:
    port, home, _foreign = duo_server

    status, body = _post(port, "/api/blueprints/demo/compile?project=home", _BLUEPRINT)

    assert status == 200, body
    assert body["compiled"] == "demo"
    artifact = home / body["artifact"]
    assert artifact.is_file()


def test_compile_refuses_a_blocked_blueprint_with_400(duo_server: Any) -> None:
    port, _home, _foreign = duo_server
    blocked = {
        "id": "flow-ko",
        "nodes": [
            {
                "id": "x",
                "kind": "extension-node",
                "ref": "crewai/crewai-crew",
                "label": "Crew",
                "pins": [{"id": "in", "direction": "in", "contract": "task-envelope"}],
            }
        ],
        "edges": [],
    }

    status, body = _post(port, "/api/blueprints/flow-ko/compile?project=home", blocked)

    assert status == 400
    assert body["ok"] is False


# ── `/validate` et `/simulate` restent volontairement SANS garde `_HOME_SLUG` ──
# (calcul pur, jamais d'écriture — voir le commentaire de `do_POST`) : prouvé
# ici plutôt que supposé, pour ne pas découvrir un défaut équivalent plus tard.


@pytest.mark.parametrize("route", ["validate", "simulate"])
def test_validate_and_simulate_are_allowed_on_a_non_home_registry_project(
    duo_server: Any, route: str
) -> None:
    port, _home, foreign = duo_server

    status, body = _post(port, f"/api/blueprints/demo/{route}?project=foreign", _BLUEPRINT)

    assert status == 200, body
    assert not (foreign / "_grimoire" / "blueprints" / "demo.blueprint.json").exists()
