"""``grimoire mcp`` : démon HTTP partagé, état, et entrée ``.mcp.json`` (#723).

Le démon lui-même (spawn, port, kill) est simulé comme pour ``cockpit start`` ;
ce qui est vérifié ici, c'est le contrat : un seul démon, un état qui ne
survit pas à un démarrage raté, une entrée ``grimoire-http`` ajoutée sans
toucher à l'entrée stdio ``grimoire`` ni aux serveurs du projet.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli import cmd_mcp
from grimoire.cli.app import app

pytestmark = pytest.mark.covers(
    "cli:mcp serve", "cli:mcp start", "cli:mcp stop", "cli:mcp status", "cli:mcp register", level="integration",
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(home))
    monkeypatch.delenv("GRIMOIRE_MCP_TRANSPORT", raising=False)
    return home


def _mock_daemon(monkeypatch: pytest.MonkeyPatch, *, pid: int = 4242, alive: bool = True) -> list[list[str]]:
    spawned: list[list[str]] = []

    def spawn(cmd: list[str]) -> int:
        spawned.append(cmd)
        return pid

    monkeypatch.setattr(cmd_mcp, "_spawn_detached", spawn)
    monkeypatch.setattr(cmd_mcp, "_port_alive", lambda host, port: alive and bool(spawned))
    monkeypatch.setattr(cmd_mcp.time, "sleep", lambda s: None)
    return spawned


# ── start / status / stop ────────────────────────────────────────────────────

def test_start_status_stop_lifecycle(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    spawned = _mock_daemon(monkeypatch, pid=4242)
    killed: list[int] = []
    monkeypatch.setattr(cmd_mcp, "_terminate", lambda pid: killed.append(pid) or True)
    monkeypatch.setattr("grimoire.tools.mcp_processes.scan", lambda *a, **k: [])

    res = runner.invoke(app, ["mcp", "start", "--port", "7399"])
    assert res.exit_code == 0, res.output
    assert spawned == [[cmd_mcp.sys.executable, "-m", "grimoire.mcp.server", "--http", "--host", "127.0.0.1", "--port", "7399"]]
    state = cmd_mcp.read_state()
    assert state == {"pid": 4242, "host": "127.0.0.1", "port": 7399, "url": "http://127.0.0.1:7399/mcp"}

    res = runner.invoke(app, ["mcp", "status"])
    assert res.exit_code == 0
    assert "7399/mcp" in res.output

    res = runner.invoke(app, ["mcp", "status", "--json"])
    payload = json.loads(res.stdout)
    assert payload["shared"]["port"] == 7399
    assert payload["measured"] is True and payload["stdio_count"] == 0

    res = runner.invoke(app, ["mcp", "start"])  # déjà démarré : pas de second démon
    assert "déjà démarré" in res.output
    assert len(spawned) == 1

    res = runner.invoke(app, ["mcp", "stop"])
    assert res.exit_code == 0
    assert killed == [4242]
    assert cmd_mcp.read_state() is None


def test_start_timeout_leaves_no_state(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_daemon(monkeypatch, pid=7, alive=False)
    killed: list[int] = []
    monkeypatch.setattr(cmd_mcp, "_terminate", lambda pid: killed.append(pid) or True)
    monkeypatch.setattr(cmd_mcp, "_START_TIMEOUT_S", 0.0)
    res = runner.invoke(app, ["mcp", "start", "--port", "7398"])
    assert res.exit_code == 1
    assert cmd_mcp.read_state() is None
    assert killed == [7]  # le processus qui n'a pas répondu n'est pas laissé orphelin


def test_start_refuses_a_port_already_taken(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    spawned: list[list[str]] = []
    monkeypatch.setattr(cmd_mcp, "_spawn_detached", lambda cmd: spawned.append(cmd) or 1)
    monkeypatch.setattr(cmd_mcp, "_port_alive", lambda host, port: True)
    res = runner.invoke(app, ["mcp", "start", "--port", "7397"])
    assert res.exit_code == 1
    assert spawned == []
    assert "--port" in res.output


def test_stop_and_status_when_nothing_runs(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cmd_mcp, "_port_alive", lambda host, port: False)
    monkeypatch.setattr("grimoire.tools.mcp_processes.scan", lambda *a, **k: None)
    res = runner.invoke(app, ["mcp", "stop"])
    assert "Aucun serveur" in res.output
    res = runner.invoke(app, ["mcp", "status"])
    assert res.exit_code == 0
    assert "grimoire mcp start" in res.output
    assert "non mesuré" in res.output


def test_status_counts_stdio_servers(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.tools.mcp_processes import McpProcess

    monkeypatch.setattr(cmd_mcp, "_port_alive", lambda host, port: False)
    procs = [McpProcess(pid=i, rss_kb=20480, argv=("grimoire-mcp",), shared=False) for i in range(1, 6)]
    monkeypatch.setattr("grimoire.tools.mcp_processes.scan", lambda *a, **k: procs)
    res = runner.invoke(app, ["mcp", "status"])
    assert "5 serveur(s) MCP stdio" in res.output
    assert "100 Mo" in res.output
    assert "WARN" in res.output


def test_orphan_state_is_cleaned_by_status(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    cmd_mcp.write_state({"pid": 1, "host": "127.0.0.1", "port": 7396, "url": "http://127.0.0.1:7396/mcp"})
    monkeypatch.setattr(cmd_mcp, "_port_alive", lambda host, port: False)
    monkeypatch.setattr("grimoire.tools.mcp_processes.scan", lambda *a, **k: [])
    runner.invoke(app, ["mcp", "status"])
    assert cmd_mcp.read_state() is None


# ── serve ────────────────────────────────────────────────────────────────────

def test_serve_http_sets_transport_and_runs(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[bool, str, int]] = []
    monkeypatch.setattr(cmd_mcp, "_run_server", lambda http, host, port: calls.append((http, host, port)))
    res = runner.invoke(app, ["mcp", "serve", "--http", "--port", "7395"])
    assert res.exit_code == 0, res.output
    assert calls == [(True, "127.0.0.1", 7395)]
    res = runner.invoke(app, ["mcp", "serve"])
    assert calls[-1] == (False, "127.0.0.1", cmd_mcp.DEFAULT_PORT)


def test_run_server_http_marks_the_process_shared(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("mcp", reason="extra optionnel grimoire-kit[mcp] non installé")
    from grimoire.mcp import server

    seen: list[tuple[str, int]] = []
    monkeypatch.setattr(server, "run_http", lambda host, port: seen.append((host, port)))
    cmd_mcp._run_server(True, "127.0.0.1", 7394)
    assert seen == [("127.0.0.1", 7394)]
    assert cmd_mcp.os.environ["GRIMOIRE_MCP_TRANSPORT"] == "http"


# ── register ─────────────────────────────────────────────────────────────────

def test_register_adds_http_entry_and_keeps_the_rest(runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cmd_mcp, "_port_alive", lambda host, port: False)
    project = tmp_path / "proj"
    project.mkdir()
    before = {
        "mcpServers": {
            "grimoire": {"command": "grimoire-mcp", "args": [], "env": {}},
            "playwright": {"type": "stdio", "command": "npx", "args": ["@playwright/mcp@0.0.80"]},
        },
        "other": {"kept": True},
    }
    (project / ".mcp.json").write_text(json.dumps(before, indent=2), encoding="utf-8")

    res = runner.invoke(app, ["mcp", "register", str(project)])
    assert res.exit_code == 0, res.output
    data = json.loads((project / ".mcp.json").read_text(encoding="utf-8"))
    assert data["mcpServers"]["grimoire"] == before["mcpServers"]["grimoire"]
    assert data["mcpServers"]["playwright"] == before["mcpServers"]["playwright"]
    assert data["other"] == {"kept": True}
    assert data["mcpServers"]["grimoire-http"] == {"type": "http", "url": "http://127.0.0.1:7331/mcp"}
    assert "grimoire mcp start" in res.output  # aucun démon ne répond : dit, pas caché

    res = runner.invoke(app, ["mcp", "register", str(project)])
    assert "déjà à jour" in res.output

    res = runner.invoke(app, ["mcp", "register", str(project), "--url", "http://127.0.0.1:9000/mcp"])
    data = json.loads((project / ".mcp.json").read_text(encoding="utf-8"))
    assert data["mcpServers"]["grimoire-http"]["url"] == "http://127.0.0.1:9000/mcp"
    assert "updated" in res.output

    res = runner.invoke(app, ["mcp", "register", str(project), "--remove"])
    assert res.exit_code == 0
    data = json.loads((project / ".mcp.json").read_text(encoding="utf-8"))
    assert "grimoire-http" not in data["mcpServers"]
    assert data["mcpServers"]["grimoire"] == before["mcpServers"]["grimoire"]
    res = runner.invoke(app, ["mcp", "register", str(project), "--remove"])
    assert "Aucune entrée" in res.output


def test_register_uses_the_running_daemon_url(runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cmd_mcp.write_state({"pid": 1, "host": "127.0.0.1", "port": 7393, "url": "http://127.0.0.1:7393/mcp"})
    monkeypatch.setattr(cmd_mcp, "_port_alive", lambda host, port: port == 7393)
    project = tmp_path / "proj"
    project.mkdir()
    res = runner.invoke(app, ["mcp", "register", str(project)])
    assert res.exit_code == 0, res.output
    data = json.loads((project / ".mcp.json").read_text(encoding="utf-8"))
    assert data == {"mcpServers": {"grimoire-http": {"type": "http", "url": "http://127.0.0.1:7393/mcp"}}}
    assert "grimoire mcp start" not in res.output


def test_register_refuses_broken_json_and_missing_dir(runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cmd_mcp, "_port_alive", lambda host, port: False)
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".mcp.json").write_text("{not json", encoding="utf-8")
    res = runner.invoke(app, ["mcp", "register", str(project)])
    assert res.exit_code == 1
    assert (project / ".mcp.json").read_text(encoding="utf-8") == "{not json"  # jamais écrasé
    res = runner.invoke(app, ["mcp", "register", str(tmp_path / "absent")])
    assert res.exit_code == 1
