"""P35 : créer une tâche et entretenir le registre depuis le cockpit.

Deux familles :

- les routes elles-mêmes (``workspace_routes._task_add`` par ``workspace_post``,
  ``cockpit_registry_routes.registry_post``) : ce qu'elles écrivent, ce
  qu'elles refusent, et que retirer ou purger ne touche jamais au dossier ;
- les mêmes écritures jouées contre un vrai serveur du cockpit : sans jeton de
  session, avec une mauvaise ``Origin``, un mauvais type de contenu ou sur un
  projet autre que le projet servi, rien n'est écrit.
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
from grimoire.tools import project_registry as reg
from grimoire.tools.cockpit_registry_routes import FOLDER_KEPT, REGISTRY_ROUTES, registry_post
from grimoire.tools.request_guard import SESSION_HEADER, session_token
from grimoire.tools.workspace_routes import POST_ROUTES, PREFIX, workspace_post

from . import _cockpit_http

TASK_ADD = f"{PREFIX}tasks/add"


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "ck"))
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)


def _project(root: Path) -> Path:
    (root / ".git").mkdir(parents=True)
    return root


def _tasks(root: Path) -> list[Any]:
    ledger_dir = root / "_grimoire-runtime-output" / "ledger"
    if not ledger_dir.is_dir():
        return []
    return TaskService(root.resolve()).ledger.list_tasks()


# ── 1. « Nouvelle tâche » : la route appelle TaskService.add ───────────────


def test_task_add_is_a_post_route() -> None:
    assert TASK_ADD in POST_ROUTES


def test_task_add_writes_title_acceptance_and_description(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    done = workspace_post(root, TASK_ADD, {
        "title": "Créer depuis le cockpit", "acceptance": ["un critère", "  ", "un autre"],
        "description": "le pourquoi",
    })
    assert done["board"] == "proposed" and done["title"] == "Créer depuis le cockpit"
    task = TaskService(root.resolve()).ledger.get_task(done["id"])
    assert task.acceptance == ("un critère", "un autre")
    assert task.description == "le pourquoi"


def test_task_add_splits_a_text_into_one_criterion_per_line(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    done = workspace_post(root, TASK_ADD, {"title": "Texte", "acceptance": "premier\n\nsecond\n"})
    assert done["acceptance"] == ["premier", "second"]


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"acceptance": ["x"]}, "titre requis"),
        ({"title": "  ", "acceptance": ["x"]}, "titre requis"),
        ({"title": "t", "acceptance": {"a": 1}}, "liste de critères"),
        ({"title": "t"}, "liste de critères"),
        ({"title": "t", "acceptance": ["  ", ""]}, "au moins un critère"),
        ({"title": "t", "acceptance": ["x"], "description": "d" * 4001}, "description trop longue"),
    ],
)
def test_task_add_refuses_an_incomplete_task_and_writes_nothing(tmp_path: Path, body: dict[str, Any], message: str) -> None:
    root = _project(tmp_path / "p")
    with pytest.raises(ValueError, match=message):
        workspace_post(root, TASK_ADD, body)
    assert _tasks(root) == []


# ── 2. Registre : ajouter, scanner, retirer, purger ─────────────────────────


@pytest.fixture
def registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Trois projets sous un HOME jetable : servi, voisin, et une entrée morte."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    served, other = _project(home / "servi"), _project(home / "voisin")
    reg.save_registry([
        {"name": "Servi", "path": str(served.resolve()), "slug": "servi"},
        {"name": "Voisin", "path": str(other.resolve()), "slug": "voisin"},
        {"name": "Disparu", "path": str(home / "disparu"), "slug": "disparu"},
    ])
    return {"home": home, "served": served.resolve(), "other": other.resolve()}


def _slugs() -> list[str]:
    return [str(p.get("slug")) for p in reg.load_registry()]


def test_registry_routes_are_the_four_gestures() -> None:
    assert {r.rsplit("/", 1)[1] for r in REGISTRY_ROUTES} == {"add", "scan", "remove", "prune"}


def test_add_registers_a_folder_then_says_it_was_already_there(registry: dict[str, Path]) -> None:
    fresh = _project(registry["home"] / "nouveau")
    status, body = registry_post("/api/projects/add", {"path": str(fresh)}, served=registry["served"])
    assert status == 200 and body["already"] is False and body["slug"] == "nouveau"
    status, again = registry_post("/api/projects/add", {"path": str(fresh)}, served=registry["served"])
    assert status == 200 and again["already"] is True and again["slug"] == "nouveau"


def test_add_refuses_a_missing_folder_and_a_path_outside_the_allowed_roots(registry: dict[str, Path], tmp_path: Path) -> None:
    status, body = registry_post("/api/projects/add", {"path": str(registry["home"] / "absent")}, served=None)
    assert status == 404 and "pas un dossier" in body["error"]
    outside = tmp_path / "ailleurs" / "x"
    outside.mkdir(parents=True)
    status, body = registry_post("/api/projects/add", {"path": str(outside)}, served=None)
    assert status == 403 and "racines autorisées" in body["error"]
    assert "x" not in _slugs()


def test_scan_proposes_without_registering(registry: dict[str, Path]) -> None:
    _project(registry["home"] / "lot" / "a-scanner")
    before = _slugs()
    status, body = registry_post("/api/projects/scan", {"root": str(registry["home"] / "lot")}, served=None)
    assert status == 200 and [c["name"] for c in body["candidates"]] == ["a-scanner"]
    assert body["candidates"][0]["registered"] is False
    assert _slugs() == before
    status, body = registry_post("/api/projects/scan", {"root": str(registry["home"]), "depth": "x"}, served=None)
    assert status == 400


def test_scan_of_a_missing_folder_is_a_404(registry: dict[str, Path]) -> None:
    status, _ = registry_post("/api/projects/scan", {"root": str(registry["home"] / "absent")}, served=None)
    assert status == 404


def test_remove_previews_then_removes_only_the_entry(registry: dict[str, Path]) -> None:
    status, preview = registry_post("/api/projects/remove", {"slug": "voisin"}, served=registry["served"])
    assert status == 200 and preview["preview"] is True and preview["entry"]["exists"] is True
    assert preview["note"] == FOLDER_KEPT and "voisin" in _slugs()

    status, done = registry_post("/api/projects/remove", {"slug": "voisin", "confirm": True}, served=registry["served"])
    assert status == 200 and done["removed"]["slug"] == "voisin" and done["remaining"] == 2
    assert "voisin" not in _slugs()
    assert registry["other"].is_dir() and (registry["other"] / ".git").is_dir()


def test_remove_forgets_the_selection_it_pointed_at(registry: dict[str, Path]) -> None:
    assert reg.set_selected_slug("voisin")
    registry_post("/api/projects/remove", {"slug": "voisin", "confirm": True}, served=registry["served"])
    assert "selected_project" not in reg.read_state()


def test_remove_refuses_the_served_project_and_an_unknown_slug(registry: dict[str, Path]) -> None:
    status, body = registry_post("/api/projects/remove", {"slug": "servi", "confirm": True}, served=registry["served"])
    assert status == 409 and "sert" in body["error"]
    status, body = registry_post("/api/projects/remove", {"slug": "fantome", "confirm": True}, served=registry["served"])
    assert status == 404
    status, _ = registry_post("/api/projects/remove", {"confirm": True}, served=registry["served"])
    assert status == 404
    assert _slugs() == ["servi", "voisin", "disparu"]


def test_prune_previews_then_removes_the_dead_entries_shown(registry: dict[str, Path]) -> None:
    status, preview = registry_post("/api/projects/prune", {}, served=registry["served"])
    assert status == 200 and preview["preview"] is True and preview["total"] == 3
    assert [c["slug"] for c in preview["candidates"]] == ["disparu"]
    assert _slugs() == ["servi", "voisin", "disparu"]

    status, done = registry_post(
        "/api/projects/prune", {"confirm": True, "slugs": ["disparu"]}, served=registry["served"],
    )
    assert status == 200 and [r["slug"] for r in done["removed"]] == ["disparu"] and done["remaining"] == 2
    assert _slugs() == ["servi", "voisin"]


def test_prune_only_removes_what_the_preview_showed(registry: dict[str, Path]) -> None:
    status, done = registry_post("/api/projects/prune", {"confirm": True, "slugs": []}, served=registry["served"])
    assert status == 200 and done["removed"] == [] and "disparu" in _slugs()


def test_prune_stale_never_drops_the_served_project(registry: dict[str, Path]) -> None:
    # `.git` est un marqueur : on le retire du servi pour qu'il paraisse « sans marqueur ».
    (registry["served"] / ".git").rmdir()
    status, preview = registry_post("/api/projects/prune", {"stale": True}, served=registry["served"])
    assert [c["slug"] for c in preview["candidates"]] == ["disparu"]
    status, _ = registry_post("/api/projects/prune", {"stale": True, "confirm": True}, served=registry["served"])
    assert status == 200 and _slugs() == ["servi", "voisin"]
    assert registry["served"].is_dir()


def test_an_unknown_registry_route_is_a_404() -> None:
    assert registry_post("/api/projects/rename", {}, served=None)[0] == 404


def test_forget_selection_keeps_a_selection_still_present(registry: dict[str, Path]) -> None:
    assert reg.set_selected_slug("voisin")
    reg.forget_selection_if_gone(reg.load_registry())
    assert reg.read_state()["selected_project"] == "voisin"
    reg.forget_selection_if_gone([])
    assert "selected_project" not in reg.read_state()
    reg.forget_selection_if_gone([])  # sans sélection : rien à faire, rien d'écrit


# ── 3. Un vrai serveur : refus sans jeton, Origin, type, autre projet ───────


@pytest.fixture
def server(registry: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    monkeypatch.setattr(cmd_cockpit, "_HOME_SLUG", "servi")
    monkeypatch.setattr(cmd_cockpit, "_HOME_PATH", registry["served"])
    cmd_cockpit._API_CACHE.clear()
    serve_dir = tmp_path / "serve"
    serve_dir.mkdir()
    httpd = cmd_cockpit.ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(cmd_cockpit._CockpitHandler, directory=str(serve_dir))
    )
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield {**registry, "port": httpd.server_address[1]}
    finally:
        httpd.shutdown()
        httpd.server_close()
        cmd_cockpit._API_CACHE.clear()


def _raw(port: int, path: str, body: dict[str, Any], headers: dict[str, str]) -> int:
    status, _ = _cockpit_http.request(port, "POST", path, data=json.dumps(body).encode(), headers=headers)
    return status


def test_the_served_cockpit_creates_a_task_and_maintains_the_registry(server: dict[str, Any]) -> None:
    port = server["port"]
    status, task = _cockpit_http.post(port, f"{TASK_ADD}?project=servi", {
        "title": "Depuis le cockpit", "acceptance": ["observable"], "description": "contexte",
    })
    assert status == 200 and task["board"] == "proposed"
    assert [t.title for t in _tasks(server["served"])] == ["Depuis le cockpit"]

    status, removed = _cockpit_http.post(port, "/api/projects/remove", {"slug": "voisin", "confirm": True})
    assert status == 200 and removed["removed"]["slug"] == "voisin"
    status, _ = _cockpit_http.post(port, "/api/projects/prune", {"confirm": True, "slugs": ["disparu"]})
    assert status == 200 and _slugs() == ["servi"]
    status, _ = _cockpit_http.post(port, "/api/projects/remove", {"slug": "servi", "confirm": True})
    assert status == 409
    status, _ = _cockpit_http.request(port, "POST", "/api/projects/remove", data=b"{pas du json", headers={})
    assert status == 400


@pytest.mark.no_session_token
@pytest.mark.parametrize("path", [f"{TASK_ADD}?project=servi", *REGISTRY_ROUTES])
def test_a_write_without_the_session_token_writes_nothing(server: dict[str, Any], path: str) -> None:
    body = {"title": "t", "acceptance": ["a"], "slug": "voisin", "confirm": True, "path": str(server["home"])}
    assert _raw(server["port"], path, body, {"Content-Type": "application/json"}) == 403
    assert _tasks(server["served"]) == []
    assert _slugs() == ["servi", "voisin", "disparu"]


@pytest.mark.no_session_token
@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"Origin": "https://evil.example"}, 403),
        ({"Origin": "null"}, 403),
        ({"Sec-Fetch-Site": "cross-site"}, 403),
        ({"Content-Type": "text/plain"}, 415),
        ({"Content-Type": "application/x-www-form-urlencoded"}, 415),
    ],
)
@pytest.mark.parametrize("path", [f"{TASK_ADD}?project=servi", "/api/projects/remove", "/api/projects/prune"])
def test_a_foreign_page_with_the_token_still_writes_nothing(
    server: dict[str, Any], path: str, headers: dict[str, str], expected: int,
) -> None:
    sent = {"Content-Type": "application/json", SESSION_HEADER: session_token(), **headers}
    body = {"title": "t", "acceptance": ["a"], "slug": "voisin", "confirm": True}
    assert _raw(server["port"], path, body, sent) == expected
    assert _tasks(server["served"]) == []
    assert _slugs() == ["servi", "voisin", "disparu"]


def test_a_task_for_another_project_than_the_served_one_is_refused(server: dict[str, Any]) -> None:
    status, body = _cockpit_http.post(
        server["port"], f"{TASK_ADD}?project=voisin", {"title": "t", "acceptance": ["a"]},
    )
    assert status == 403 and body["error"] == "hôte en lecture seule"
    assert _tasks(server["other"]) == [] and _tasks(server["served"]) == []


# ── 5. Boîte à idées (I13) : les trois routes d'écriture suivent la même garde ──────────────

IDEA_POSTS = [f"{PREFIX}ideas/add", f"{PREFIX}ideas/drop", f"{PREFIX}ideas/promote"]


def _ideas_file(root: Path) -> Path:
    return root / "_grimoire-runtime-output" / "ledger" / "ideas.jsonl"


@pytest.mark.no_session_token
@pytest.mark.parametrize("path", IDEA_POSTS)
def test_an_idea_write_without_the_session_token_writes_nothing(server: dict[str, Any], path: str) -> None:
    body = {"text": "une idée", "id": "IDEA-0001"}
    assert _raw(server["port"], f"{path}?project=servi", body, {"Content-Type": "application/json"}) == 403
    assert not _ideas_file(server["served"]).exists()


@pytest.mark.parametrize("path", IDEA_POSTS)
def test_an_idea_write_is_absent_on_a_read_only_host(server: dict[str, Any], path: str) -> None:
    status, body = _cockpit_http.post(server["port"], f"{path}?project=voisin", {"text": "une idée", "id": "IDEA-0001"})
    assert status == 403 and body["error"] == "hôte en lecture seule"
    assert not _ideas_file(server["other"]).exists() and not _ideas_file(server["served"]).exists()


def test_an_idea_write_on_the_served_project_goes_through(server: dict[str, Any]) -> None:
    status, body = _cockpit_http.post(server["port"], f"{PREFIX}ideas/add?project=servi", {"text": "une idée"})
    assert status == 200 and body["state"] == "open"
    assert _ideas_file(server["served"]).exists()
