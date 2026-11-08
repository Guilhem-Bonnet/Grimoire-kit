"""GAO-perf-cockpit-002 — les lectures lourdes du cockpit se valident par ``ETag`` et répondent 304.

``tasks`` (``attention``, ``sessions``, ``sheet`` aussi) pèse 88 à 215 Ko une fois rempli : renvoyer ces octets
inchangés à chaque ouverture de page coûte le réseau sans rien apprendre au client. La réponse porte donc
l'empreinte de son corps ; un ``If-None-Match`` qui la nomme revient en 304 sans corps.
"""

from __future__ import annotations

import http.client
import json
import threading
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from grimoire.cli import cmd_cockpit
from grimoire.missions.service import TaskService
from grimoire.tools import project_registry, workspace_read_cache

TASKS = "/api/workspace/tasks?project=home"


@pytest.fixture
def served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Un cockpit mono-projet (``home``) sur la loopback, avec la tâche que la vue lira."""
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "ck"))
    home = tmp_path / "home"
    (home / ".git").mkdir(parents=True)
    project_registry.save_registry([{"name": "Home", "path": str(home), "slug": "home"}])
    monkeypatch.setattr(cmd_cockpit, "_HOME_SLUG", "home")
    cmd_cockpit._API_CACHE.clear()
    workspace_read_cache.clear()
    TaskService(home).add("Une tâche à lister", ("critère",), actor="t", owner="guilhem", ready=True)

    httpd = cmd_cockpit.ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(cmd_cockpit._CockpitHandler, directory=str(tmp_path))
    )
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield httpd.server_address[1], home
    finally:
        httpd.shutdown()
        httpd.server_close()
        cmd_cockpit._API_CACHE.clear()
        workspace_read_cache.clear()


def _get(port: int, path: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    try:
        conn.request("GET", path, headers=headers or {})
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


def test_avec_l_etag_courant_dans_if_none_match_la_reponse_est_un_304_sans_corps(served: Any) -> None:
    port, _home = served
    status, headers, body = _get(port, TASKS)
    assert status == 200
    etag = headers.get("ETag")
    assert etag and json.loads(body)["count"] == 1

    status, headers, body = _get(port, TASKS, {"If-None-Match": etag})
    assert status == 304
    assert body == b""
    assert headers.get("ETag") == etag


def test_un_corps_change_porte_un_autre_etag(served: Any) -> None:
    port, home = served
    status, headers, _body = _get(port, TASKS)
    assert status == 200
    first = headers["ETag"]

    TaskService(home).add("Une seconde tâche", ("critère",), actor="t", owner="guilhem", ready=True)
    status, headers, body = _get(port, TASKS, {"If-None-Match": first})
    assert status == 200
    assert json.loads(body)["count"] == 2
    assert headers.get("ETag") != first
