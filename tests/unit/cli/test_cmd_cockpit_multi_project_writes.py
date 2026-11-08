"""Lot 1 — un seul cockpit écrit sur n'importe quel projet enrôlé.

La garde historique comparait la requête au projet d'ouverture
(``_HOME_SLUG``) : naviguer vers un autre projet du registre rendait le
cockpit en lecture seule, ce qui obligeait à ouvrir un cockpit par projet.
Le lot 1 remplace cette garde par une résolution ENRÔLÉE : le slug de
``?project=`` sert de clé au registre (``registered_root``), jamais de chemin,
et le dossier doit exister. Une écriture sans slug, un slug inconnu ou un
``project`` de corps qui contredit l'URL sont refusés.

Restent hors de ce lot (lot 2), donc limitées au projet d'ouverture :
``file/write``, ``file/override`` et ``command``.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from grimoire.cli import cmd_cockpit
from grimoire.missions.service import TaskService
from grimoire.tools import project_registry
from grimoire.tools.workspace_routes import PREFIX

from .. import _cockpit_http

TASK_ADD = f"{PREFIX}tasks/add"


def _project(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    (root / ".git").mkdir(parents=True)
    (root / "_grimoire" / "overrides").mkdir(parents=True)
    return root


def _tasks(root: Path) -> list[Any]:
    ledger_dir = root / "_grimoire-runtime-output" / "ledger"
    if not ledger_dir.is_dir():
        return []
    return TaskService(root.resolve()).ledger.list_tasks()


@pytest.fixture(autouse=True)
def _cockpit_home_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "ck"))


@pytest.fixture
def duo_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[int, Path, Path]]:
    """``home`` est le projet d'ouverture ; ``foreign`` est enrôlé mais jamais
    ouvert par ce process — c'est exactement la cible que le lot 1 doit
    rendre inscriptible."""
    home, foreign = _project(tmp_path, "home"), _project(tmp_path, "foreign")
    project_registry.save_registry(
        [
            {"name": "Home", "path": str(home.resolve()), "slug": "home"},
            {"name": "Foreign", "path": str(foreign.resolve()), "slug": "foreign"},
        ]
    )
    project_registry.set_selected_slug("home")
    monkeypatch.setattr(cmd_cockpit, "_HOME_SLUG", "home")
    monkeypatch.setattr(cmd_cockpit, "_HOME_PATH", home.resolve())
    cmd_cockpit._API_CACHE.clear()
    httpd = cmd_cockpit.ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(cmd_cockpit._CockpitHandler, directory=str(tmp_path))
    )
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield httpd.server_address[1], home.resolve(), foreign.resolve()
    finally:
        httpd.shutdown()
        httpd.server_close()
        cmd_cockpit._API_CACHE.clear()


# ── Une écriture sur un autre projet enrôlé est acceptée et bien routée ────


def test_a_write_on_another_enrolled_project_lands_on_that_root(
    duo_server: tuple[int, Path, Path],
) -> None:
    port, home, foreign = duo_server

    status, body = _cockpit_http.post(
        port, f"{TASK_ADD}?project=foreign", {"title": "Depuis la flotte", "acceptance": ["visible"]}
    )

    assert status == 200, body
    assert [t.title for t in _tasks(foreign)] == ["Depuis la flotte"]
    assert _tasks(home) == [], "l'écriture ne doit pas retomber sur le projet d'ouverture"


def test_a_write_without_a_slug_is_refused_without_falling_back_to_the_selection(
    duo_server: tuple[int, Path, Path],
) -> None:
    """``selected_slug()`` vaut ``home`` (état partagé entre onglets) : une
    écriture sans ``?project=`` ne doit PAS s'y replier."""
    port, home, foreign = duo_server

    status, body = _cockpit_http.post(port, TASK_ADD, {"title": "Sans cible", "acceptance": ["aucune"]})

    assert status == 403, body
    assert "lecture seule" in body["error"].lower()
    assert _tasks(home) == [] and _tasks(foreign) == []


def test_a_write_on_an_unknown_slug_is_refused(duo_server: tuple[int, Path, Path]) -> None:
    port, home, foreign = duo_server

    status, body = _cockpit_http.post(port, f"{TASK_ADD}?project=ghost", {"title": "Fantôme", "acceptance": ["aucune"]})

    assert status == 403, body
    assert _tasks(home) == [] and _tasks(foreign) == []


def test_a_body_project_that_disagrees_with_the_url_is_refused(
    duo_server: tuple[int, Path, Path],
) -> None:
    """Même contrat que les routes ``portfolio`` : ``?project=`` résout la
    racine, le ``project`` du corps doit la confirmer."""
    port, home, foreign = duo_server

    status, body = _cockpit_http.post(
        port,
        f"{TASK_ADD}?project=foreign",
        {"project": "home", "title": "Mauvais projet", "acceptance": ["aucune"]},
    )

    assert status == 403, body
    assert "ne correspond pas" in body["error"]
    assert _tasks(home) == [] and _tasks(foreign) == []


# ── Lot 2 : file/write, file/override et command restent au projet d'ouverture ──


@pytest.mark.parametrize(
    ("route", "body"),
    [
        (f"{PREFIX}file/write", {"path": "_grimoire/overrides/note.md", "text": "x"}),
        (f"{PREFIX}file/override", {"path": "_grimoire/overrides/note.md"}),
        (f"{PREFIX}command", {"argv": ["version"]}),
    ],
)
def test_lot_2_writes_stay_on_the_opening_project(
    duo_server: tuple[int, Path, Path], route: str, body: dict[str, Any]
) -> None:
    port, _home, foreign = duo_server

    status, payload = _cockpit_http.post(port, f"{route}?project=foreign", body)

    assert status == 403, payload
    assert "lecture seule" in payload["error"].lower()
    assert not (foreign / "_grimoire" / "overrides" / "note.md").exists()

    # Le projet d'ouverture, lui, garde la capacité : la garde ne le refuse
    # pas (la route peut ensuite refuser pour sa propre raison métier).
    status_home, payload_home = _cockpit_http.post(port, f"{route}?project=home", body)
    assert not (status_home == 403 and "lecture seule" in payload_home["error"].lower()), payload_home


# ── La lecture reste inchangée ──────────────────────────────────────────────


def test_reads_still_work_on_any_registered_project(duo_server: tuple[int, Path, Path]) -> None:
    port, _home, _foreign = duo_server

    status, body = _cockpit_http.get(port, f"{PREFIX}tasks?project=foreign")

    assert status == 200, body
    assert body["count"] == 0


def test_reads_on_an_unknown_slug_stay_a_404(duo_server: tuple[int, Path, Path]) -> None:
    port, _home, _foreign = duo_server

    status, _body = _cockpit_http.get(port, f"{PREFIX}tasks?project=ghost")

    assert status == 404
