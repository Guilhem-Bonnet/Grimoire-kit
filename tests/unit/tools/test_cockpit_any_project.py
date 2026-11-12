"""Lot 5 — tout projet enrôlé se pilote comme le projet d'ouverture.

Le lot 2 avait ouvert ``file/write``, ``file/override``, ``command`` et les
mutations mémoire aux autres projets, mais derrière une confirmation nommée
(``confirm_project``) et une modale du front. Décision du 2026-11-12 : un seul
cockpit, tous les projets — la confirmation disparaît, les gestes de correction
mémoire (P02) compris. Ce qui reste n'est jamais demandé à l'humain : la garde
d'écriture (Origin, ``Sec-Fetch-Site``, jeton, hôte loopback), la clé de
contrôle du mandat/pilote/fournisseurs, et le journal ``write-guard.jsonl`` qui
note le projet visé de chaque écriture.

Le registre, lui, ne doit plus se remplir de projets jetables : l'enrôlement
IMPLICITE (``serve``/``start``/``init``/``forge_server``) ignore le répertoire
temporaire, ``~/.cache``, ``~/.claude/jobs`` et les worktrees
``.git/grimoire/worktrees/``. ``grimoire cockpit add`` explicite reste possible
partout.
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
from grimoire.tools import project_registry, workspace_exec
from grimoire.tools.workspace_routes import PREFIX
from grimoire.tools.workspace_write_guard import cockpit_journal_path

from .. import _cockpit_http


def _project(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    (root / ".git").mkdir(parents=True)
    (root / "_grimoire" / "overrides").mkdir(parents=True)
    (root / "project-context.yaml").write_text(
        f"project:\n  name: {name}\nmemory:\n  backend: local\n", encoding="utf-8",
    )
    return root


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
    ouvert par ce process — c'est la cible que le lot 5 rend pilotable."""
    home, foreign = _project(tmp_path, "home"), _project(tmp_path, "foreign")
    project_registry.save_registry(
        [
            {"name": "Home", "path": str(home.resolve()), "slug": "home"},
            {"name": "Foreign", "path": str(foreign.resolve()), "slug": "foreign"},
        ]
    )
    project_registry.set_selected_slug("home")
    monkeypatch.setattr(cmd_cockpit, "_OPENING_SLUG", "home")
    monkeypatch.setattr(cmd_cockpit, "_OPENING_PATH", home.resolve())
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


# ── Écritures de fichiers et de commandes : aucun confirm_project ────────────


def test_file_write_on_another_enrolled_project_needs_no_confirmation(
    duo_server: tuple[int, Path, Path],
) -> None:
    port, home, foreign = duo_server

    status, body = _cockpit_http.post(
        port,
        f"{PREFIX}file/write?project=foreign",
        {"path": "_grimoire/overrides/note.md", "text": "chez foreign"},
    )

    assert status == 200, body
    assert (foreign / "_grimoire" / "overrides" / "note.md").read_text(encoding="utf-8") == "chez foreign"
    assert not (home / "_grimoire" / "overrides" / "note.md").exists()
    # Le journal de la garde note toujours le projet visé, sans geste nommé.
    wrote = [line for line in _journal_lines() if line.get("route") == f"{PREFIX}file/write"]
    assert wrote and wrote[-1]["project"] == "foreign"


def test_command_on_another_enrolled_project_needs_no_confirmation(
    duo_server: tuple[int, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    port, _home, foreign = duo_server
    seen: dict[str, Any] = {}

    def _fake_run(project_root: Path, argv: list[str], **kwargs: Any) -> dict[str, Any]:
        seen["root"] = project_root
        return {"ok": True, "command": "grimoire version", "argv": argv, "code": 0, "output": "0.0.0"}

    monkeypatch.setattr(workspace_exec, "run_command", _fake_run)

    status, body = _cockpit_http.post(port, f"{PREFIX}command?project=foreign", {"argv": ["version"]})

    assert status == 200, body
    assert seen["root"] == foreign


def test_override_on_another_enrolled_project_needs_no_confirmation(
    duo_server: tuple[int, Path, Path],
) -> None:
    port, home, foreign = duo_server
    rel = "_grimoire/kit/note.md"
    kit_file = foreign / rel
    kit_file.parent.mkdir(parents=True)
    kit_file.write_text("kit", encoding="utf-8")

    status, body = _cockpit_http.post(port, f"{PREFIX}file/override?project=foreign", {"path": rel})

    assert status == 200, body
    assert body["created"] is True
    assert (foreign / "_grimoire" / "overrides" / "note.md").is_file()
    assert not (home / "_grimoire" / "overrides" / "note.md").exists()


# ── Mémoire : mutation et geste P02 sur un autre projet ─────────────────────


def test_memory_mutation_on_another_enrolled_project_needs_no_confirmation(
    duo_server: tuple[int, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    port, _home, foreign = duo_server
    seen: dict[str, Any] = {}

    def _fake_invoke(project_root: Path, argv: list[str]) -> cmd_cockpit._CliResult:
        seen["root"] = project_root
        return cmd_cockpit._CliResult(0, "{}", "")

    monkeypatch.setattr(cmd_cockpit, "_invoke_memory_cli", _fake_invoke)

    status, body = _cockpit_http.post(
        port, "/api/memory", {"action": "gc", "project": "foreign", "confirm": True}
    )

    assert status == 200, body
    assert seen["root"] == foreign


def test_memory_gesture_p02_on_another_enrolled_project_is_accepted(
    duo_server: tuple[int, Path, Path],
) -> None:
    port, _home, foreign = duo_server
    from grimoire.core.config import GrimoireConfig
    from grimoire.memory.manager import MemoryManager

    mgr = MemoryManager.from_config(
        GrimoireConfig.from_yaml(foreign / "project-context.yaml"), project_root=foreign
    )
    entry_id = mgr.store("Décision d'un autre projet.", tags=("a",)).id
    ask = {
        "action": "gesture",
        "project": "foreign",
        "gesture": "tags",
        "id": entry_id,
        "params": {"tags": "a, b"},
    }

    status, preview = _cockpit_http.post(port, "/api/memory", ask)

    assert status == 200, preview
    assert preview["ok"] is True
    assert preview["changes"][0]["after"] == "a, b"


# ── Les refus qui RESTENT : résolution par le registre ─────────────────────


def test_an_unknown_slug_stays_refused_with_404(duo_server: tuple[int, Path, Path]) -> None:
    port, _home, _foreign = duo_server

    status, body = _cockpit_http.post(
        port, f"{PREFIX}file/write?project=ghost", {"path": "_grimoire/overrides/note.md", "text": "x"}
    )

    assert status == 404, body


def test_a_body_project_that_contradicts_the_url_stays_refused_with_400(
    duo_server: tuple[int, Path, Path],
) -> None:
    port, home, foreign = duo_server

    status, body = _cockpit_http.post(
        port,
        f"{PREFIX}file/write?project=foreign",
        {"path": "_grimoire/overrides/note.md", "text": "x", "project": "home"},
    )

    assert status == 400, body
    assert not (home / "_grimoire" / "overrides" / "note.md").exists()
    assert not (foreign / "_grimoire" / "overrides" / "note.md").exists()


# ── Registre : aucun enrôlement implicite d'un dossier jetable ──────────────


def _disposable_cases(tmp_path: Path) -> dict[str, Path]:
    home = Path.home()
    worktree = tmp_path / "depot" / ".git" / "grimoire" / "worktrees" / "GAO-tache"
    return {
        "cache": home / ".cache" / "projet",
        "jobs": home / ".claude" / "jobs" / "projet",
        "worktree": worktree,
    }


@pytest.mark.parametrize("case", ["cache", "jobs", "worktree"])
def test_implicit_enrolment_ignores_a_disposable_project(
    tmp_path: Path, case: str,
) -> None:
    project = _disposable_cases(tmp_path)[case]
    (project / ".git").mkdir(parents=True)

    assert project_registry.register_project(project, implicit=True) is None
    assert not any(Path(p["path"]) == project.resolve() for p in project_registry.load_registry())

    # ``grimoire cockpit add`` explicite, lui, enrôle n'importe où.
    slug = project_registry.register_project(project)
    assert slug
    assert any(Path(p["path"]) == project.resolve() for p in project_registry.load_registry())


def test_implicit_enrolment_ignores_a_directory_under_the_os_temp_root(
    tmp_path: Path,
) -> None:
    import shutil
    import tempfile

    project = Path(tempfile.gettempdir()) / "projet-jetable-any-project"
    project.mkdir(parents=True, exist_ok=True)
    try:
        assert project_registry.register_project(project, implicit=True) is None
        assert project_registry.load_registry() == []

        slug = project_registry.register_project(project)
        assert slug
        assert any(Path(p["path"]) == project.resolve() for p in project_registry.load_registry())
    finally:
        shutil.rmtree(project, ignore_errors=True)
