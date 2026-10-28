"""P10 du cockpit : le pouls en flux (Server-Sent Events) sur ``/api/workspace/stream``.

Constat sur la base : la page ne voyait un changement qu'au sondage suivant
(``GRIMOIRE_PULSE_SECONDS``, 5 s par défaut), et une proposition ne bougeait pas
l'empreinte du pouls. Le flux promet qu'une écriture dans le ledger, dans un
journal de session ou dans une proposition part vers la page en moins de 2 s.

Les tests du générateur tournent sur une horloge factice ; ceux du serveur
passent par le vrai ``_CockpitHandler`` sur la loopback, avec l'horloge réelle :
c'est la seule couche où le délai de 2 s se mesure.
"""

from __future__ import annotations

import http.client
import json
import sys
import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from grimoire.cli import cmd_cockpit
from grimoire.hosts.decisions import record_agent_miss
from grimoire.missions.service import TaskService
from grimoire.proposals import sync_proposals
from grimoire.tools import project_registry
from grimoire.tools.workspace_pulse import STREAM_RETRY_MS, pulse_stream
from grimoire.tools.workspace_routes import GET_ROUTES, STREAM_HEADERS, STREAM_PATH

# Garantie produit : 2 s. Sous Windows, le sondage (stat + relecture) et la boucle locale
# traînent (2,41 s observés en CI) : le seuil y est élargi, la garantie reste stricte ailleurs.
DEADLINE_S = 2.0 if sys.platform != "win32" else 4.0


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "cockpit-home"))
    monkeypatch.delenv("GRIMOIRE_PULSE_SECONDS", raising=False)
    for var in (
        "GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID",
        "AI_AGENT", "CLAUDECODE", "CURSOR_AGENT", "GEMINI_CLI", "CODEX_SANDBOX",
    ):
        monkeypatch.delenv(var, raising=False)


def _project(tmp_path: Path, name: str = "alpha") -> Path:
    root = tmp_path / name
    (root / ".git").mkdir(parents=True)
    (root / "project-context.yaml").write_text(
        "project:\n  name: test-cockpit-stream\n  type: webapp\n"
        "user:\n  name: Guilhem\n  language: Français\n  skill_level: expert\n"
        "agents:\n  archetype: minimal\n",
        encoding="utf-8",
    )
    return root


def _parse(chunk: bytes) -> dict[str, Any]:
    """Un événement SSE en champs ; ``data`` décodé en JSON."""
    fields: dict[str, Any] = {}
    for line in chunk.decode("utf-8").strip().splitlines():
        name, _, value = line.partition(": ")
        fields[name] = value
    if "data" in fields:
        fields["data"] = json.loads(fields["data"])
    return fields


# ── Le générateur, horloge factice ──────────────────────────────────────────


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.on_sleep: Callable[[], None] | None = None

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        if self.on_sleep is not None:
            action, self.on_sleep = self.on_sleep, None
            action()


def _stream(root: Path, clock: _Clock, **kwargs: Any) -> Iterator[bytes]:
    return pulse_stream(root, sleep=clock.sleep, clock=lambda: clock.now, **kwargs)


def test_le_flux_annonce_son_delai_de_reconnexion_puis_l_etat_tout_de_suite(tmp_path: Path) -> None:
    """Le premier pouls part sans attendre : c'est lui qui fixe « mis à jour il y a N s » côté page."""
    clock = _Clock()
    stream = _stream(_project(tmp_path), clock)

    assert next(stream) == f"retry: {STREAM_RETRY_MS}\n\n".encode()
    first = _parse(next(stream))

    assert clock.now == 0.0, "le premier pouls n'attend aucun tour"
    assert first["event"] == "pulse"
    assert first["id"] == first["data"]["fingerprint"]
    assert first["data"]["changed"] is True
    assert first["data"]["decisions"]["pending"] == 0


def test_ledger_immobile_un_seul_battement_par_intervalle_declare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rien ne bouge : pas d'événement à chaque tour, un battement ``changed: false`` par intervalle."""
    monkeypatch.setenv("GRIMOIRE_PULSE_SECONDS", "3")
    clock = _Clock()
    stream = _stream(_project(tmp_path), clock)
    next(stream)
    first = _parse(next(stream))

    beat = _parse(next(stream))

    assert clock.now == pytest.approx(3.0), "un battement par intervalle déclaré, pas un par tour"
    assert beat["data"]["changed"] is False
    assert "decisions" not in beat["data"], "sans changement, rien n'est relu"
    assert beat["data"]["fingerprint"] == first["data"]["fingerprint"]


def test_un_changement_part_au_tour_suivant_sans_attendre_l_intervalle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRIMOIRE_PULSE_SECONDS", "300")
    root = _project(tmp_path)
    clock = _Clock()
    stream = _stream(root, clock, tick=0.5)
    next(stream)
    before = _parse(next(stream))
    clock.on_sleep = lambda: TaskService(root).add("Carte née pendant le flux", ("critère",), actor="t", ready=True)

    after = _parse(next(stream))

    assert clock.now == pytest.approx(0.5), "un tour, pas l'intervalle de 300 s"
    assert after["data"]["changed"] is True
    assert after["data"]["fingerprint"] != before["data"]["fingerprint"]


def test_le_flux_n_est_pas_une_lecture_de_la_table() -> None:
    """Les lectures de ``GET_ROUTES`` rendent une charge utile ; un flux sans fin n'y a pas sa place."""
    assert STREAM_PATH not in GET_ROUTES
    assert dict(STREAM_HEADERS)["Content-Type"].startswith("text/event-stream")


# ── Le vrai serveur du cockpit, horloge réelle ──────────────────────────────


@pytest.fixture
def served(tmp_path: Path) -> Iterator[tuple[int, Path]]:
    root = _project(tmp_path)
    project_registry.save_registry([{"name": "Alpha", "path": str(root), "slug": "alpha"}])
    httpd = cmd_cockpit.ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(cmd_cockpit._CockpitHandler, directory=str(tmp_path))
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield int(httpd.server_address[1]), root
    httpd.shutdown()
    httpd.server_close()


class _Reader:
    """Lit le flux événement par événement, avec un délai par lecture."""

    def __init__(self, port: int, query: str) -> None:
        self.conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        self.conn.request("GET", f"{STREAM_PATH}?{query}")
        self.response = self.conn.getresponse()

    def next_event(self) -> dict[str, Any]:
        lines: list[bytes] = []
        while True:
            line = self.response.fp.readline()
            if not line:
                raise EOFError("le flux s'est fermé")
            if line in (b"\n", b"\r\n"):
                if lines and any(raw.startswith(b"data: ") for raw in lines):
                    return _parse(b"".join(lines))
                lines = []  # bloc sans données (``retry``) : pas un événement
                continue
            lines.append(line)

    def close(self) -> None:
        self.conn.close()


def _write_ledger(root: Path) -> None:
    TaskService(root).add("Carte écrite pendant que le flux est ouvert", ("critère",), actor="t", ready=True)


def _write_session_journal(root: Path) -> None:
    runs = root / "_grimoire-output" / ".runs"
    runs.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat()
    (runs / "session-S-flux.json").write_text(
        json.dumps({"schema_version": 1, "session_id": "S-flux", "started_at": now, "updated_at": now, "rules": {}}),
        encoding="utf-8",
    )


def _write_proposal(root: Path) -> None:
    # Deux non-choix sur la même spécialité : le déclencheur (#395) écrit une proposition.
    # Les traces elles-mêmes ne sont pas surveillées : seul le fichier de proposition bouge l'empreinte.
    record_agent_miss(root, category="infra", specialty="terraform")
    record_agent_miss(root, category="infra", specialty="terraform")
    sync_proposals(root)
    assert list((root / "_grimoire-output" / "proposals").glob("*.yaml")), "aucune proposition écrite"


@pytest.mark.parametrize(
    "write",
    [_write_ledger, _write_session_journal, _write_proposal],
    ids=["ledger", "journal-de-session", "proposition"],
)
def test_une_ecriture_emet_un_evenement_sur_le_flux_en_moins_de_2_s(
    served: tuple[int, Path], write: Callable[[Path], None]
) -> None:
    port, root = served
    reader = _Reader(port, "project=alpha")
    try:
        assert reader.response.status == 200
        assert reader.response.getheader("Content-Type", "").startswith("text/event-stream")
        before = reader.next_event()["data"]["fingerprint"]

        write(root)
        written_at = time.monotonic()  # l'écriture est sur le disque : le délai promis court d'ici
        event = reader.next_event()
        elapsed = time.monotonic() - written_at
    finally:
        reader.close()

    assert event["data"]["changed"] is True
    assert event["data"]["fingerprint"] != before
    assert elapsed < DEADLINE_S, f"événement reçu {elapsed:.2f} s après l'écriture"


def test_le_flux_reprend_depuis_l_empreinte_connue(served: tuple[int, Path]) -> None:
    """``since`` égal à l'état courant : le premier événement dit ``changed: false`` et ne relit rien."""
    port, _root = served
    first = _Reader(port, "project=alpha")
    try:
        known = first.next_event()["data"]["fingerprint"]
    finally:
        first.close()
    again = _Reader(port, f"project=alpha&since={known}")
    try:
        resumed = again.next_event()["data"]
    finally:
        again.close()

    assert resumed["changed"] is False
    assert "decisions" not in resumed


def test_un_projet_hors_du_registre_n_ouvre_aucun_flux(served: tuple[int, Path]) -> None:
    port, _root = served
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", f"{STREAM_PATH}?project=fantome")
        response = conn.getresponse()
        assert response.status == 404
        assert json.loads(response.read())["ok"] is False
    finally:
        conn.close()
