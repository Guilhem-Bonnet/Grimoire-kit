"""#857 — les tests HTTP du cockpit survivent à une connexion coupée.

Constat (runners ``windows-latest``, une PR sur deux) : ``ConnectionAbortedError:
[WinError 10053]`` sur des tests différents à chaque run, tous des refus
(403/404) du cockpit. Mécanisme : ``http.client`` envoie l'en-tête et le corps
en deux ``send`` ; sous charge, ``_CockpitHandler`` parse l'en-tête, refuse,
répond et ferme avant que le corps n'arrive. Le corps atteint un socket
fermé : RST, et Windows jette la réponse déjà reçue.

Deux preuves, sans Windows :

1. côté serveur, le cockpit ne répond pas avant d'avoir lu le corps déclaré —
   sur la base, la réponse arrive alors que le corps n'est pas encore parti
   (rouge-avant / vert-après, reproduit en envoyant l'en-tête seul) ;
2. côté client des tests, ``_cockpit_http`` retente sur une coupure franche
   (``SO_LINGER`` à 0 → RST, rendu ``ConnectionResetError`` à la lecture ou
   ``BrokenPipeError`` à l'envoi du corps sous macOS), relit la réponse en
   entier, et ne masque ni un 4xx ni un refus de connexion.
"""

from __future__ import annotations

import json
import select
import socket
import struct
import threading
import time
import urllib.error
from collections.abc import Iterator
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from grimoire.cli import cmd_cockpit
from grimoire.tools import request_guard

from . import _cockpit_http

# ── 1. Serveur : le corps est lu avant la réponse ───────────────────────────


@pytest.fixture
def cockpit_port(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[int]:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "ck"))
    httpd = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(cmd_cockpit._CockpitHandler, directory=str(tmp_path))
    )
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()


def _split_post(port: int, path: str, payload: dict[str, object]) -> tuple[bool, bytes]:
    """En-tête puis corps en deux envois séparés, comme ``http.client``.

    Retourne ``(réponse arrivée avant le corps, réponse complète)``.
    """
    body = json.dumps(payload).encode("utf-8")
    head = (
        f"POST {path} HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"{request_guard.SESSION_HEADER}: {request_guard.session_token()}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")
    with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
        sock.sendall(head)
        early, _, _ = select.select([sock], [], [], 0.5)
        sock.sendall(body)
        chunks: list[bytes] = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    return bool(early), b"".join(chunks)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/api/other", b"HTTP/1.0 404"),
        ("/api/blueprints/demo/compile?project=nulle-part", b"HTTP/1.0 403"),
    ],
)
def test_le_cockpit_lit_le_corps_avant_de_refuser(cockpit_port: int, path: str, expected: bytes) -> None:
    """Un refus n'est pas émis tant que le corps déclaré n'est pas lu ; la
    connexion se ferme proprement, en-tête ``Connection: close`` posé."""
    early, response = _split_post(cockpit_port, path, {"id": "demo", "nodes": [], "edges": []})

    assert not early, "le cockpit a répondu avant d'avoir lu le corps (RST sous Windows, #857)"
    assert response.startswith(expected), response[:80]
    assert b"\r\nConnection: close\r\n" in response


# ── 2. Client des tests : retente sur une coupure franche, rien d'autre ─────


class _CuttingServer:
    """Faux serveur : coupe ``cuts`` connexions (RST), puis répond ``status``."""

    def __init__(self, cuts: int, status: int = 200, body: dict[str, object] | None = None) -> None:
        self.cuts, self.status = cuts, status
        self.body = json.dumps(body if body is not None else {"ok": True}).encode()
        self.accepted = 0
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        self.sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except TimeoutError:
                continue
            self.accepted += 1
            with conn:
                conn.settimeout(5)
                raw = b""
                while b"\r\n\r\n" not in raw:
                    raw += conn.recv(4096)
                if self.accepted <= self.cuts:
                    # En-tête lu, puis RST sans répondre : ce que voit le client
                    # sous Windows quand le serveur ferme sur un corps en vol.
                    # (Lire d'abord évite que le RST tombe sur le ``send`` du
                    # corps, où macOS le rend en ``BrokenPipeError`` — retenté
                    # aussi, voir ``_cockpit_http.RETRIED``.)
                    conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                    continue
                head, _, rest = raw.partition(b"\r\n\r\n")
                length = 0
                for line in head.split(b"\r\n"):
                    if line.lower().startswith(b"content-length:"):
                        length = int(line.split(b":", 1)[1])
                while len(rest) < length:
                    rest += conn.recv(4096)
                reason = {200: "OK", 403: "Forbidden", 404: "Not Found"}[self.status]
                conn.sendall(
                    f"HTTP/1.0 {self.status} {reason}\r\nContent-Type: application/json\r\n"
                    f"Content-Length: {len(self.body)}\r\nConnection: close\r\n\r\n".encode()
                    + self.body
                )

    def close(self) -> None:
        self._stop.set()
        self.thread.join(timeout=2)
        self.sock.close()


@pytest.fixture
def fast_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_cockpit_http, "RETRY_DELAY_S", 0.01)


@pytest.mark.usefixtures("fast_retry")
def test_le_client_retente_apres_une_coupure_et_relit_la_reponse() -> None:
    server = _CuttingServer(cuts=1, body={"ok": True, "n": 1})
    try:
        status, body = _cockpit_http.post(server.port, "/api/x", {"a": 1}, timeout=5)
    finally:
        server.close()

    assert (status, body) == (200, {"ok": True, "n": 1})
    assert server.accepted == 2


@pytest.mark.usefixtures("fast_retry")
def test_le_client_ne_retente_jamais_un_refus_http() -> None:
    server = _CuttingServer(cuts=0, status=403, body={"ok": False, "error": "lecture seule"})
    try:
        status, body = _cockpit_http.post(server.port, "/api/x", {"a": 1}, timeout=5)
    finally:
        server.close()

    assert (status, body["error"]) == (403, "lecture seule")
    assert server.accepted == 1


@pytest.mark.usefixtures("fast_retry")
def test_le_client_rend_la_coupure_apres_trois_essais() -> None:
    server = _CuttingServer(cuts=_cockpit_http.ATTEMPTS + 2)
    try:
        with pytest.raises((*_cockpit_http.RETRIED, urllib.error.URLError)):
            _cockpit_http.post(server.port, "/api/x", {"a": 1}, timeout=5)
    finally:
        server.close()

    assert server.accepted == _cockpit_http.ATTEMPTS


def test_le_client_ne_retente_pas_un_refus_de_connexion(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un refus de connexion est rendu au premier essai : une seule ouverture,
    aucun délai. (Compté, pas chronométré : sur un runner Windows saturé, un
    ``connect`` refusé sur la loopback a pris 2 s.)"""
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    opened: list[str] = []
    slept: list[float] = []
    real_urlopen = urllib.request.urlopen
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda req, **kw: (opened.append(req.full_url), real_urlopen(req, **kw))[1]
    )
    monkeypatch.setattr(time, "sleep", slept.append)

    with pytest.raises(urllib.error.URLError) as caught:
        _cockpit_http.get(port, "/api/x", timeout=5)

    assert isinstance(caught.value.reason, ConnectionRefusedError)
    assert len(opened) == 1
    assert slept == []
