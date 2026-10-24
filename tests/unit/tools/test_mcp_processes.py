"""Compte des serveurs MCP vivants et avertissement de ``doctor`` (#723).

Un ``/proc`` factice sous ``tmp_path`` : le scanner ne devine rien, il lit
``cmdline`` et ``status``. Sans ``/proc`` il répond « non mesuré ».
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.cli import cmd_up
from grimoire.cli.cmd_up import check_mcp_json, check_mcp_processes
from grimoire.tools.mcp_processes import McpProcess, classify, scan, stdio_servers, total_rss_kb


def _proc(root: Path, pid: int, argv: list[str], rss_kb: int) -> None:
    d = root / str(pid)
    d.mkdir(parents=True)
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    (d / "status").write_text(f"Name:\tpython\nVmRSS:\t{rss_kb} kB\nThreads:\t1\n", encoding="utf-8")


def test_classify_recognises_the_kit_servers_only() -> None:
    assert classify(["/venv/bin/grimoire-mcp"]) is False
    assert classify(["grimoire-mcp.exe", "--http"]) is True
    assert classify(["/venv/bin/python3.12", "-m", "grimoire.mcp.server"]) is False
    assert classify(["python", "-m", "grimoire.mcp.server", "--http", "--port", "7331"]) is True
    assert classify(["/usr/bin/python3", "-m", "grimoire", "mcp", "serve", "--http"]) is True
    assert classify(["grimoire", "mcp", "serve"]) is False
    assert classify(["grimoire", "mcp", "status"]) is None
    assert classify(["/usr/bin/zsh", "-c", "grimoire-mcp"]) is None  # un shell qui cite le nom n'est pas un serveur
    assert classify(["python", "-m", "pytest", "tests/unit/mcp"]) is None
    assert classify([]) is None


def test_scan_reads_a_fake_proc_tree(tmp_path: Path) -> None:
    proc = tmp_path / "proc"
    _proc(proc, 100, ["/venv/bin/python", "-m", "grimoire.mcp.server"], 30000)
    _proc(proc, 101, ["/venv/bin/grimoire-mcp"], 45000)
    _proc(proc, 102, ["/venv/bin/python", "-m", "grimoire.mcp.server", "--http", "--port", "7331"], 60000)
    _proc(proc, 103, ["/usr/bin/zsh", "-c", "grimoire-mcp"], 5000)
    _proc(proc, 104, ["/venv/bin/grimoire-mcp"], 1)  # moi-même : exclu
    (proc / "self").mkdir()
    (proc / "meminfo").write_text("MemTotal: 1 kB\n", encoding="utf-8")
    broken = proc / "105"
    broken.mkdir()  # pas de cmdline : disparu entre deux lectures, ignoré

    found = scan(proc, self_pid=104)
    assert found is not None
    assert [(p.pid, p.rss_kb, p.shared) for p in found] == [(100, 30000, False), (101, 45000, False), (102, 60000, True)]
    assert [p.pid for p in stdio_servers(found)] == [100, 101]
    assert total_rss_kb(stdio_servers(found)) == 75000


def test_scan_without_proc_is_not_measured(tmp_path: Path) -> None:
    assert scan(tmp_path / "nope") is None


# ── doctor ───────────────────────────────────────────────────────────────────

def _stdio(n: int, rss_kb: int = 51200) -> list[McpProcess]:
    return [McpProcess(pid=i, rss_kb=rss_kb, argv=("grimoire-mcp",), shared=False) for i in range(1, n + 1)]


def test_doctor_warns_above_the_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("grimoire.tools.mcp_processes.scan", lambda *a, **k: _stdio(11))
    chk = check_mcp_processes()
    assert chk.name == "mcp_processes" and chk.passed is True and chk.level == "warn"
    assert "11 grimoire-mcp stdio process(es) alive, 550 MB RSS" in chk.detail
    assert "grimoire mcp start" in chk.remedy


def test_doctor_is_quiet_at_or_below_the_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("grimoire.tools.mcp_processes.scan", lambda *a, **k: _stdio(3))
    chk = check_mcp_processes()
    assert chk.level == "ok" and chk.passed is True
    monkeypatch.setattr("grimoire.tools.mcp_processes.scan", lambda *a, **k: [])
    assert check_mcp_processes().level == "info"


def test_doctor_names_the_shared_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    shared = McpProcess(pid=99, rss_kb=60000, argv=("grimoire-mcp", "--http"), shared=True)
    monkeypatch.setattr("grimoire.tools.mcp_processes.scan", lambda *a, **k: [*_stdio(1), shared])
    chk = check_mcp_processes()
    assert "1 grimoire-mcp stdio" in chk.detail and "shared http server running (pid 99)" in chk.detail


def test_doctor_says_not_measured_without_proc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("grimoire.tools.mcp_processes.scan", lambda *a, **k: None)
    chk = check_mcp_processes()
    assert chk.level == "info" and "not measured" in chk.detail


def test_doctor_checks_the_http_entry_of_mcp_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".mcp.json").write_text(
        '{"mcpServers": {"grimoire-http": {"type": "http", "url": "http://127.0.0.1:7331/mcp"},'
        ' "badport": {"type": "http", "url": "http://localhost:abc/mcp"},'
        ' "ftp": {"type": "http", "url": "ftp://localhost:21/mcp"}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(cmd_up, "_tcp_reachable", lambda url, port, **k: port == 7331)
    by_name = {c.name: c for c in check_mcp_json(tmp_path)}
    assert by_name["mcp_grimoire-http"].level == "ok"
    assert by_name["mcp_badport"].passed is False and "invalid url" in by_name["mcp_badport"].detail
    assert by_name["mcp_ftp"].passed is False and "invalid url" in by_name["mcp_ftp"].detail

    monkeypatch.setattr(cmd_up, "_tcp_reachable", lambda url, port, **k: False)
    chk = {c.name: c for c in check_mcp_json(tmp_path)}["mcp_grimoire-http"]
    assert chk.passed is True and chk.level == "warn"  # injoignable : l'entrée stdio reste, pas un FAIL
    assert "grimoire mcp start" in chk.remedy


def test_doctor_takes_the_scheme_port_of_a_remote_http_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Constat sur la Forge (3.65.0) : context7 et github, servis en https sans port explicite,
    # sortaient en FAIL « has no port » avec une remédiation grimoire hors sujet.
    (tmp_path / ".mcp.json").write_text(
        '{"mcpServers": {"context7": {"type": "streamable-http", "url": "https://mcp.context7.com/mcp"},'
        ' "github": {"type": "http", "url": "https://api.githubcopilot.com/mcp/"},'
        ' "plain": {"type": "http", "url": "http://localhost/mcp"}}}',
        encoding="utf-8",
    )
    probed: list[tuple[str, int]] = []
    monkeypatch.setattr(cmd_up, "_tcp_reachable", lambda url, port, **k: probed.append((url, port)) or url.startswith("https"))
    by_name = {c.name: c for c in check_mcp_json(tmp_path)}
    assert by_name["mcp_context7"].level == "ok" and by_name["mcp_github"].level == "ok"
    assert ("https://mcp.context7.com/mcp", 443) in probed and ("http://localhost/mcp", 80) in probed
    assert by_name["mcp_plain"].passed is True and by_name["mcp_plain"].level == "warn"
    assert "grimoire mcp start" in by_name["mcp_plain"].remedy  # serveur local : remède grimoire

    monkeypatch.setattr(cmd_up, "_tcp_reachable", lambda url, port, **k: False)
    remote = {c.name: c for c in check_mcp_json(tmp_path)}["mcp_context7"]
    assert remote.passed is True and remote.level == "warn"
    assert "grimoire mcp" not in remote.remedy  # serveur tiers : rien à démarrer côté grimoire
