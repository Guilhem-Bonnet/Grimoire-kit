"""Message vocal entrant (lot V2) : ``voice/config`` et ``voice/transcribe`` sur un vrai cockpit, moteur simulé.

Aucun modèle, aucune bibliothèque audio : le moteur est un module factice du registre.
"""

from __future__ import annotations

import base64
import struct
import threading
from collections.abc import Callable, Iterator, Sequence
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from grimoire.cli import cmd_cockpit
from grimoire.tools import project_registry
from grimoire.voice import stt_service
from grimoire.voice.engines import EngineCallError, EngineSpec, SpeechToText, Transcript, register_engine

from .. import _cockpit_http

CONFIG = "/api/workspace/voice/config?project=home"
TRANSCRIBE = "/api/workspace/voice/transcribe?project=home"


def wav(seconds: float = 0.1) -> bytes:
    n = int(16_000 * seconds) * 2
    header = (
        b"RIFF" + struct.pack("<I", 36 + n) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 16_000, 32_000, 2, 16)
    )
    return header + b"data" + struct.pack("<I", n) + b"\0" * n


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


class Calls:
    """Ce que le moteur simulé a reçu."""

    def __init__(self) -> None:
        self.paths: list[Path] = []
        self.existed: list[bool] = []
        self.vocabulary: list[Sequence[str]] = []
        self.language: list[str | None] = []
        self.fail = False


@pytest.fixture
def calls(fake_module: Callable[..., Any], isolated_registry: dict) -> Iterator[Calls]:
    log = Calls()

    class FakeSTT(SpeechToText):
        def __init__(self, **_options: Any) -> None:
            pass

        def transcribe(self, audio: Any, *, language: str | None = None, vocabulary: Sequence[str] = ()) -> Transcript:
            log.paths.append(audio)
            log.existed.append(Path(audio).is_file())
            log.vocabulary.append(tuple(vocabulary))
            log.language.append(language)
            if log.fail:
                raise EngineCallError("le moteur simulé a échoué")
            return Transcript(text="bonjour   le monde", language="fr", duration_s=0.1)

    fake_module("fake_stt_engine", FakeSTT=FakeSTT)
    register_engine(EngineSpec("fake", "stt", "Moteur simulé", "fake_stt_engine:FakeSTT", ("fake_stt_engine",)))
    yield log
    stt_service.reset_engines()


def _project(root: Path, context: str) -> Path:
    root.mkdir(parents=True)
    (root / "project-context.yaml").write_text(context, encoding="utf-8")
    return root


VOICE_ON = "project:\n  name: home\ninteraction:\n  mode: voice\nvoice:\n  stt: fake\n  max_record_seconds: 5\n"


@pytest.fixture
def served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[str], tuple[int, Path]]]:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "ck"))
    servers: list[Any] = []

    def start(context: str) -> tuple[int, Path]:
        home = _project(tmp_path / "home", context)
        project_registry.save_registry([{"name": "Home", "path": str(home), "slug": "home"}])
        monkeypatch.setattr(cmd_cockpit, "_OPENING_SLUG", "home")
        monkeypatch.setattr(cmd_cockpit, "_OPENING_PATH", home.resolve())
        httpd = cmd_cockpit.ThreadingHTTPServer(
            ("127.0.0.1", 0), partial(cmd_cockpit._CockpitHandler, directory=str(tmp_path))
        )
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        return httpd.server_address[1], home

    yield start
    for httpd in servers:
        httpd.shutdown()
        httpd.server_close()


def test_stt_config_offers_the_button_in_voice_mode(served: Any, calls: Calls) -> None:
    port, _ = served(VOICE_ON)
    status, body = _cockpit_http.get(port, CONFIG)
    assert status == 200
    assert body["available"] is True and body["max_seconds"] == 5 and body["sample_rate"] == 16_000


def test_stt_transcribes_with_project_vocabulary_then_deletes_the_audio(served: Any, calls: Calls) -> None:
    port, _ = served(VOICE_ON)
    status, body = _cockpit_http.post(port, TRANSCRIBE, {"audio": b64(wav())})
    assert status == 200, body
    assert body["text"] == "bonjour   le monde"
    assert calls.existed == [True]  # l'audio existait pendant l'appel…
    assert not calls.paths[0].exists()  # … et plus après
    assert "home" in calls.vocabulary[0]  # nom du projet
    assert calls.language == ["fr"]


def test_stt_audio_is_deleted_when_the_engine_fails(served: Any, calls: Calls) -> None:
    port, _ = served(VOICE_ON)
    calls.fail = True
    status, body = _cockpit_http.post(port, TRANSCRIBE, {"audio": b64(wav())})
    assert status == 400 and "le moteur simulé a échoué" in body["error"]
    assert not calls.paths[0].exists()


def test_stt_route_is_404_with_a_reason_when_the_extra_is_missing(served: Any, calls: Calls) -> None:
    port, _ = served("project:\n  name: home\ninteraction:\n  mode: voice\n")  # faster-whisper par défaut, absent ici
    status, config = _cockpit_http.get(port, CONFIG)
    if config["available"]:  # un poste de dev qui a l'extra : rien à vérifier ici
        pytest.skip("l'extra voice est installé")
    assert status == 200 and config["available"] is False and "voice" in config["reason"]
    status, body = _cockpit_http.post(port, TRANSCRIBE, {"audio": b64(wav())})
    assert status == 404 and "vocal indisponible" in body["error"] and "voice" in body["error"]


def test_stt_route_is_404_when_voice_mode_is_off(served: Any, calls: Calls) -> None:
    port, _ = served("project:\n  name: home\nvoice:\n  stt: fake\n")
    _, config = _cockpit_http.get(port, CONFIG)
    assert config["available"] is False and "éteint" in config["reason"]
    status, body = _cockpit_http.post(port, TRANSCRIBE, {"audio": b64(wav())})
    assert status == 404 and "éteint" in body["error"]
    assert calls.paths == []


def test_stt_route_needs_the_session_token_and_the_origin(served: Any, calls: Calls) -> None:
    port, _ = served(VOICE_ON)
    payload = {"audio": b64(wav())}
    status, body = _cockpit_http.post(port, TRANSCRIBE, payload, headers={"X-Grimoire-Session": "faux"})
    assert status == 403
    status, body = _cockpit_http.post(port, TRANSCRIBE, payload, headers={"Origin": "http://evil.example"})
    assert status == 403 and "origine" in body["error"]
    assert calls.paths == []


def test_stt_audio_over_the_cap_is_refused_before_the_engine(served: Any, calls: Calls) -> None:
    port, _ = served(VOICE_ON)  # plafond : 5 s
    status, body = _cockpit_http.post(port, TRANSCRIBE, {"audio": b64(wav(7))})
    assert status == 400 and "trop long" in body["error"]
    assert calls.paths == []


def test_stt_body_over_the_absolute_ceiling_is_413(served: Any, calls: Calls) -> None:
    port, _ = served(VOICE_ON)
    big = b64(b"\0" * (stt_service.MAX_BODY_BYTES + 1024))
    status, _body = _cockpit_http.post(port, TRANSCRIBE, {"audio": big}, timeout=60)
    assert status == 413
    assert calls.paths == []


@pytest.mark.parametrize("audio", ["", "pas du base64 !!", b64(b"pas un wav, mais assez long" * 5)])
def test_stt_rejects_unreadable_audio(served: Any, calls: Calls, audio: str) -> None:
    port, _ = served(VOICE_ON)
    status, _body = _cockpit_http.post(port, TRANSCRIBE, {"audio": audio})
    assert status == 400
    assert calls.paths == []


def test_stt_vocabulary_names_project_branch_and_recent_files(tmp_path: Path) -> None:
    import subprocess

    root = tmp_path / "mon-projet"
    root.mkdir()
    run = lambda *a: subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True)  # noqa: E731
    run("init", "-q", "-b", "feature/dictee")
    (root / "notes.md").write_text("x")
    run("add", ".")
    run("-c", "user.name=t", "-c", "user.email=t@example.org", "commit", "-q", "-m", "x")
    words = stt_service.project_vocabulary(root)
    assert {"mon-projet", "feature/dictee", "notes.md"} <= set(words)


def test_stt_dictation_adds_the_notice_and_keeps_the_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from grimoire.tools import workspace_chat

    seen: list[str] = []

    class Conv:
        def send(self, text: str, *_a: Any) -> dict[str, Any]:
            seen.append(text)
            return {"ok": True}

    monkeypatch.setattr(workspace_chat, "_conversation", lambda _root: Conv())
    workspace_chat.chat_send(tmp_path, {"text": "ouvre la tâche 12", "dictation": True})
    workspace_chat.chat_send(tmp_path, {"text": "tapé au clavier"})
    assert seen[0] == f"ouvre la tâche 12\n\n{workspace_chat.DICTATION_NOTICE}"
    assert "dictée vocale, erreurs de reconnaissance possibles" in seen[0]
    assert seen[1] == "tapé au clavier"


def test_stt_settings_max_record_seconds_falls_back_when_invalid() -> None:
    from grimoire.voice.settings import settings_from_mapping

    bad = settings_from_mapping({"voice": {"max_record_seconds": 99999}})
    assert bad.max_record_s == 120.0 and any("max_record_seconds" in p for p in bad.problems)
    assert settings_from_mapping({"voice": {"max_record_seconds": 30}}).max_record_s == 30.0
