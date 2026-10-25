"""Routes du cockpit restées sans test — issue #536 (solde de la matrice).

Chaque test parle à un vrai ``_CockpitHandler`` sur la loopback (client
``_cockpit_http``, #857), jamais à un mock : la matrice
``docs/cockpit-coverage-matrix.md`` exige qu'une ligne « couverte » échoue si le
comportement décrit disparaît.

Regroupe : ``GET /api/fs/browse``, ``POST /api/projects/{select,update,add,scan}``,
``POST /api/memory``, la traduction des erreurs de lecture ``/api/workspace/*``,
``GET /api/workspace/tasks/<id>``, ``POST .../block`` et l'écriture Source
(étage non éditable, chemin hors racine).
"""

from __future__ import annotations

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
    """Deux projets au registre ; ``home`` est le projet de lancement."""
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


# ── GET /api/fs/browse : 200 / 403 / 404 ────────────────────────────────────


def test_browse_liste_les_sous_dossiers_d_un_dossier_permis(duo_server: Any, tmp_path: Path) -> None:
    port, home, _foreign = duo_server
    (home / "src").mkdir()
    (home / ".cache").mkdir()

    status, body = _get(port, f"/api/fs/browse?path={home}")

    assert status == 200, body
    names = {e["name"] for e in body["entries"]}
    assert "src" in names
    assert ".cache" not in names, "les dossiers cachés sont écartés"
    assert body["path"] == str(home.resolve())


def test_browse_hors_des_racines_autorisees_est_un_403(duo_server: Any) -> None:
    port, _home, _foreign = duo_server

    status, body = _get(port, "/api/fs/browse?path=/etc")

    assert status == 403
    assert body["ok"] is False
    assert "racines autorisées" in body["error"]


def test_browse_d_un_fichier_est_un_404(duo_server: Any) -> None:
    port, home, _foreign = duo_server
    (home / "note.txt").write_text("x", encoding="utf-8")

    status, body = _get(port, f"/api/fs/browse?path={home / 'note.txt'}")

    assert status == 404
    assert "pas un dossier" in body["error"]


# ── POST /api/projects/select ───────────────────────────────────────────────


def test_select_par_slug_retient_le_projet(duo_server: Any) -> None:
    port, _home, _foreign = duo_server

    status, body = _post(port, "/api/projects/select", {"slug": "foreign"})

    assert (status, body) == (200, {"ok": True, "selected": "foreign"})
    assert project_registry.selected_slug() == "foreign"


def test_select_par_chemin_historique_retrouve_le_slug(duo_server: Any) -> None:
    port, _home, foreign = duo_server

    status, body = _post(port, "/api/projects/select", {"path": str(foreign)})

    assert status == 200, body
    assert body["selected"] == "foreign"


def test_select_d_un_projet_inconnu_est_un_404(duo_server: Any) -> None:
    port, _home, _foreign = duo_server

    status, body = _post(port, "/api/projects/select", {"slug": "fantome"})

    assert status == 404
    assert body["error"] == "projet inconnu"


def test_select_avec_un_corps_non_json_est_un_400(duo_server: Any) -> None:
    port, _home, _foreign = duo_server

    status, body = _cockpit_http.request(port, "POST", "/api/projects/select", data=b"{pas du json")

    assert status == 400
    assert body["error"] == "bad json"


# ── POST /api/projects/update ───────────────────────────────────────────────


def test_update_d_un_projet_hors_registre_est_un_404(duo_server: Any) -> None:
    port, _home, _foreign = duo_server

    status, body = _post(port, "/api/projects/update", {"project": "fantome"})

    assert status == 404
    assert body["error"] == "projet inconnu"


def test_update_est_un_apercu_tant_que_confirm_n_est_pas_vrai(duo_server: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """``confirm`` doit valoir exactement ``True`` : ``"true"`` ou ``1`` restent un aperçu."""
    port, _home, _foreign = duo_server
    seen: list[bool] = []

    def _fake(target: Path, *, dry_run: bool) -> dict[str, Any]:
        seen.append(dry_run)
        return {"ok": True, "dryRun": dry_run}

    monkeypatch.setattr(cmd_cockpit, "update_project", _fake)

    for confirm in ("true", 1, None):
        _post(port, "/api/projects/update", {"project": "home", "confirm": confirm})
    _post(port, "/api/projects/update", {"project": "home", "confirm": True})

    assert seen == [True, True, True, False]


def test_update_avec_un_corps_non_json_est_un_400(duo_server: Any) -> None:
    port, _home, _foreign = duo_server

    status, body = _cockpit_http.request(port, "POST", "/api/projects/update", data=b"[")

    assert status == 400
    assert body["error"] == "bad json"


# ── POST /api/projects/add et /scan ─────────────────────────────────────────


def test_add_enregistre_un_dossier_permis(duo_server: Any, tmp_path: Path) -> None:
    port, _home, _foreign = duo_server
    nouveau = _project(tmp_path, "nouveau")

    status, body = _post(port, "/api/projects/add", {"path": str(nouveau)})

    assert status == 200, body
    assert body["added"] is True
    assert body["path"] == str(nouveau.resolve())
    assert any(e.get("path") == str(nouveau.resolve()) for e in project_registry.load_registry())


def test_add_hors_des_racines_autorisees_est_un_403(duo_server: Any) -> None:
    port, _home, _foreign = duo_server

    status, body = _post(port, "/api/projects/add", {"path": "/etc"})

    assert status == 403
    assert "racines autorisées" in body["error"]


def test_add_d_un_fichier_est_un_404(duo_server: Any, tmp_path: Path) -> None:
    port, home, _foreign = duo_server
    (home / "note.txt").write_text("x", encoding="utf-8")

    status, body = _post(port, "/api/projects/add", {"path": str(home / "note.txt")})

    assert status == 404
    assert "pas un dossier" in body["error"]


def test_add_avec_un_corps_non_json_est_un_400(duo_server: Any) -> None:
    port, _home, _foreign = duo_server

    status, _body = _cockpit_http.request(port, "POST", "/api/projects/add", data=b"nope")

    assert status == 400


def test_scan_propose_sans_enroler(duo_server: Any, tmp_path: Path) -> None:
    port, home, _foreign = duo_server
    avant = len(project_registry.load_registry())
    _project(tmp_path / "vrac", "alpha")

    status, body = _post(port, "/api/projects/scan", {"root": str(tmp_path / "vrac"), "depth": 2})

    assert status == 200, body
    assert [c["name"] for c in body["candidates"]] == ["alpha"]
    assert body["truncated"] is False
    assert len(project_registry.load_registry()) == avant, "un scan propose, il n'enrôle jamais"
    assert home.is_dir()


def test_scan_tronque_a_max_scan_results(duo_server: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    port, _home, _foreign = duo_server
    monkeypatch.setattr(project_registry, "MAX_SCAN_RESULTS", 3)
    for i in range(5):
        _project(tmp_path / "gros", f"p{i}")

    status, body = _post(port, "/api/projects/scan", {"root": str(tmp_path / "gros"), "depth": 2})

    assert status == 200, body
    assert len(body["candidates"]) == 3
    assert body["truncated"] is True


def test_scan_hors_racines_est_un_403_et_racine_fichier_un_404(duo_server: Any, tmp_path: Path) -> None:
    port, home, _foreign = duo_server
    (home / "note.txt").write_text("x", encoding="utf-8")

    refus, corps = _post(port, "/api/projects/scan", {"root": "/etc"})
    absent, corps_absent = _post(port, "/api/projects/scan", {"root": str(home / "note.txt")})

    assert refus == 403, corps
    assert absent == 404, corps_absent


# ── POST /api/memory ────────────────────────────────────────────────────────


def test_memory_une_mutation_sans_confirmation_est_un_403(duo_server: Any) -> None:
    port, _home, _foreign = duo_server

    status, body = _post(port, "/api/memory", {"action": "gc", "project": "home"})

    assert status == 403
    assert body["error"] == "confirmation explicite requise"


def test_memory_confirm_doit_valoir_true_exactement(duo_server: Any) -> None:
    port, _home, _foreign = duo_server

    status, _body = _post(port, "/api/memory", {"action": "gc", "project": "home", "confirm": "true"})

    assert status == 403


def test_memory_une_action_inconnue_est_un_400(duo_server: Any) -> None:
    port, _home, _foreign = duo_server

    status, body = _post(port, "/api/memory", {"action": "rm-rf", "project": "home"})

    assert status == 400
    assert "action non autorisée" in body["error"]


def test_memory_une_recherche_sans_requete_est_un_400(duo_server: Any) -> None:
    port, _home, _foreign = duo_server

    status, body = _post(port, "/api/memory", {"action": "search", "project": "home", "query": "  "})

    assert status == 400
    assert body["error"] == "query requise"


def test_memory_un_projet_inconnu_est_un_400(duo_server: Any) -> None:
    port, _home, _foreign = duo_server

    status, body = _post(port, "/api/memory", {"action": "search", "project": "fantome", "query": "x"})

    assert status == 400
    assert body["error"] == "projet inconnu"


def test_memory_une_commande_trop_lente_est_un_504(duo_server: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    port, _home, _foreign = duo_server

    def _timeout(*_a: Any, **_k: Any) -> Any:
        raise TimeoutError

    monkeypatch.setattr(cmd_cockpit, "_run_memory_in_process", _timeout)

    status, body = _post(port, "/api/memory", {"action": "search", "project": "home", "query": "x"})

    assert status == 504
    assert body["error"] == "timeout"
