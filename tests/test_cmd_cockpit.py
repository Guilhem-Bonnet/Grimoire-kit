"""Tests for ``grimoire cockpit`` — registry management and site sync."""

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
    def __init__(self, returncode: int, stderr: str = "", stdout: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout


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


def test_resolve_project_path(tmp_path: Path) -> None:
    proj = _project(tmp_path, "zeta")
    cmd_cockpit._save_registry([{"name": "Zeta", "path": str(proj), "slug": "zeta"}])
    assert cmd_cockpit._resolve_project_path("zeta") == proj
    assert cmd_cockpit._resolve_project_path("") == proj  # empty → first
    assert cmd_cockpit._resolve_project_path("unknown") is None


def test_register_project_helper(tmp_path: Path) -> None:
    proj = _project(tmp_path, "reg")
    assert cmd_cockpit.register_project(proj, "Reg") == "reg"
    assert cmd_cockpit.register_project(proj) is None  # idempotent
    assert cmd_cockpit.register_project(tmp_path / "nope") is None  # not a directory


def test_register_project_slug_collision(tmp_path: Path) -> None:
    a = _project(tmp_path, "a")
    b = _project(tmp_path, "b")
    cmd_cockpit.register_project(a, "Same")
    assert cmd_cockpit.register_project(b, "Same") == "same-2"


def test_init_hook_registers_project(tmp_path: Path) -> None:
    from grimoire.cli import cmd_init

    proj = _project(tmp_path, "fromsetup")
    cmd_init._maybe_register_cockpit(proj, "From Setup", "text")
    assert "from-setup" in [p["slug"] for p in cmd_cockpit._load_registry()]


def test_init_hook_opt_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.cli import cmd_init

    monkeypatch.setenv("GRIMOIRE_NO_COCKPIT", "1")
    cmd_init._maybe_register_cockpit(_project(tmp_path, "skip"), "Skip", "text")
    assert cmd_cockpit._load_registry() == []


def _post_api(port: int, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/memory",
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
def api_server(tmp_path: Path):  # type: ignore[no-untyped-def]
    proj = _project(tmp_path, "served")
    cmd_cockpit._save_registry([{"name": "Served", "path": str(proj), "slug": "served"}])
    httpd = cmd_cockpit.ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(cmd_cockpit._CockpitHandler, directory=str(tmp_path))
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def test_api_dispatches_allowlisted_action(
    api_server: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def _fake_run(cmd: list[str], **kw: Any) -> _FakeProc:
        captured["cmd"] = cmd
        captured["cwd"] = kw.get("cwd")
        return _FakeProc(0, stdout='{"backend": "qdrant"}')

    monkeypatch.setattr(cmd_cockpit.subprocess, "run", _fake_run)
    status, body = _post_api(api_server, {"action": "status", "project": "served"})
    assert status == 200
    assert body["ok"] is True
    assert "qdrant" in body["stdout"]
    assert captured["cmd"][-3:] == ["memory", "status"] or "status" in captured["cmd"]


def test_api_rejects_unknown_action(api_server: int) -> None:
    status, body = _post_api(api_server, {"action": "rm -rf", "project": "served"})
    assert status == 400
    assert body["ok"] is False


def test_api_search_requires_query(api_server: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cmd_cockpit.subprocess, "run", lambda *a, **k: _FakeProc(0))
    status, _ = _post_api(api_server, {"action": "search", "project": "served", "query": ""})
    assert status == 400


def test_api_unknown_project(api_server: int) -> None:
    status, _ = _post_api(api_server, {"action": "status", "project": "ghost"})
    assert status == 400


def test_api_mutation_requires_confirm(api_server: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cmd_cockpit.subprocess, "run", lambda *a, **k: _FakeProc(0))
    status, body = _post_api(api_server, {"action": "gc", "project": "served"})
    assert status == 403
    assert body["ok"] is False


def test_api_gc_runs_with_confirm(api_server: int, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_run(cmd: list[str], **kw: Any) -> _FakeProc:
        captured["cmd"] = cmd
        return _FakeProc(0, stdout='{"consolidated": 3}')

    monkeypatch.setattr(cmd_cockpit.subprocess, "run", _fake_run)
    status, body = _post_api(api_server, {"action": "gc", "project": "served", "confirm": True})
    assert status == 200
    assert body["ok"] is True and body["mutation"] is True
    assert captured["cmd"][-1] == "gc"


def test_api_delete_requires_id(api_server: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cmd_cockpit.subprocess, "run", lambda *a, **k: _FakeProc(0))
    status, _ = _post_api(api_server, {"action": "delete", "project": "served", "confirm": True})
    assert status == 400


def test_api_delete_dispatches_id_with_yes(api_server: int, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_run(cmd: list[str], **kw: Any) -> _FakeProc:
        captured["cmd"] = cmd
        return _FakeProc(0, stdout='{"deleted": true}')

    monkeypatch.setattr(cmd_cockpit.subprocess, "run", _fake_run)
    status, body = _post_api(
        api_server, {"action": "delete", "project": "served", "confirm": True, "id": "dec-03"}
    )
    assert status == 200
    assert body["ok"] is True
    assert captured["cmd"][-3:] == ["delete", "dec-03", "--yes"]


def test_api_404_on_other_path(api_server: int) -> None:
    req = urllib.request.Request(
        f"http://127.0.0.1:{api_server}/api/other", data=b"{}", method="POST"
    )
    try:
        urllib.request.urlopen(req, timeout=5)  # noqa: S310
        raise AssertionError("expected 404")
    except urllib.error.HTTPError as exc:
        assert exc.code == 404


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
