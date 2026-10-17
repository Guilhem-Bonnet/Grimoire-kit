"""L0 — sélection de projet et API de lecture multi-projets du cockpit.

Sous ``tests/unit/`` parce que c'est le périmètre mesuré par la couverture en
CI (``pytest tests/unit/ tests/test_agentic_standard.py --cov``) : y laisser ces
tests ailleurs ferait passer du code éprouvé pour du code non couvert.
"""

from __future__ import annotations

import json
import threading
import time
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

    def _fake_invoke(proot: Path, argv: list[str]) -> Any:
        calls.append(argv)
        return cmd_cockpit._CliResult(0, "{}", "")

    monkeypatch.setattr(cmd_cockpit, "_invoke_memory_cli", _fake_invoke)
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

    def _fake_invoke(proot: Path, argv: list[str]) -> Any:
        captured["argv"] = argv
        return cmd_cockpit._CliResult(0, "{}", "")

    monkeypatch.setattr(cmd_cockpit, "_invoke_memory_cli", _fake_invoke)
    _post_json(
        solo_server, "/api/memory", {"action": "search", "project": "served", "query": "mémoire"}
    )
    assert captured["argv"][-2:] == ["--", "mémoire"]


def test_api_memory_spawns_no_subprocess(
    solo_server: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CodeQL #223 : la requête n'atteint plus aucun sous-processus."""

    def _boom(*a: Any, **kw: Any) -> Any:
        raise AssertionError("sous-processus lancé par /api/memory")

    monkeypatch.setattr(cmd_cockpit.subprocess, "run", _boom)
    monkeypatch.setattr(cmd_cockpit.subprocess, "Popen", _boom)
    before = Path.cwd()
    status, body = _post_json(
        solo_server, "/api/memory", {"action": "search", "project": "served", "query": "mémoire"}
    )
    assert status == 200
    assert set(body) == {"ok", "code", "stdout", "stderr", "action", "mutation"}
    assert body["action"] == "search" and body["mutation"] is False
    # Même résultat que la CLI lancée directement dans le projet servi.
    served = tmp_path / "served"
    expected = cmd_cockpit._invoke_memory_cli(served, ["--output", "json", "memory", "search", "--", "mémoire"])
    assert (body["code"], body["stdout"]) == (expected.returncode, expected.stdout)
    assert Path.cwd() == before


def test_api_memory_never_changes_process_cwd(
    solo_server: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*a: Any, **kw: Any) -> None:
        raise AssertionError("os.chdir appelé par /api/memory")

    monkeypatch.setattr(cmd_cockpit.os, "chdir", _boom)
    monkeypatch.setattr("os.chdir", _boom)
    before = Path.cwd()
    status, _body = _post_json(
        solo_server, "/api/memory", {"action": "search", "project": "served", "query": "x"}
    )
    assert status == 200
    assert Path.cwd() == before


def test_api_memory_concurrent_projects_each_get_their_own(
    duo_server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deux requêtes simultanées sur deux projets : chacune lit SON projet."""
    from grimoire.cli import cmd_memory

    port, alpha, beta = duo_server
    for proj in (alpha, beta):
        (proj / "project-context.yaml").write_text("x: 1\n", encoding="utf-8")

    class _Entry:
        def __init__(self, text: str) -> None:
            self.text = text

        def to_dict(self) -> dict[str, str]:
            return {"text": self.text}

    class _Mgr:
        prefers_hybrid = False

        def __init__(self, root: Path) -> None:
            self.root = root

        @classmethod
        def from_config(cls, cfg: Any, project_root: Path) -> _Mgr:
            return cls(project_root)

    def _search(mgr: _Mgr, query: str, **kw: Any) -> list[_Entry]:
        time.sleep(0.2)  # laisse la requête voisine arriver
        return [_Entry(mgr.root.name)]

    monkeypatch.setattr(cmd_memory, "MemoryManager", _Mgr)
    monkeypatch.setattr(cmd_memory, "run_memory_search", _search)
    monkeypatch.setattr(cmd_memory.GrimoireConfig, "from_yaml", classmethod(lambda cls, p: object()))
    out: dict[str, Any] = {}

    def _call(slug: str) -> None:
        out[slug] = _post_json(port, "/api/memory", {"action": "search", "project": slug, "query": "q"})

    threads = [threading.Thread(target=_call, args=(slug,)) for slug in ("alpha", "beta")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    for slug in ("alpha", "beta"):
        status, body = out[slug]
        assert status == 200 and body["ok"], body
        assert json.loads(body["stdout"]) == [{"text": slug}], body


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



def test_fleet_endpoint_aggregates_health_and_memory_for_every_registered_project(
    duo_server: Any,
) -> None:
    """``/api/fleet`` remplace les ``2×N`` appels que la carte Flotte du
    cockpit (``web/workspace/spaces/piloter.js::loadFleet``) faisait un par
    un et par projet — une seule réponse, une entrée par projet du registre.
    """
    port, _alpha, _beta = duo_server
    status, body = _get_api(port, "/api/fleet")
    assert status == 200
    projects = body["projects"]
    assert {p["slug"] for p in projects} == {"alpha", "beta"}
    assert {p["name"] for p in projects} == {"Alpha", "Beta"}
    for entry in projects:
        assert "health" in entry
        assert "memory" in entry
        # Mode rapide : jamais de sonde réseau depuis cette route agrégée —
        # les deux projets n'ont pas de ``project-context.yaml``, donc jamais
        # sondés (voir memory_link._empty_status).
        assert entry["memory"]["probed"] is False
        assert entry["memory"]["stale"] is True
        # `name` + `managed` (PR perf front #541) : la Flotte du cockpit
        # (`piloter.js::renderFleet`/`watchReasons`) les lit sur `r.entry` et
        # n'a plus d'autre appel pour les chercher que `/api/fleet` lui-même
        # depuis que `loadFleet` ne fait plus qu'UN appel réseau. Ni Alpha
        # ni Beta n'ont de `_grimoire/` ni de `project-context.yaml` (juste
        # un `.git`, voir `_project()` ci-dessus) : simples dépôts git, pas
        # initialisés Grimoire.
        assert entry["managed"] is False
