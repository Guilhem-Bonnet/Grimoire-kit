"""``POST /api/memory`` avec ``action: "gesture"`` sur un vrai cockpit (P34).

La correction directe d'une entrée passe par la route de gouvernance existante
— aucune nouvelle route — donc par les mêmes gardes : seul le projet servi
accepte un geste (P02), même pour un aperçu ; l'application exige ``confirm``.
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


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "ck"))
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)


def _project(root: Path, name: str) -> Path:
    root.mkdir(parents=True)
    (root / "project-context.yaml").write_text(
        f"project:\n  name: {name}\nmemory:\n  backend: local\n", encoding="utf-8",
    )
    return root


def _store(root: Path, text: str) -> str:
    from grimoire.core.config import GrimoireConfig
    from grimoire.memory.manager import MemoryManager

    mgr = MemoryManager.from_config(GrimoireConfig.from_yaml(root / "project-context.yaml"), project_root=root)
    return mgr.store(text, tags=("a",)).id


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    home, other = _project(tmp_path / "home", "home"), _project(tmp_path / "other", "other")
    project_registry.save_registry([
        {"name": "Home", "path": str(home), "slug": "home"},
        {"name": "Other", "path": str(other), "slug": "other"},
    ])
    monkeypatch.setattr(cmd_cockpit, "_HOME_SLUG", "home")
    monkeypatch.setattr(cmd_cockpit, "_HOME_PATH", home.resolve())
    httpd = cmd_cockpit.ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(cmd_cockpit._CockpitHandler, directory=str(tmp_path))
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1], home, other
    httpd.shutdown()
    httpd.server_close()


def test_preview_then_apply_on_the_served_project(server: Any) -> None:
    port, home, _ = server
    eid = _store(home, "Décision du projet servi.")
    ask = {"action": "gesture", "project": "home", "gesture": "tags", "id": eid, "params": {"tags": "a, b"}}
    status, preview = _cockpit_http.post(port, "/api/memory", ask)
    assert status == 200 and preview["ok"] is True
    assert preview["changes"][0]["before"] == "a" and preview["changes"][0]["after"] == "a, b"

    status, refused = _cockpit_http.post(port, "/api/memory", {**ask, "mode": "apply", "version": preview["version"]})
    assert status == 403 and "confirmation" in refused["error"]

    status, done = _cockpit_http.post(
        port, "/api/memory", {**ask, "mode": "apply", "version": preview["version"], "confirm": True},
    )
    assert status == 200 and done["message"] == "Étiquettes corrigées."


def test_another_project_is_read_only_even_for_a_preview(server: Any) -> None:
    port, _, other = server
    eid = _store(other, "Décision d'un autre projet.")
    status, body = _cockpit_http.post(port, "/api/memory", {
        "action": "gesture", "project": "other", "gesture": "tags", "id": eid, "params": {"tags": "b"},
    })
    assert status == 403 and body["error"] == "hôte en lecture seule"
