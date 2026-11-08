"""Lot 4 — un projet par onglet : chaque lecture nomme son projet.

Le projet affiché ne vient plus d'un état global : ``?project=<slug>`` sert de
clé au registre pour CETTE requête, et deux lectures sur deux slugs rendent
chacune les données de leur projet, sans toucher la sélection du serveur.
Sans paramètre, le repli historique (sélection courante, sinon primaire) reste
en place pour les liens anciens ; un slug inconnu reste un 404.

Le chat de l'orchestrateur suit la même cible — sa session vit dans les
fichiers du projet visé, donc celle de l'onglet.

La moitié navigateur du contrat (``sessionStorage`` dans
``web/workspace/api.js``) n'est pas pilotable depuis pytest : ce fichier prouve
la moitié serveur et le routage du chat sur un vrai handler.
"""

from __future__ import annotations

import threading
from collections.abc import Generator, Iterator
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from grimoire.cli import cmd_cockpit
from grimoire.missions.service import TaskService
from grimoire.tools import project_registry, workspace_read_cache, workspace_routes

from .._cockpit_http import get


@pytest.fixture(autouse=True)
def _cockpit_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Un état de cockpit isolé : la sélection globale ne fuit entre tests."""
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "ck"))


def _project(tmp_path: Path, name: str) -> Path:
    """Un projet enrôlable dont le ledger porte une tâche reconnaissable."""
    root = tmp_path / name
    (root / ".git").mkdir(parents=True)
    TaskService(root).add(f"Tâche de {name}", ("critère",), actor="t", owner="guilhem")
    return root


@pytest.fixture
def two_tabs(tmp_path: Path) -> Iterator[tuple[int, Path, Path]]:
    """Deux projets enrôlés, servis par le cockpit — donc deux onglets."""
    alpha, beta = _project(tmp_path, "alpha"), _project(tmp_path, "beta")
    project_registry.save_registry(
        [
            {"name": "Alpha", "path": str(alpha.resolve()), "slug": "alpha"},
            {"name": "Beta", "path": str(beta.resolve()), "slug": "beta"},
        ]
    )
    cmd_cockpit._API_CACHE.clear()
    workspace_read_cache.clear()
    httpd = cmd_cockpit.ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(cmd_cockpit._CockpitHandler, directory=str(tmp_path))
    )
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield httpd.server_address[1], alpha.resolve(), beta.resolve()
    finally:
        httpd.shutdown()
        httpd.server_close()
        cmd_cockpit._API_CACHE.clear()
        workspace_read_cache.clear()


def test_two_tabs_read_their_own_project_without_touching_the_selection(
    two_tabs: tuple[int, Path, Path],
) -> None:
    """La lecture nommée ne dépend pas d'un état partagé — et n'y touche pas.

    Les deux ordres sont joués : aucune lecture ne laisse le projet précédent
    en place pour la suivante (cache ou sélection ne fuient pas d'un onglet à
    l'autre).
    """
    port, _alpha, _beta = two_tabs
    before = project_registry.read_state()

    for first, second in (("alpha", "beta"), ("beta", "alpha")):
        status_first, body_first = get(port, f"/api/workspace/tasks?project={first}&annotations=0")
        status_second, body_second = get(port, f"/api/workspace/tasks?project={second}&annotations=0")

        assert (status_first, status_second) == (200, 200)
        assert [t["title"] for t in body_first["tasks"]] == [f"Tâche de {first}"]
        assert [t["title"] for t in body_second["tasks"]] == [f"Tâche de {second}"]

    assert project_registry.read_state() == before, "une lecture ne change pas la sélection"


def test_a_read_without_project_keeps_the_current_fallback(
    two_tabs: tuple[int, Path, Path],
) -> None:
    """Rétrocompatibilité : un lien sans ``?project=`` lit la sélection courante."""
    port, _alpha, _beta = two_tabs
    assert project_registry.set_selected_slug("beta")

    status, body = get(port, "/api/workspace/tasks?annotations=0")

    assert status == 200, body
    assert [t["title"] for t in body["tasks"]] == ["Tâche de beta"]
    assert project_registry.read_state()["selected_project"] == "beta"


def test_an_unknown_slug_is_refused(two_tabs: tuple[int, Path, Path]) -> None:
    """Un slug absent du registre ne retombe jamais sur un autre projet."""
    port, _alpha, _beta = two_tabs

    status, body = get(port, "/api/workspace/tasks?project=ghost")

    assert status == 404, body
    assert body["error"] == "projet inconnu"


def test_the_chat_stream_follows_the_tab_project(
    two_tabs: tuple[int, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le chat est rattaché au slug de l'onglet, pas à la sélection globale.

    La conversation n'est pas ouverte pour de vrai : on vérifie la racine que
    l'hôte passe au flux, celle dont dépendent les fichiers de session.
    """
    port, _alpha, beta = two_tabs
    assert project_registry.set_selected_slug("alpha")
    seen: dict[str, Path] = {}

    def _fake_stream(project_root: Path, _query: Any) -> Generator[bytes, None, None]:
        seen["root"] = project_root
        yield b""

    monkeypatch.setattr(workspace_routes, "chat_stream", _fake_stream)

    status, _body = get(port, "/api/workspace/chat/stream?project=beta")

    assert status == 200
    assert seen["root"].resolve() == beta
