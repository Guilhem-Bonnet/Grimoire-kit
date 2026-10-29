"""P94 — transport du cockpit : gzip, ETag/304, HTTP/1.1 keep-alive, flux SSE."""

from __future__ import annotations

import gzip
import http.client
import json
import threading
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from grimoire.cli import cmd_cockpit
from grimoire.tools import project_registry

BIG_JS = "export const x = 1;\n" * 400


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "home"))
    proj = tmp_path / "served"
    (proj / "_grimoire").mkdir(parents=True)
    project_registry.save_registry([{"name": "Served", "path": str(proj), "slug": "served"}])
    monkeypatch.setattr(cmd_cockpit, "_HOME_SLUG", "served")
    monkeypatch.setattr(cmd_cockpit, "_HOME_PATH", proj.resolve())
    web = tmp_path / "web"
    web.mkdir()
    (web / "app.js").write_text(BIG_JS)
    (web / "tiny.js").write_text("x")
    httpd = cmd_cockpit.ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(cmd_cockpit._CockpitHandler, directory=str(web))
    )
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1], web
    httpd.shutdown()
    httpd.server_close()


def _get(conn: http.client.HTTPConnection, path: str, **headers: str) -> tuple[http.client.HTTPResponse, bytes]:
    conn.request("GET", path, headers=headers)
    resp = conn.getresponse()
    return resp, resp.read()


def test_js_gzip_when_accepted_plain_otherwise(server: tuple[int, Path]) -> None:
    port, _ = server
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    resp, body = _get(conn, "/app.js", **{"Accept-Encoding": "gzip, br"})
    assert resp.getheader("Content-Encoding") == "gzip"
    assert gzip.decompress(body).decode() == BIG_JS
    resp, body = _get(conn, "/app.js")
    assert resp.getheader("Content-Encoding") is None
    assert body.decode() == BIG_JS
    resp, body = _get(conn, "/tiny.js", **{"Accept-Encoding": "gzip"})
    assert resp.getheader("Content-Encoding") is None  # sous le seuil
    assert body == b"x"


def test_json_over_threshold_gzip(server: tuple[int, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    port, _ = server
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    resp, body = _get(conn, "/api/projects", **{"Accept-Encoding": "gzip"})
    assert resp.status == 200
    big = resp.getheader("Content-Encoding") == "gzip"
    data: Any = json.loads(gzip.decompress(body) if big else body)
    assert data  # lisible dans les deux cas
    monkeypatch.setattr(cmd_cockpit, "projects_payload", lambda: {"pad": "x" * 5000})
    resp, body = _get(conn, "/api/projects", **{"Accept-Encoding": "gzip"})
    assert resp.getheader("Content-Encoding") == "gzip"
    assert json.loads(gzip.decompress(body)) == {"pad": "x" * 5000}
    resp, body = _get(conn, "/api/projects")
    assert resp.getheader("Content-Encoding") is None
    assert json.loads(body) == {"pad": "x" * 5000}


def test_etag_304_then_200_after_change(server: tuple[int, Path]) -> None:
    port, web = server
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    resp, _ = _get(conn, "/app.js")
    etag = resp.getheader("ETag")
    assert etag and resp.getheader("Last-Modified")
    assert resp.getheader("Cache-Control") == "no-cache"
    resp, body = _get(conn, "/app.js", **{"If-None-Match": etag})
    assert (resp.status, body) == (304, b"")
    resp, body = _get(conn, "/app.js", **{"If-Modified-Since": resp.getheader("Date") or ""})
    assert resp.status in (200, 304)
    (web / "app.js").write_text(BIG_JS + "export const y = 2;\n")
    resp, body = _get(conn, "/app.js", **{"If-None-Match": etag})
    assert resp.status == 200
    assert resp.getheader("ETag") != etag
    assert b"export const y" in body


def test_http11_keepalive_reuses_connection(server: tuple[int, Path]) -> None:
    port, _ = server
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    resp, _ = _get(conn, "/app.js")
    assert resp.version == 11
    assert (resp.getheader("Connection") or "").lower() != "close"
    sock = conn.sock
    for _ in range(3):
        resp, _ = _get(conn, "/api/projects")
        assert resp.status == 200
    assert conn.sock is sock  # même socket : la connexion a été gardée


def test_post_then_get_on_same_connection(server: tuple[int, Path]) -> None:
    port, _ = server
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", "/api/memory", body=b'{"action":"x"}', headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    resp.read()
    resp, _ = _get(conn, "/app.js")
    assert resp.status == 200


def test_pulse_stream_stays_open_and_delivers(server: tuple[int, Path]) -> None:
    from grimoire.tools.workspace_routes import STREAM_PATH

    port, _ = server
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", f"{STREAM_PATH}?project=served")
    resp = conn.getresponse()
    assert resp.status == 200
    assert resp.getheader("Content-Type", "").startswith("text/event-stream")
    first = resp.fp.readline()  # type: ignore[union-attr]
    assert first.strip()  # un événement (ou le retry) arrive, flux toujours ouvert
    assert not resp.isclosed()
    conn.close()
