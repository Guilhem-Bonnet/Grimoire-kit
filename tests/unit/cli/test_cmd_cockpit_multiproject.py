"""L0 — sélection de projet et API de lecture multi-projets du cockpit.

Sous ``tests/unit/`` parce que c'est le périmètre mesuré par la couverture en
CI (``pytest tests/unit/ tests/test_agentic_standard.py --cov``) : y laisser ces
tests ailleurs ferait passer du code éprouvé pour du code non couvert.
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
from typer.testing import CliRunner

from grimoire.cli import cmd_cockpit
from grimoire.cli.app import app
from grimoire.tools import project_registry


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _cockpit_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "ck"
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(home))
    return home


def _project(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    (p / ".git").mkdir(parents=True)
    return p



def _get_api(port: int, path: str) -> tuple[int, Any]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _post_json(port: int, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


@pytest.fixture
def duo_server(tmp_path: Path):  # type: ignore[no-untyped-def]
    """Two registered projects, served by the cockpit handler."""
    alpha, beta = _project(tmp_path, "alpha"), _project(tmp_path, "beta")
    project_registry.save_registry([
        {"name": "Alpha", "path": str(alpha), "slug": "alpha"},
        {"name": "Beta", "path": str(beta), "slug": "beta"},
    ])
    httpd = cmd_cockpit.ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(cmd_cockpit._CockpitHandler, directory=str(tmp_path))
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1], alpha, beta
    httpd.shutdown()
    httpd.server_close()


def test_projects_endpoint_lists_registry(duo_server: Any) -> None:
    port, alpha, _beta = duo_server
    status, body = _get_api(port, "/api/projects")
    assert status == 200
    assert [p["slug"] for p in body["projects"]] == ["alpha", "beta"]
    assert body["primary"] == "alpha"
    assert body["selected"] == "alpha"
    assert next(p for p in body["projects"] if p["slug"] == "alpha")["path"] == str(alpha)
    assert all(p["exists"] for p in body["projects"])


def test_select_switches_the_served_project(duo_server: Any) -> None:
    """The acceptance criterion: selecting another project changes what is served."""
    port, alpha, beta = duo_server

    status, body = _get_api(port, "/api/status")
    assert status == 200
    assert body["projectRoot"] == str(alpha)

    assert _post_json(port, "/api/projects/select", {"slug": "beta"}) == (200, {"ok": True, "selected": "beta"})

    status, body = _get_api(port, "/api/status")
    assert status == 200
    assert body["projectRoot"] == str(beta), "la sélection doit changer le projet servi"
    assert _get_api(port, "/api/projects")[1]["selected"] == "beta"


def test_explicit_query_overrides_the_selection(duo_server: Any) -> None:
    port, alpha, beta = duo_server
    _post_json(port, "/api/projects/select", {"slug": "beta"})
    assert _get_api(port, "/api/status?project=alpha")[1]["projectRoot"] == str(alpha)
    assert _get_api(port, "/api/status")[1]["projectRoot"] == str(beta)


def test_select_accepts_a_path_for_the_legacy_ui(duo_server: Any) -> None:
    port, _alpha, beta = duo_server
    status, body = _post_json(port, "/api/projects/select", {"path": str(beta)})
    assert (status, body["selected"]) == (200, "beta")


def test_select_rejects_an_unknown_project(duo_server: Any) -> None:
    port, _alpha, _beta = duo_server
    status, body = _post_json(port, "/api/projects/select", {"slug": "ghost"})
    assert status == 404
    assert body["ok"] is False


def test_status_advertises_a_read_only_host(duo_server: Any) -> None:
    """The UI must know it is on the cockpit, so it stops offering mutations."""
    port, _alpha, _beta = duo_server
    _status, body = _get_api(port, "/api/status")
    assert body["host"] == "cockpit"
    assert body["readOnly"] is True
    assert body["project"] == "alpha"


def test_unknown_api_path_is_404_not_a_static_probe(duo_server: Any) -> None:
    port, _alpha, _beta = duo_server
    assert _get_api(port, "/api/nope")[0] == 404


def test_static_files_still_served(duo_server: Any, tmp_path: Path) -> None:
    port, _alpha, _beta = duo_server
    (tmp_path / "hello.txt").write_text("bonjour", encoding="utf-8")
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/hello.txt", timeout=5) as resp:
        assert resp.read().decode() == "bonjour"


def test_selection_falls_back_when_the_project_disappears(duo_server: Any) -> None:
    port, alpha, _beta = duo_server
    _post_json(port, "/api/projects/select", {"slug": "beta"})
    project_registry.save_registry([{"name": "Alpha", "path": str(alpha), "slug": "alpha"}])
    assert _get_api(port, "/api/status")[1]["projectRoot"] == str(alpha)


def test_prune_drops_dead_paths(runner: CliRunner, tmp_path: Path) -> None:
    alive = _project(tmp_path, "alive")
    project_registry.save_registry([
        {"name": "Alive", "path": str(alive), "slug": "alive"},
        {"name": "Gone", "path": str(tmp_path / "gone"), "slug": "gone"},
    ])
    # ``--yes`` depuis la réconciliation avec #152 : la purge demande
    # confirmation, et un CliRunner sans entrée avorterait sur le prompt.
    result = runner.invoke(app, ["cockpit", "prune", "--yes"])
    assert result.exit_code == 0
    assert [p["slug"] for p in project_registry.load_registry()] == ["alive"]


def test_prune_dry_run_changes_nothing(runner: CliRunner, tmp_path: Path) -> None:
    project_registry.save_registry([{"name": "Gone", "path": str(tmp_path / "gone"), "slug": "gone"}])
    result = runner.invoke(app, ["cockpit", "prune", "--dry-run"])
    assert result.exit_code == 0
    assert [p["slug"] for p in project_registry.load_registry()] == ["gone"]


# ── Garde argv du dispatch mémoire ───────────────────────────────────────────


class _FakeProc:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


@pytest.fixture
def solo_server(tmp_path: Path):  # type: ignore[no-untyped-def]
    proj = _project(tmp_path, "served")
    project_registry.save_registry([{"name": "Served", "path": str(proj), "slug": "served"}])
    httpd = cmd_cockpit.ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(cmd_cockpit._CockpitHandler, directory=str(tmp_path))
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def test_argv_values_cannot_pose_as_options(
    solo_server: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pas de shell ici, mais `--yes` en valeur vaudrait confirmation."""
    calls: list[list[str]] = []

    def _fake_run(cmd: list[str], **kw: Any) -> _FakeProc:
        calls.append(cmd)
        return _FakeProc(0, stdout="{}")

    monkeypatch.setattr(cmd_cockpit.subprocess, "run", _fake_run)
    for hostile in ("--help", "-x", "--yes", "a" * 600, "nul\x00byte"):
        status, body = _post_json(
            solo_server, "/api/memory", {"action": "search", "project": "served", "query": hostile}
        )
        assert (status, body["ok"]) == (400, False), hostile
    assert calls == []


def test_argv_values_are_passed_after_a_separator(
    solo_server: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def _fake_run(cmd: list[str], **kw: Any) -> _FakeProc:
        captured["cmd"] = cmd
        return _FakeProc(0, stdout="{}")

    monkeypatch.setattr(cmd_cockpit.subprocess, "run", _fake_run)
    _post_json(
        solo_server, "/api/memory", {"action": "search", "project": "served", "query": "mémoire"}
    )
    assert captured["cmd"][-2:] == ["--", "mémoire"]


def test_select_rejects_malformed_json(duo_server: Any) -> None:
    port, _alpha, _beta = duo_server
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/projects/select",
        data=b"{not json",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=5)  # noqa: S310
        raise AssertionError("aurait dû échouer")
    except urllib.error.HTTPError as exc:
        assert exc.code == 400

