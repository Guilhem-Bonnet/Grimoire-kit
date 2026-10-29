"""``/api/workspace/notify`` (I1) : lire, écrire et essayer la commande de notification, derrière la garde d'écriture."""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from grimoire.cli.doctor_notify import apply_notify_check
from grimoire.core.standard_generation import STANDARD_PROFILE_FILE
from grimoire.data import web_path
from grimoire.missions.decisions import NOTIFY_ENV
from grimoire.tools.forge_server import ForgeAPI, make_handler
from grimoire.tools.request_guard import SESSION_HEADER, session_token

pytestmark = pytest.mark.no_session_token


def _call(port: int, method: str, path: str, payload: dict[str, Any] | None = None, *, token: bool = True) -> tuple[int, Any]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers[SESSION_HEADER] = session_token()
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 — loopback de test
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8") or "{}")


@pytest.fixture
def atelier(tmp_path_factory: pytest.TempPathFactory, real_project: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[int, Path]]:
    monkeypatch.delenv(NOTIFY_ENV, raising=False)
    profile = real_project / STANDARD_PROFILE_FILE
    before = profile.read_text(encoding="utf-8")
    api = ForgeAPI(real_project, Path(__file__).resolve().parents[2], web_path())
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(api))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield httpd.server_address[1], real_project
    finally:
        httpd.shutdown()
        httpd.server_close()
        profile.write_text(before, encoding="utf-8")


def _writer(tmp_path: Path) -> tuple[str, Path]:
    """Une commande qui écrit son message dans un fichier : la preuve qu'elle a été exécutée."""
    out = tmp_path / "sortie.txt"
    script = tmp_path / "notifier.py"
    script.write_text(
        "import os, sys, pathlib\n"
        f"pathlib.Path({str(out)!r}).write_text(os.environ['GRIMOIRE_NOTIFY_TITLE'] + '|' + sys.argv[1], encoding='utf-8')\n",
        encoding="utf-8",
    )
    # Guillemets doubles : le découpage des commandes suit le shell du système, et Windows ne lit pas les simples.
    return f'"{sys.executable}" "{script}" {{message}}', out


def test_le_test_execute_la_commande_configuree(atelier: tuple[int, Path], tmp_path: Path) -> None:
    port, _ = atelier
    command, out = _writer(tmp_path)
    status, state = _call(port, "POST", "/api/workspace/notify", {"command": command})
    assert status == 200 and state["command"] == command and state["source"] == "profile"
    status, body = _call(port, "POST", "/api/workspace/notify/test", {})
    assert status == 200 and body["ok"] is True and body["code"] == 0
    assert "essai" in out.read_text(encoding="utf-8").lower()


def test_une_commande_qui_echoue_se_dit(atelier: tuple[int, Path]) -> None:
    port, _ = atelier
    assert _call(port, "POST", "/api/workspace/notify", {"command": f'"{sys.executable}" -c "raise SystemExit(3)"'})[0] == 200
    status, body = _call(port, "POST", "/api/workspace/notify/test", {})
    assert status == 200 and body["ok"] is False and body["code"] != 0 and "3" in body["error"]


def test_une_commande_vide_est_un_400_avec_sa_raison(atelier: tuple[int, Path]) -> None:
    port, _ = atelier
    _call(port, "POST", "/api/workspace/notify", {"command": ""})
    status, body = _call(port, "POST", "/api/workspace/notify/test", {})
    assert status == 400 and "commande" in json.dumps(body, ensure_ascii=False)


def test_une_commande_illisible_n_est_pas_ecrite(atelier: tuple[int, Path]) -> None:
    port, project = atelier
    before = (project / STANDARD_PROFILE_FILE).read_text(encoding="utf-8")
    status, body = _call(port, "POST", "/api/workspace/notify", {"command": "notify-send 'ouvert"})
    assert status == 400 and "illisible" in json.dumps(body, ensure_ascii=False)
    assert (project / STANDARD_PROFILE_FILE).read_text(encoding="utf-8") == before


def test_sans_jeton_d_ecriture_c_est_refuse(atelier: tuple[int, Path]) -> None:
    port, project = atelier
    before = (project / STANDARD_PROFILE_FILE).read_text(encoding="utf-8")
    assert _call(port, "POST", "/api/workspace/notify/test", {}, token=False)[0] == 403
    assert _call(port, "POST", "/api/workspace/notify", {"command": "true"}, token=False)[0] == 403
    assert (project / STANDARD_PROFILE_FILE).read_text(encoding="utf-8") == before


def test_doctor_signale_une_commande_illisible(real_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(NOTIFY_ENV, "notify-send 'ouvert")
    results: list[dict[str, Any]] = []
    apply_notify_check(results, real_project, fmt="json", console=Console(quiet=True))
    (entry,) = results
    assert entry["name"] == "notify_command" and entry["passed"] is False and entry["level"] == "warn"
    assert "illisible" in entry["detail"]

    monkeypatch.setenv(NOTIFY_ENV, "notify-send {title}")
    results.clear()
    apply_notify_check(results, real_project, fmt="json", console=Console(quiet=True))
    assert results[0]["passed"] is True
    monkeypatch.delenv(NOTIFY_ENV)
    results.clear()
    apply_notify_check(results, real_project, fmt="json", console=Console(quiet=True))
    assert results == [] or results[0]["passed"] is True
