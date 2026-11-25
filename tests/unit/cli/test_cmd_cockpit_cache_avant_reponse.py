"""Une écriture du cockpit vide le cache des lectures avant que sa réponse parte.

Constat (CI des releases 3.85.0 à 3.87.0, tests e2e de l'espace « Exécuter » en
échec par intermittence) : ``do_POST`` vidait le cache dans son ``finally``,
donc après l'envoi de la réponse. La page relit dès la réponse reçue ; si sa
lecture arrivait avant ce ``finally``, elle rejoignait un calcul commencé avant
l'écriture (vol unique, #1221) ou retrouvait une réponse gardée, affichait
l'état d'avant et ne redemandait jamais.
"""

from __future__ import annotations

import http.server
import threading
import time
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from grimoire.cli import cmd_cockpit

from .. import _cockpit_http


@pytest.fixture(autouse=True)
def _cockpit_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "ck"))


@pytest.fixture
def journal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Un cockpit réel dont on note, dans l'ordre, les vidages du cache et les départs de réponse."""
    events: list[str] = []
    forget = cmd_cockpit._forget_live_reads
    send_response = http.server.BaseHTTPRequestHandler.send_response

    def _forget() -> None:
        events.append("vidage")
        forget()

    def _send_response(self: http.server.BaseHTTPRequestHandler, code: int, message: str | None = None) -> None:
        events.append(f"réponse {self.command}")
        send_response(self, code, message)

    monkeypatch.setattr(cmd_cockpit, "_forget_live_reads", _forget)
    monkeypatch.setattr(http.server.BaseHTTPRequestHandler, "send_response", _send_response)
    httpd = cmd_cockpit.ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(cmd_cockpit._CockpitHandler, directory=str(tmp_path))
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1], events
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def test_le_cache_est_vide_avant_que_la_reponse_d_une_ecriture_parte(journal: Any) -> None:
    port, events = journal

    status, _ = _cockpit_http.post(port, "/api/projects/select", {"slug": "inconnu"})

    assert status == 404
    assert events[:2] == ["vidage", "réponse POST"], events


def test_une_lecture_ne_vide_pas_le_cache(journal: Any) -> None:
    port, events = journal

    _cockpit_http.post(port, "/api/projects/select", {"slug": "inconnu"})
    # Le second vidage (le ``finally`` de l'écriture) suit la réponse : on l'attend avant de repartir de zéro.
    deadline = time.monotonic() + 5
    while len(events) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert events == ["vidage", "réponse POST", "vidage"], events
    del events[:]
    status, _ = _cockpit_http.get(port, "/api/projects")

    assert status == 200
    assert events == ["réponse GET"], events
