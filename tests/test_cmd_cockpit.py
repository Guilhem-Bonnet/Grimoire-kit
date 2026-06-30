"""Tests for ``grimoire cockpit`` — registry management and site sync."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli import cmd_cockpit
from grimoire.cli.app import app


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


def test_slug() -> None:
    assert cmd_cockpit._slug("Atlas Ops") == "atlas-ops"
    assert cmd_cockpit._slug("Grimoire/Kit!") == "grimoire-kit"
    assert cmd_cockpit._slug("///") == "project"


def test_add_and_list(runner: CliRunner, tmp_path: Path) -> None:
    proj = _project(tmp_path, "alpha")
    res = runner.invoke(app, ["cockpit", "add", str(proj), "--name", "Alpha"])
    assert res.exit_code == 0
    reg = cmd_cockpit._load_registry()
    assert reg == [{"name": "Alpha", "path": str(proj.resolve()), "slug": "alpha"}]

    res = runner.invoke(app, ["cockpit", "list"])
    assert res.exit_code == 0
    assert "alpha" in res.output


def test_add_is_idempotent(runner: CliRunner, tmp_path: Path) -> None:
    proj = _project(tmp_path, "beta")
    runner.invoke(app, ["cockpit", "add", str(proj)])
    runner.invoke(app, ["cockpit", "add", str(proj)])
    assert len(cmd_cockpit._load_registry()) == 1


def test_slug_collision_disambiguated(runner: CliRunner, tmp_path: Path) -> None:
    a = _project(tmp_path, "a")
    b = _project(tmp_path, "b")
    runner.invoke(app, ["cockpit", "add", str(a), "--name", "Same"])
    runner.invoke(app, ["cockpit", "add", str(b), "--name", "Same"])
    slugs = sorted(p["slug"] for p in cmd_cockpit._load_registry())
    assert slugs == ["same", "same-2"]


def test_add_rejects_missing_dir(runner: CliRunner, tmp_path: Path) -> None:
    res = runner.invoke(app, ["cockpit", "add", str(tmp_path / "nope")])
    assert res.exit_code == 1
    assert cmd_cockpit._load_registry() == []


def test_remove(runner: CliRunner, tmp_path: Path) -> None:
    proj = _project(tmp_path, "gamma")
    runner.invoke(app, ["cockpit", "add", str(proj), "--name", "Gamma"])
    res = runner.invoke(app, ["cockpit", "remove", "gamma"])
    assert res.exit_code == 0
    assert cmd_cockpit._load_registry() == []


class _FakeHTTPD:
    """Stand-in for ThreadingHTTPServer that exits serve_forever immediately."""

    def __init__(self, addr: tuple[str, int], handler: object) -> None:
        self.addr = addr

    def serve_forever(self) -> None:
        raise KeyboardInterrupt

    def server_close(self) -> None:
        pass


def test_refresh_empty_registry(runner: CliRunner) -> None:
    res = runner.invoke(app, ["cockpit", "refresh"])
    assert res.exit_code == 0
    assert "démo" in res.output  # bundled demo data kept as fallback


def test_serve_no_refresh_mocked(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cmd_cockpit, "ThreadingHTTPServer", _FakeHTTPD)
    monkeypatch.setattr(cmd_cockpit.webbrowser, "open", lambda *a, **k: None)
    res = runner.invoke(app, ["cockpit", "serve", "--no-open", "--no-refresh", "--port", "0"])
    assert res.exit_code == 0
    assert "Cockpit" in res.output


def test_serve_refresh_empty_registry_mocked(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cmd_cockpit, "ThreadingHTTPServer", _FakeHTTPD)
    monkeypatch.setattr(cmd_cockpit.webbrowser, "open", lambda *a, **k: None)
    res = runner.invoke(app, ["cockpit", "serve", "--no-open", "--port", "0"])
    assert res.exit_code == 0  # empty registry → no subprocess, demo fallback


def test_default_callback_invokes_serve(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cmd_cockpit, "ThreadingHTTPServer", _FakeHTTPD)
    opened: list[str] = []
    monkeypatch.setattr(cmd_cockpit.webbrowser, "open", lambda url, *a, **k: opened.append(url))
    res = runner.invoke(app, ["cockpit"])
    assert res.exit_code == 0
    assert opened and opened[0].endswith("/portfolio.html")


def test_serve_port_in_use(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(addr: object, handler: object) -> None:
        raise OSError("address already in use")

    monkeypatch.setattr(cmd_cockpit, "ThreadingHTTPServer", _boom)
    res = runner.invoke(app, ["cockpit", "serve", "--no-open", "--no-refresh", "--port", "0"])
    assert res.exit_code == 1


class _FakeProc:
    def __init__(self, returncode: int, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = ""


def test_refresh_generates_when_project_registered(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = _project(tmp_path, "delta")
    runner.invoke(app, ["cockpit", "add", str(proj)])
    monkeypatch.setattr(cmd_cockpit.subprocess, "run", lambda *a, **k: _FakeProc(0))
    res = runner.invoke(app, ["cockpit", "refresh"])
    assert res.exit_code == 0
    assert "régénérées" in res.output


def test_generate_data_warns_on_subprocess_failure(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = _project(tmp_path, "epsilon")
    runner.invoke(app, ["cockpit", "add", str(proj)])
    monkeypatch.setattr(cmd_cockpit.subprocess, "run", lambda *a, **k: _FakeProc(1, "trace\nlast error line"))
    res = runner.invoke(app, ["cockpit", "refresh"])
    assert res.exit_code == 0  # partial generation is non-fatal


def test_sync_site_seeds_demo_and_preserves_generated_data(tmp_path: Path) -> None:
    serve = tmp_path / "serve"

    # First sync: bundled site copied + data/ seeded from the demo fallback.
    cmd_cockpit._sync_site(serve)
    assert (serve / "forge-nav.js").is_file()
    assert (serve / "data" / "projects.json").is_file()

    # A later refresh owns data/ — a re-sync must NOT clobber it.
    sentinel = serve / "data" / "projects.json"
    sentinel.write_text('{"generated": true}', encoding="utf-8")
    cmd_cockpit._sync_site(serve)
    assert json.loads(sentinel.read_text(encoding="utf-8")) == {"generated": True}
