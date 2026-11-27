"""Interface commune, registre et chargement à la demande des moteurs vocaux (#1343 V1).

Tous les moteurs sont simulés : aucune bibliothèque audio, aucun téléchargement.
"""

from __future__ import annotations

import time
from typing import Any, ClassVar

import pytest

from grimoire.voice import engines
from grimoire.voice.engines import (
    EngineCallError,
    EngineSlot,
    EngineSpec,
    EngineUnavailableError,
    Speech,
    SpeechToText,
    TextToSpeech,
    Transcript,
    VoiceEngines,
    availability,
    get_spec,
    install_hint,
    register_engine,
)
from grimoire.voice.settings import settings_from_mapping

MISSING = "grimoire_test_voice_module_absent"


class FakeSTT(SpeechToText):
    loads = 0
    closes = 0
    last_options: ClassVar[dict[str, Any]] = {}

    def __init__(self, **options: Any) -> None:
        type(self).loads += 1
        type(self).last_options = options
        self.calls: list[tuple[Any, ...]] = []

    def transcribe(self, audio, *, language=None, vocabulary=()):  # type: ignore[no-untyped-def]
        self.calls.append((audio, language, tuple(vocabulary)))
        return Transcript(text="bonjour", language=language)

    def close(self) -> None:
        type(self).closes += 1


class FakeTTS(TextToSpeech):
    last_options: ClassVar[dict[str, Any]] = {}

    def __init__(self, **options: Any) -> None:
        type(self).last_options = options

    def synthesize(self, text: str, *, voice: str | None = None) -> Speech:
        return Speech(text=text, audio=b"RIFF", sample_rate=24000)


@pytest.fixture(autouse=True)
def _reset_fakes() -> None:
    FakeSTT.loads = FakeSTT.closes = 0
    FakeSTT.last_options = FakeTTS.last_options = {}


@pytest.fixture
def fake_engines(fake_module, isolated_registry) -> tuple[EngineSpec, EngineSpec]:
    fake_module("grimoire_test_fake_voice", FakeSTT=FakeSTT, FakeTTS=FakeTTS)
    stt = EngineSpec("fake", "stt", "Faux STT", "grimoire_test_fake_voice:FakeSTT", ("grimoire_test_fake_voice",))
    tts = EngineSpec("fake", "tts", "Faux TTS", "grimoire_test_fake_voice:FakeTTS", ("grimoire_test_fake_voice",))
    register_engine(stt)
    register_engine(tts)
    return stt, tts


# ── Registre ──────────────────────────────────────────────────────────────────


def test_registry_lists_the_three_engine_families() -> None:
    assert engines.engine_names("stt") == ["browser", "faster-whisper"]
    assert engines.engine_names("tts") == ["browser", "pocket-tts", "qwen3-tts-clone"]
    assert get_spec("stt", "faster-whisper").modules == ("faster_whisper",)
    assert get_spec("tts", "pocket-tts").modules == ("pocket_tts",)
    assert get_spec("tts", "browser").modules == ()


def test_unknown_engine_gives_a_readable_reason() -> None:
    with pytest.raises(EngineUnavailableError) as err:
        get_spec("tts", "qwen9")
    assert "« qwen9 » inconnu" in err.value.reason
    assert "pocket-tts" in err.value.reason


def test_availability_reads_the_path_without_importing(isolated_registry) -> None:
    spec = EngineSpec("x", "stt", "Moteur X", f"{MISSING}:X", (MISSING,))
    avail = availability(spec)
    assert not avail.ok
    assert MISSING in avail.reason and install_hint() in avail.reason


def test_browser_fallback_needs_no_model() -> None:
    assert availability(get_spec("tts", "browser")).ok
    slot = EngineSlot(get_spec("tts", "browser"), idle_unload_s=0)
    speech = slot.run(lambda e: e.synthesize("salut"))
    assert speech == Speech(text="salut", client_side=True)
    stt = EngineSlot(get_spec("stt", "browser"), idle_unload_s=0)
    with pytest.raises(EngineCallError) as err:
        stt.run(lambda e: e.transcribe(b"..."))
    assert "navigateur" in err.value.reason


# ── Chargement au premier appel, raisons lisibles ─────────────────────────────


def test_engine_loads_on_first_call_only(fake_engines, clock, timers) -> None:
    slot = EngineSlot(fake_engines[0], {"model": "tiny"}, idle_unload_s=60, clock=clock, timer_factory=timers)
    assert not slot.loaded and FakeSTT.loads == 0
    assert slot.run(lambda e: e.transcribe(b"a")).text == "bonjour"
    slot.run(lambda e: e.transcribe(b"b"))
    assert slot.loaded and FakeSTT.loads == 1
    assert FakeSTT.last_options == {"model": "tiny"}


def test_not_installed_engine_raises_reason_not_traceback(isolated_registry) -> None:
    slot = EngineSlot(EngineSpec("x", "stt", "Moteur X", f"{MISSING}:X", (MISSING,)), idle_unload_s=0)
    with pytest.raises(EngineUnavailableError) as err:
        slot.run(lambda e: e)
    assert install_hint() in err.value.reason
    assert err.value.__cause__ is None
    assert not slot.loaded


def test_import_error_of_the_adapter_is_a_reason(fake_module) -> None:
    fake_module("grimoire_test_dep_present")
    spec = EngineSpec("x", "stt", "Moteur X", "grimoire_test_adapter_absent:X", ("grimoire_test_dep_present",))
    with pytest.raises(EngineUnavailableError) as err:
        EngineSlot(spec, idle_unload_s=0).run(lambda e: e)
    assert err.value.reason.startswith("Moteur X ne s'importe pas (ModuleNotFoundError")
    assert "\n" not in err.value.reason and "Traceback" not in err.value.reason


def test_constructor_failure_is_a_one_line_reason(fake_module) -> None:
    class Broken(SpeechToText):
        def __init__(self, **_: Any) -> None:
            raise RuntimeError("pas de\nmémoire")

        def transcribe(self, audio, *, language=None, vocabulary=()):  # type: ignore[no-untyped-def]
            raise AssertionError

    fake_module("grimoire_test_broken", Broken=Broken)
    spec = EngineSpec("b", "stt", "Cassé", "grimoire_test_broken:Broken", ("grimoire_test_broken",))
    slot = EngineSlot(spec, idle_unload_s=0)
    with pytest.raises(EngineUnavailableError) as err:
        slot.run(lambda e: e)
    assert err.value.reason == "Cassé n'a pas pu se charger (RuntimeError: pas de mémoire)"
    assert not slot.loaded


def test_call_failure_is_a_readable_engine_failure(fake_engines) -> None:
    slot = EngineSlot(fake_engines[0], idle_unload_s=0)

    def boom(_engine: Any) -> None:
        raise ValueError("audio illisible")

    with pytest.raises(EngineCallError) as err:
        slot.run(boom)
    assert err.value.reason == "Faux STT a échoué (ValueError: audio illisible)"
    assert slot.loaded  # le moteur reste chargé : seul l'appel a échoué


# ── Déchargement ──────────────────────────────────────────────────────────────


def test_unload_on_demand(fake_engines, clock, timers) -> None:
    slot = EngineSlot(fake_engines[0], idle_unload_s=60, clock=clock, timer_factory=timers)
    slot.run(lambda e: None)
    assert slot.unload() is True
    assert not slot.loaded and FakeSTT.closes == 1
    assert timers.armed is None
    assert slot.unload() is False
    slot.run(lambda e: None)
    assert FakeSTT.loads == 2


def test_idle_unload_after_the_configured_delay(fake_engines, clock, timers) -> None:
    slot = EngineSlot(fake_engines[0], idle_unload_s=60, clock=clock, timer_factory=timers)
    slot.run(lambda e: None)
    assert timers.armed is not None and timers.armed.delay == 60

    clock.now += 59
    assert timers.armed.callback() is False  # pas encore : le minuteur est réarmé pour le reste
    assert slot.loaded and timers.armed.delay == pytest.approx(1)

    clock.now += 1
    assert timers.armed.callback() is True
    assert not slot.loaded and FakeSTT.closes == 1


def test_each_call_pushes_the_idle_deadline(fake_engines, clock, timers) -> None:
    slot = EngineSlot(fake_engines[0], idle_unload_s=60, clock=clock, timer_factory=timers)
    slot.run(lambda e: None)
    clock.now += 50
    slot.run(lambda e: None)
    clock.now += 50
    assert slot.reap_idle() is False
    clock.now += 10
    assert slot.reap_idle() is True


def test_zero_delay_never_unloads_by_itself(fake_engines, clock, timers) -> None:
    slot = EngineSlot(fake_engines[0], idle_unload_s=0, clock=clock, timer_factory=timers)
    slot.run(lambda e: None)
    clock.now += 10**6
    assert timers.armed is None
    assert slot.reap_idle() is False and slot.loaded


def test_real_timer_unloads_an_idle_engine(fake_engines) -> None:
    slot = EngineSlot(fake_engines[0], idle_unload_s=0.05)
    slot.run(lambda e: None)
    deadline = time.monotonic() + 5
    while slot.loaded and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not slot.loaded and FakeSTT.closes == 1


# ── La paire d'un projet ──────────────────────────────────────────────────────


def test_voice_engines_from_settings_loads_nothing_until_used(fake_engines, clock, timers) -> None:
    settings = settings_from_mapping(
        {
            "interaction": {"mode": "voice"},
            "voice": {"stt": {"engine": "fake", "model": "tiny"}, "tts": "fake", "idle_unload_seconds": 30},
        }
    )
    pair = VoiceEngines.from_settings(settings, clock=clock, timer_factory=timers)
    assert not pair.stt.loaded and not pair.tts.loaded
    assert pair.stt.idle_unload_s == 30

    assert pair.transcribe(b"x", vocabulary=["grimoire"]).text == "bonjour"
    assert pair.stt.loaded and not pair.tts.loaded
    assert FakeSTT.last_options == {"model": "tiny", "allow_download": False, "language": "fr"}

    assert pair.synthesize("ok").audio == b"RIFF"
    pair.unload()
    assert not pair.stt.loaded and not pair.tts.loaded


def test_browser_engines_receive_no_download_option() -> None:
    pair = VoiceEngines.from_settings(settings_from_mapping({"voice": {"stt": "browser", "tts": "browser"}}))
    assert pair.stt.options == {} and pair.tts.options == {}


def test_unknown_engine_in_settings_is_refused_with_a_reason() -> None:
    with pytest.raises(EngineUnavailableError) as err:
        VoiceEngines.from_settings(settings_from_mapping({"voice": {"stt": "nope"}}))
    assert "« nope » inconnu" in err.value.reason


def test_project_download_switch_overrides_the_engine_one(fake_engines, clock, timers) -> None:
    for project, engine, expected in ((False, True, False), (True, False, True)):
        settings = settings_from_mapping(
            {"voice": {"allow_download": project, "stt": {"engine": "fake", "allow_download": engine}, "tts": "fake"}}
        )
        VoiceEngines.from_settings(settings, clock=clock, timer_factory=timers).transcribe(b"x")
        assert FakeSTT.last_options["allow_download"] is expected
