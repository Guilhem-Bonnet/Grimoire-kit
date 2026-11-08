"""Lot 1 et lot 2 — un seul cockpit écrit sur n'importe quel projet enrôlé.

La garde historique comparait la requête au projet d'ouverture
(``_HOME_SLUG``) : naviguer vers un autre projet du registre rendait le
cockpit en lecture seule, ce qui obligeait à ouvrir un cockpit par projet.
Le lot 1 remplace cette garde par une résolution ENRÔLÉE : le slug de
``?project=`` sert de clé au registre (``registered_root``), jamais de chemin,
et le dossier doit exister. Une écriture sans slug, un slug inconnu ou un
``project`` de corps qui contredit l'URL sont refusés.

Le lot 2 ouvre les écritures SENSIBLES — ``file/write``, ``file/override``,
``command`` et les mutations mémoire (``delete``/``gc``/``sync``) — à tout
projet enrôlé, sous confirmation NOMMÉE : le corps doit porter
``confirm_project`` égal au slug visé. Le projet d'ouverture garde son geste
historique (sans confirmation). Chaque acceptation laisse une ligne
``confirm_project`` dans le journal de la garde.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from grimoire.cli import cmd_cockpit
from grimoire.missions.service import TaskService
from grimoire.tools import project_registry, workspace_exec
from grimoire.tools.workspace_routes import PREFIX
from grimoire.tools.workspace_write_guard import cockpit_journal_path

from .. import _cockpit_http

TASK_ADD = f"{PREFIX}tasks/add"
SENSITIVE_ROUTES = (
    f"{PREFIX}file/write",
    f"{PREFIX}file/override",
    f"{PREFIX}command",
)


def _project(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    (root / ".git").mkdir(parents=True)
    (root / "_grimoire" / "overrides").mkdir(parents=True)
    return root


def _kit_file(root: Path, name: str = "note.md") -> str:
    """Un fichier de l'étage kit, source possible d'un override."""
    rel = f"_grimoire/kit/{name}"
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("kit", encoding="utf-8")
    return rel


def _tasks(root: Path) -> list[Any]:
    ledger_dir = root / "_grimoire-runtime-output" / "ledger"
    if not ledger_dir.is_dir():
        return []
    return TaskService(root.resolve()).ledger.list_tasks()


def _body(route: str, root: Path) -> dict[str, Any]:
    """Un corps valide pour la route sensible, sans ``confirm_project``."""
    if route == f"{PREFIX}file/write":
        return {"path": "_grimoire/overrides/note.md", "text": "x"}
    if route == f"{PREFIX}file/override":
        return {"path": _kit_file(root)}
    return {"argv": ["version"]}


def _journal_lines() -> list[dict[str, Any]]:
    path = cockpit_journal_path()
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


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


# ── Lot 2 : écritures sensibles sur un autre projet, sur confirmation nommée ──


@pytest.mark.parametrize("route", SENSITIVE_ROUTES)
def test_sans_confirm_project_une_ecriture_sensible_sur_un_autre_projet_est_refusee(
    duo_server: tuple[int, Path, Path], route: str
) -> None:
    port, _home, foreign = duo_server

    status, payload = _cockpit_http.post(port, f"{route}?project=foreign", _body(route, foreign))

    assert status == 403, payload
    assert "confirm_project" in payload["error"]
    assert not (foreign / "_grimoire" / "overrides" / "note.md").exists()


@pytest.mark.parametrize("route", SENSITIVE_ROUTES)
def test_une_confirmation_d_un_autre_slug_est_refusee(duo_server: tuple[int, Path, Path], route: str) -> None:
    port, _home, foreign = duo_server

    status, payload = _cockpit_http.post(
        port, f"{route}?project=foreign", {**_body(route, foreign), "confirm_project": "home"}
    )

    assert status == 403, payload
    assert "confirm_project" in payload["error"]
    assert not (foreign / "_grimoire" / "overrides" / "note.md").exists()


def test_une_bonne_confirmation_ecrit_le_fichier_sur_la_bonne_racine(
    duo_server: tuple[int, Path, Path],
) -> None:
    port, home, foreign = duo_server

    status, body = _cockpit_http.post(
        port,
        f"{PREFIX}file/write?project=foreign",
        {"path": "_grimoire/overrides/note.md", "text": "chez foreign", "confirm_project": "foreign"},
    )

    assert status == 200, body
    assert (foreign / "_grimoire" / "overrides" / "note.md").read_text(encoding="utf-8") == "chez foreign"
    assert not (home / "_grimoire" / "overrides" / "note.md").exists()

    gestures = [line for line in _journal_lines() if line.get("gesture") == "confirm_project"]
    assert gestures, "le journal de la garde doit garder la confirmation"
    assert gestures[-1]["project"] == "foreign"
    assert gestures[-1]["confirm_project"] == "foreign"


def test_une_bonne_confirmation_cree_l_override_sur_la_bonne_racine(
    duo_server: tuple[int, Path, Path],
) -> None:
    port, home, foreign = duo_server
    rel = _kit_file(foreign)

    status, body = _cockpit_http.post(
        port,
        f"{PREFIX}file/override?project=foreign",
        {"path": rel, "confirm_project": "foreign"},
    )

    assert status == 200, body
    assert body["created"] is True
    assert (foreign / "_grimoire" / "overrides" / "note.md").is_file()
    assert not (home / "_grimoire" / "overrides" / "note.md").exists()


def test_une_bonne_confirmation_lance_la_commande_dans_la_bonne_racine(
    duo_server: tuple[int, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """La liste blanche et le délai restent ceux de ``workspace_exec`` ; on
    capture seulement la racine reçue, pour prouver que la route l'honore."""
    port, _home, foreign = duo_server
    seen: dict[str, Any] = {}

    def _fake_run(project_root: Path, argv: list[str], **kwargs: Any) -> dict[str, Any]:
        seen["root"] = project_root
        seen["argv"] = argv
        return {"ok": True, "command": "grimoire version", "argv": argv, "code": 0, "output": "0.0.0"}

    monkeypatch.setattr(workspace_exec, "run_command", _fake_run)

    status, body = _cockpit_http.post(
        port,
        f"{PREFIX}command?project=foreign",
        {"argv": ["version"], "confirm_project": "foreign"},
    )

    assert status == 200, body
    assert seen["root"] == foreign
    assert seen["argv"] == ["version"]


@pytest.mark.parametrize("path", ["../evade.md", "/tmp/evade.md", "_grimoire/overrides/../../evade.md"])
def test_la_traversee_de_chemin_reste_refusee_meme_confirmee(
    duo_server: tuple[int, Path, Path], tmp_path: Path, path: str
) -> None:
    port, _home, _foreign = duo_server

    status, body = _cockpit_http.post(
        port,
        f"{PREFIX}file/write?project=foreign",
        {"path": path, "text": "x", "confirm_project": "foreign"},
    )

    assert status == 403, body
    assert not (tmp_path / "evade.md").exists()
    assert not Path("/tmp/evade.md").exists()


def test_le_projet_d_ouverture_garde_son_geste_sans_confirmation(
    duo_server: tuple[int, Path, Path],
) -> None:
    """La confirmation nommée est la condition pour un AUTRE projet ; le
    projet d'ouverture garde le geste historique (aucune régression)."""
    port, home, _foreign = duo_server

    status, body = _cockpit_http.post(
        port,
        f"{PREFIX}file/write?project=home",
        {"path": "_grimoire/overrides/note.md", "text": "chez home"},
    )

    assert status == 200, body
    assert (home / "_grimoire" / "overrides" / "note.md").read_text(encoding="utf-8") == "chez home"


# ── Lot 2 : mutations mémoire (delete/gc/sync) sur un autre projet ──────────


MEMORY_MUTATIONS = (
    ("gc", {}),
    ("sync", {}),
    ("delete", {"id": "dec-01"}),
)


@pytest.mark.parametrize(("action", "extra"), MEMORY_MUTATIONS)
def test_une_mutation_memoire_sans_confirmation_nommee_est_refusee(
    duo_server: tuple[int, Path, Path], action: str, extra: dict[str, Any]
) -> None:
    port, _home, _foreign = duo_server

    status, body = _cockpit_http.post(
        port, "/api/memory", {"action": action, "project": "foreign", "confirm": True, **extra}
    )

    assert status == 403, body
    assert body["error"] == "hôte en lecture seule"


@pytest.mark.parametrize(("action", "extra"), MEMORY_MUTATIONS)
def test_une_mutation_memoire_avec_confirmation_nommee_vise_la_bonne_racine(
    duo_server: tuple[int, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    extra: dict[str, Any],
) -> None:
    port, _home, foreign = duo_server
    seen: dict[str, Any] = {}

    def _fake_invoke(project_root: Path, argv: list[str]) -> cmd_cockpit._CliResult:
        seen["root"] = project_root
        seen["argv"] = argv
        return cmd_cockpit._CliResult(0, "{}", "")

    monkeypatch.setattr(cmd_cockpit, "_invoke_memory_cli", _fake_invoke)

    status, body = _cockpit_http.post(
        port,
        "/api/memory",
        {
            "action": action,
            "project": "foreign",
            "confirm": True,
            "confirm_project": "foreign",
            **extra,
        },
    )

    assert status == 200, body
    assert body["ok"] is True
    assert seen["root"] == foreign

    gestures = [line for line in _journal_lines() if line.get("gesture") == "confirm_project"]
    assert gestures[-1]["project"] == "foreign"
    assert gestures[-1]["confirm_project"] == "foreign"


def test_une_mutation_memoire_avec_un_mauvais_slug_reste_refusee(
    duo_server: tuple[int, Path, Path],
) -> None:
    port, _home, _foreign = duo_server

    status, body = _cockpit_http.post(
        port,
        "/api/memory",
        {"action": "gc", "project": "foreign", "confirm": True, "confirm_project": "home"},
    )

    assert status == 403, body


def test_un_geste_memoire_reste_reserve_au_projet_d_ouverture(
    duo_server: tuple[int, Path, Path],
) -> None:
    """Le lot 2 ouvre delete/gc/sync ; les gestes de correction gardent leur
    contrat d'aperçu/application, réservé au projet servi (P02)."""
    port, _home, _foreign = duo_server

    status, body = _cockpit_http.post(
        port,
        "/api/memory",
        {
            "action": "gesture",
            "project": "foreign",
            "gesture": "tags",
            "id": "dec-01",
            "params": {"tags": "b"},
            "confirm_project": "foreign",
        },
    )

    assert status == 403, body
    assert body["error"] == "hôte en lecture seule"


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
