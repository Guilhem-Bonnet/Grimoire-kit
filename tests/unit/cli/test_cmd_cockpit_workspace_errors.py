"""Erreurs métier des routes ``/api/workspace/*`` du cockpit — issue #536.

Le cockpit recueillait toute exception de lecture dans un 500 générique, alors
que l'atelier (``forge_http.py``) et ``do_POST`` du même cockpit traduisent
``FileNotFoundError`` en 404, ``PermissionError`` en 403 et ``ValueError`` en
400. Une tâche inconnue ou un chemin refusé arrivaient donc comme une panne
serveur. Ce module le prouve avec un vrai ``_CockpitHandler`` et couvre au
passage les écritures voisines : ``POST .../tasks/<id>/block`` (raison requise)
et ``POST .../file/write`` (étage non éditable, chemin hors racine).
"""

from __future__ import annotations

import json
import threading
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from grimoire.cli import cmd_cockpit
from grimoire.tools import project_registry

from .. import _cockpit_http

_get = _cockpit_http.get
_post = _cockpit_http.post


@pytest.fixture
def home_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Un projet de lancement (``home``) portant une tâche du Mission Ledger."""
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "ck"))
    home = tmp_path / "home"
    (home / ".git").mkdir(parents=True)
    project_registry.save_registry([{"name": "Home", "path": str(home), "slug": "home"}])
    monkeypatch.setattr(cmd_cockpit, "_HOME_SLUG", "home")
    cmd_cockpit._API_CACHE.clear()

    from grimoire.missions.service import TaskService

    ledger = TaskService(home).ledger
    mission = ledger.create_mission(title="Chantier", origin="test", created_by="test")
    task = ledger.create_task(mission.id, "À bloquer", acceptance=("une raison est donnée",))

    httpd = cmd_cockpit.ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(cmd_cockpit._CockpitHandler, directory=str(tmp_path))
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1], home, task.id
    httpd.shutdown()
    httpd.server_close()
    cmd_cockpit._API_CACHE.clear()


# ── GET /api/workspace/tasks/<id> ───────────────────────────────────────────


def test_une_tache_connue_est_rendue_avec_son_etat_de_board(home_server: Any) -> None:
    port, _home, task_id = home_server

    status, body = _get(port, f"/api/workspace/tasks/{task_id}?project=home")

    assert status == 200, body
    assert body["id"] == task_id
    assert body["board"] == "proposed"


def test_une_tache_inconnue_est_un_404_pas_un_500(home_server: Any) -> None:
    """Le test de ``test_workspace_api`` ne prouve que la couche fonction
    (``FileNotFoundError``) ; c'est le transport qui décide du code HTTP."""
    port, _home, _task_id = home_server

    status, body = _get(port, "/api/workspace/tasks/GAO-inexistante-999?project=home")

    assert status == 404
    assert body["ok"] is False
    assert "GAO-inexistante-999" in body["error"]


def test_un_identifiant_de_tache_malforme_est_un_400(home_server: Any) -> None:
    port, _home, _task_id = home_server

    status, body = _get(port, "/api/workspace/tasks/a/b?project=home")

    assert status == 400
    assert "identifiant de tâche invalide" in body["error"]


# ── Les autres lectures workspace suivent la même traduction ────────────────


@pytest.mark.parametrize("path", ["../../etc/passwd", "/etc/passwd", "_grimoire/rien-ici.md"])
def test_un_chemin_refuse_par_la_lecture_de_fichier_est_un_403(home_server: Any, path: str) -> None:
    port, _home, _task_id = home_server

    status, body = _get(port, f"/api/workspace/file?path={path}&project=home")

    assert status == 403, body
    assert body["ok"] is False


# ── POST /api/workspace/tasks/<id>/block ────────────────────────────────────


def test_bloquer_sans_raison_est_un_400_et_ne_bouge_pas_la_tache(home_server: Any) -> None:
    port, _home, task_id = home_server

    status, body = _post(port, f"/api/workspace/tasks/{task_id}/block?project=home", {})

    assert status == 400
    assert "reason" in body["error"]
    _s, detail = _get(port, f"/api/workspace/tasks/{task_id}?project=home")
    assert detail["status"] == "proposed"


def test_bloquer_avec_une_raison_passe_la_tache_en_blocked(home_server: Any) -> None:
    port, home, task_id = home_server

    # Une tâche « proposed » ne peut pas être bloquée (transition invalide) :
    # on la rend prête puis on la prend, comme le fait le bouton « Réaliser ».
    from grimoire.missions.schemas import TaskState
    from grimoire.missions.service import TaskService

    TaskService(home).transition(task_id, TaskState.READY, "test")
    claimed, claim_body = _post(port, f"/api/workspace/tasks/{task_id}/claim?project=home", {})
    assert claimed == 200, claim_body

    status, body = _post(port, f"/api/workspace/tasks/{task_id}/block?project=home", {"reason": "attend la revue"})

    assert status == 200, body
    _s, detail = _get(port, f"/api/workspace/tasks/{task_id}?project=home")
    assert detail["status"] == "blocked"
    assert detail["board"] == "blocked"


# ── POST /api/workspace/file/write ──────────────────────────────────────────


def test_ecrire_dans_l_etage_kit_est_un_403_qui_nomme_l_override(home_server: Any) -> None:
    port, home, _task_id = home_server
    cible = home / "_grimoire" / "kit" / "note.md"
    cible.parent.mkdir(parents=True)
    cible.write_text("kit", encoding="utf-8")

    status, body = _post(
        port, "/api/workspace/file/write?project=home", {"path": "_grimoire/kit/note.md", "text": "perdu"}
    )

    assert status == 403
    assert "override" in body["error"]
    assert cible.read_text(encoding="utf-8") == "kit", "l'étage kit n'est jamais écrit"


@pytest.mark.parametrize("path", ["../evade.md", "/tmp/evade.md", "_grimoire/overrides/../../evade.md"])
def test_ecrire_hors_de_la_racine_est_un_403_et_n_ecrit_rien(home_server: Any, tmp_path: Path, path: str) -> None:
    port, _home, _task_id = home_server

    status, body = _post(port, "/api/workspace/file/write?project=home", {"path": path, "text": "x"})

    assert status == 403, body
    assert not (tmp_path / "evade.md").exists()
    assert not Path("/tmp/evade.md").exists()


def test_ecrire_dans_l_etage_overrides_reussit(home_server: Any) -> None:
    port, home, _task_id = home_server

    status, body = _post(
        port,
        "/api/workspace/file/write?project=home",
        {"path": "_grimoire/overrides/note.md", "text": "mien"},
    )

    assert status == 200, body
    assert (home / "_grimoire" / "overrides" / "note.md").read_text(encoding="utf-8") == "mien"


# ── Les vraies pannes restent des 500 ───────────────────────────────────────


@pytest.mark.parametrize(
    "error",
    [
        json.JSONDecodeError("fichier d'état illisible", "{", 1),
        RuntimeError("backend en panne"),
        OSError("disque plein"),
    ],
    ids=["json-illisible", "runtime", "os"],
)
def test_une_vraie_panne_de_lecture_reste_un_500(
    home_server: Any, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    """La traduction ne doit pas tout ranger en 4xx : un fichier d'état illisible
    (``JSONDecodeError`` est un ``ValueError``) ou une panne du système ne sont
    pas des requêtes invalides."""
    port, _home, _task_id = home_server

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise error

    monkeypatch.setattr("grimoire.tools.forge_routes.api_get", _boom)

    status, body = _get(port, "/api/status?project=home")

    assert status == 500
    assert body["ok"] is False
    assert str(error) in body["error"]
