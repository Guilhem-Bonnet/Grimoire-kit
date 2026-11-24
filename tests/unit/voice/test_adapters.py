"""Adaptateurs faster-whisper et pocket-tts, contre des bibliothèques simulées (aucun modèle, aucun réseau)."""

from __future__ import annotations

import io
import os
import wave
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from grimoire.voice.engines import EngineSlot, EngineUnavailableError, get_spec

# ── faster-whisper ────────────────────────────────────────────────────────────


class FakeWhisperModel:
    instances: ClassVar[list[FakeWhisperModel]] = []
    fail = False

    def __init__(self, model: str, **kwargs: Any) -> None:
        if type(self).fail:
            raise LocalEntryNotFoundError("snapshot not found in cache")
        self.model = model
        self.kwargs = kwargs
        self.calls: list[dict[str, Any]] = []
        type(self).instances.append(self)

    def transcribe(self, audio: Any, **kwargs: Any) -> tuple[Any, Any]:
        self.calls.append({"audio": audio, **kwargs})
        segments = iter([SimpleNamespace(text=" Bonjour "), SimpleNamespace(text="le kit. ")])
        return segments, SimpleNamespace(language="fr", duration=1.5)


@pytest.fixture
def fake_whisper(fake_module) -> type[FakeWhisperModel]:
    FakeWhisperModel.instances = []
    FakeWhisperModel.fail = False
    fake_module("faster_whisper", WhisperModel=FakeWhisperModel)
    return FakeWhisperModel


def test_whisper_uses_local_cache_only_by_default(fake_whisper) -> None:
    slot = EngineSlot(get_spec("stt", "faster-whisper"), {"language": "fr", "allow_download": False}, idle_unload_s=0)
    transcript = slot.run(lambda e: e.transcribe(b"\x00\x01", vocabulary=["Grimoire", " ", "cockpit"]))
    model = fake_whisper.instances[0]
    assert model.model == "small"
    assert model.kwargs == {"device": "cpu", "compute_type": "int8", "local_files_only": True}
    assert transcript.text == "Bonjour le kit."
    assert (transcript.language, transcript.duration_s) == ("fr", 1.5)
    call = model.calls[0]
    assert isinstance(call["audio"], io.BytesIO)
    assert call["language"] == "fr" and call["hotwords"] == "Grimoire cockpit"


def test_whisper_download_only_with_consent(fake_whisper) -> None:
    EngineSlot(get_spec("stt", "faster-whisper"), {"allow_download": True, "model": "base"}, idle_unload_s=0).run(
        lambda e: None,
    )
    assert fake_whisper.instances[0].kwargs["local_files_only"] is False


def test_whisper_missing_model_is_a_readable_reason(fake_whisper) -> None:
    fake_whisper.fail = True
    with pytest.raises(EngineUnavailableError) as err:
        EngineSlot(get_spec("stt", "faster-whisper"), idle_unload_s=0).run(lambda e: None)
    assert "cache local" in err.value.reason and "voice.allow_download: true" in err.value.reason
    assert "\n" not in err.value.reason


# ── pocket-tts ────────────────────────────────────────────────────────────────


class FakeTensor:
    def __init__(self, values: list[float]) -> None:
        self.values = values

    def reshape(self, *_: Any) -> FakeTensor:
        return self

    def clamp(self, lo: float, hi: float) -> FakeTensor:
        return FakeTensor([min(hi, max(lo, v)) for v in self.values])

    def __mul__(self, k: float) -> FakeTensor:
        return FakeTensor([v * k for v in self.values])

    def to(self, dtype: Any) -> FakeTensor:
        assert dtype == "int16"
        return FakeTensor([int(v) for v in self.values])

    def cpu(self) -> FakeTensor:
        return self

    def numpy(self) -> FakeTensor:
        return self

    def tobytes(self) -> bytes:
        return b"".join(int(v).to_bytes(2, "little", signed=True) for v in self.values)


class LocalEntryNotFoundError(Exception):
    """Même nom que celui de huggingface_hub : c'est lui que le moteur reconnaît."""


class FakeTTSModel:
    loads: ClassVar[list[dict[str, Any]]] = []
    offline_seen: ClassVar[list[str | None]] = []
    fail = False
    sample_rate = 24000

    @classmethod
    def load_model(cls, language: str | None = None) -> FakeTTSModel:
        cls.offline_seen.append(os.environ.get("HF_HUB_OFFLINE"))
        if cls.fail:
            raise LocalEntryNotFoundError("absent du cache")
        cls.loads.append({"language": language})
        return cls()

    def get_state_for_audio_prompt(self, voice: str) -> str:
        type(self).offline_seen.append(os.environ.get("HF_HUB_OFFLINE"))
        return f"state:{voice}"

    def generate_audio(self, state: str, text: str) -> FakeTensor:
        assert state.startswith("state:")
        return FakeTensor([0.0, 0.5, 2.0])


@pytest.fixture
def fake_pocket(fake_module, monkeypatch) -> type[FakeTTSModel]:
    FakeTTSModel.loads = []
    FakeTTSModel.offline_seen = []
    FakeTTSModel.fail = False
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    fake_module("pocket_tts", TTSModel=FakeTTSModel)
    fake_module(
        "pocket_tts.default_parameters",
        get_default_voice_for_language=lambda language: {"french": "estelle"}.get(language, "alba"),
    )
    fake_module("torch", int16="int16")
    monkeypatch.setattr("grimoire.voice.pocket_tts_engine.model_cached", lambda options: True)
    return FakeTTSModel


def test_pocket_loads_french_offline_and_returns_wav(fake_pocket) -> None:
    slot = EngineSlot(get_spec("tts", "pocket-tts"), {"language": "fr", "allow_download": False}, idle_unload_s=0)
    speech = slot.run(lambda e: e.synthesize("Bonjour."))
    assert fake_pocket.loads == [{"language": "french"}]
    assert "HF_HUB_OFFLINE" not in os.environ  # l'état du processus n'est jamais touché
    assert speech.sample_rate == 24000 and not speech.client_side
    with wave.open(io.BytesIO(speech.audio or b"")) as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 24000)
        frames = wav.readframes(wav.getnframes())
    assert [int.from_bytes(frames[i : i + 2], "little", signed=True) for i in (0, 2, 4)] == [0, 16383, 32767]


def test_pocket_with_consent_does_not_force_offline(fake_pocket) -> None:
    EngineSlot(get_spec("tts", "pocket-tts"), {"allow_download": True, "voice": "alba"}, idle_unload_s=0).run(
        lambda e: e.synthesize("x", voice="alba"),
    )
    assert "HF_HUB_OFFLINE" not in os.environ


def test_pocket_missing_model_is_a_readable_reason(fake_pocket) -> None:
    fake_pocket.fail = True
    with pytest.raises(EngineUnavailableError) as err:
        EngineSlot(get_spec("tts", "pocket-tts"), {"language": "fr"}, idle_unload_s=0).run(lambda e: None)
    assert "« french »" in err.value.reason and "voice.allow_download: true" in err.value.reason
    assert "HF_HUB_OFFLINE" not in os.environ



def test_pocket_other_failure_is_not_called_a_cache_miss(fake_pocket) -> None:
    fake_pocket.fail = False
    original = fake_pocket.load_model
    fake_pocket.load_model = classmethod(lambda cls, language=None: (_ for _ in ()).throw(MemoryError("plein")))
    try:
        with pytest.raises(EngineUnavailableError) as err:
            EngineSlot(get_spec("tts", "pocket-tts"), {"language": "fr"}, idle_unload_s=0).run(lambda e: None)
    finally:
        fake_pocket.load_model = original
    assert "introuvable dans le cache" not in err.value.reason and "MemoryError" in err.value.reason


def test_whisper_other_failure_is_not_called_a_cache_miss(fake_module) -> None:
    class Boom:
        def __init__(self, *a, **k) -> None:
            raise ValueError("compute type non supporté")

    fake_module("faster_whisper", WhisperModel=Boom)
    with pytest.raises(EngineUnavailableError) as err:
        EngineSlot(get_spec("stt", "faster-whisper"), {}, idle_unload_s=0).run(lambda e: None)
    assert "introuvable dans le cache" not in err.value.reason and "ValueError" in err.value.reason


def test_whisper_cache_miss_keeps_its_message(fake_module) -> None:
    class Miss:
        def __init__(self, *a, **k) -> None:
            raise LocalEntryNotFoundError("x")

    fake_module("faster_whisper", WhisperModel=Miss)
    with pytest.raises(EngineUnavailableError) as err:
        EngineSlot(get_spec("stt", "faster-whisper"), {}, idle_unload_s=0).run(lambda e: None)
    assert "introuvable dans le cache" in err.value.reason


def test_pocket_url_voice_is_refused_without_consent(fake_pocket) -> None:
    slot = EngineSlot(get_spec("tts", "pocket-tts"), {"voice": "https://exemple.test/v.wav"}, idle_unload_s=0)
    with pytest.raises(EngineUnavailableError) as err:
        slot.run(lambda e: None)
    assert "URL" in err.value.reason and "introuvable dans le cache" not in err.value.reason
    assert fake_pocket.loads == [{"language": "french"}]  # refusée avant tout appel du moteur à la voix


def test_pocket_url_voice_is_allowed_with_consent(fake_pocket) -> None:
    slot = EngineSlot(
        get_spec("tts", "pocket-tts"), {"voice": "hf://kyutai/voix.wav", "allow_download": True}, idle_unload_s=0
    )
    slot.run(lambda e: None)
    assert "HF_HUB_OFFLINE" not in os.environ


def test_pocket_absent_from_cache_is_refused_before_loading(fake_pocket, monkeypatch) -> None:
    monkeypatch.setattr("grimoire.voice.pocket_tts_engine.model_cached", lambda options: False)
    with pytest.raises(EngineUnavailableError) as err:
        EngineSlot(get_spec("tts", "pocket-tts"), {"language": "fr"}, idle_unload_s=0).run(lambda e: None)
    assert "introuvable dans le cache" in err.value.reason
    assert fake_pocket.loads == [] and fake_pocket.offline_seen == []  # rien n'a été chargé


def test_loading_never_touches_the_process_hub_state(fake_pocket, monkeypatch) -> None:
    import threading

    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    seen: list[str | None] = []
    original = FakeTTSModel.load_model.__func__

    def spy(cls, language=None):
        seen.append(os.environ.get("HF_HUB_OFFLINE"))
        return original(cls, language)

    monkeypatch.setattr(FakeTTSModel, "load_model", classmethod(spy))
    threads = [
        threading.Thread(
            target=lambda: EngineSlot(get_spec("tts", "pocket-tts"), {"language": "fr"}, idle_unload_s=0).run(
                lambda e: None
            )
        )
        for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert seen == ["0"] * 8 and os.environ["HF_HUB_OFFLINE"] == "0"


@pytest.mark.parametrize(
    "exc",
    [FileNotFoundError("voix.wav"), RuntimeError("CUDA out of memory")],
)
def test_other_failures_are_not_cache_misses(exc) -> None:
    from grimoire.voice._offline import is_cache_miss

    assert not is_cache_miss(exc)


def test_repository_not_found_is_not_a_cache_miss() -> None:
    from grimoire.voice._offline import is_cache_miss

    class RepositoryNotFoundError(Exception):
        pass

    assert not is_cache_miss(RepositoryNotFoundError("dépôt inexistant"))


def test_context_does_not_make_a_cache_miss() -> None:
    from grimoire.voice._offline import is_cache_miss

    try:
        try:
            raise FileNotFoundError("x")
        except FileNotFoundError:
            raise RuntimeError("CUDA OOM") from None
    except RuntimeError as exc:
        assert not is_cache_miss(exc)


def test_explicit_cause_local_entry_not_found_is_a_cache_miss() -> None:
    from grimoire.voice._offline import is_cache_miss

    try:
        raise RuntimeError("enveloppe") from LocalEntryNotFoundError("x")
    except RuntimeError as exc:
        assert is_cache_miss(exc)
