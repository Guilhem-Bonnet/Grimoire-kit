"""Le moteur Qwen3-TTS en clonage (#1343) : registre, défaut, cache sans réseau, fil dédié basse priorité."""

from __future__ import annotations

import io
import os
import sys
import threading
import wave
from pathlib import Path
from typing import Any, ClassVar

import pytest

from grimoire.voice import engines
from grimoire.voice.engines import EngineSlot, EngineUnavailableError, get_spec
from grimoire.voice.qwen3_tts_engine import (
    BUNDLED_TEXT,
    BUNDLED_VOICE,
    MODEL_FILES,
    MODEL_REPO,
    Qwen3CloneTextToSpeech,
    model_cached,
    split_sentences,
)
from grimoire.voice.settings import default_tts, settings_from_mapping

np = pytest.importorskip("numpy")
hub = pytest.importorskip("huggingface_hub")


def _fake_cache(root: Path, *, skip: tuple[str, ...] = ()) -> Path:
    """Un cache Hugging Face factice du dépôt 0.6B Base, sans le ou les fichiers de ``skip``."""
    repo = root / f"models--{MODEL_REPO.replace('/', '--')}"
    snap = repo / "snapshots" / "abc123"
    (repo / "refs").mkdir(parents=True)
    (repo / "refs" / "main").write_text("abc123")
    for name in MODEL_FILES:
        if name in skip:
            continue
        target = snap / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}")
    return snap


@pytest.fixture
def hf_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "hub"
    root.mkdir()
    monkeypatch.setattr(hub.constants, "HF_HUB_CACHE", str(root))
    return root


class _FakeModel:
    calls: ClassVar[list[dict[str, Any]]] = []
    seen: ClassVar[dict[str, Any]] = {}

    def __init__(self, source: str) -> None:
        self.source = source

    def generate_voice_clone(self, **kwargs: Any) -> tuple[list[Any], int]:
        _FakeModel.calls.append(kwargs)
        if sys.platform.startswith("linux"):
            _FakeModel.seen["nice"] = os.getpriority(os.PRIO_PROCESS, threading.get_native_id())
        _FakeModel.seen["thread"] = threading.current_thread()
        return [np.full(2400, 0.1, dtype=np.float32)], 24000


@pytest.fixture
def fake_qwen(fake_module: Any) -> type[_FakeModel]:
    _FakeModel.calls = []
    _FakeModel.seen = {}
    loaded: list[str] = []

    class Qwen3TTSModel:
        @staticmethod
        def from_pretrained(source: str) -> _FakeModel:
            loaded.append(source)
            return _FakeModel(source)

    Qwen3TTSModel.loaded = loaded  # type: ignore[attr-defined]
    fake_module("qwen_tts", Qwen3TTSModel=Qwen3TTSModel)
    _FakeModel.loader = Qwen3TTSModel  # type: ignore[attr-defined]
    return _FakeModel


# ── registre et défaut ────────────────────────────────────────────────────────


def test_engine_is_registered_with_its_own_extra() -> None:
    spec = get_spec("tts", "qwen3-tts-clone")
    assert spec.modules == ("qwen_tts",) and spec.extra == "voice-qwen"
    assert spec.cache_probe and spec.memory_hint


def test_qwen_extra_does_not_count_as_the_voice_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engines, "_module_installed", lambda m: m != "qwen_tts")
    assert engines.voice_extra_installed() is True


def test_default_is_qwen_when_extra_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engines, "_module_installed", lambda _m: True)
    assert default_tts() == "qwen3-tts-clone"
    assert settings_from_mapping({}).tts.engine == "qwen3-tts-clone"


def test_default_is_pocket_tts_without_the_qwen_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engines, "_module_installed", lambda m: m != "qwen_tts")
    assert settings_from_mapping({}).tts.engine == "pocket-tts"


def test_explicit_choice_wins_over_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engines, "_module_installed", lambda _m: True)
    assert settings_from_mapping({"voice": {"tts": "pocket-tts"}}).tts.engine == "pocket-tts"


# ── voix livrée ───────────────────────────────────────────────────────────────


def test_bundled_voice_and_its_transcript_ship_with_the_package() -> None:
    assert BUNDLED_VOICE.is_file() and BUNDLED_TEXT.read_text(encoding="utf-8").startswith("Les enfants")
    with wave.open(str(BUNDLED_VOICE)) as wav:
        assert wav.getnchannels() == 1 and 1 < wav.getnframes() / wav.getframerate() < 30
    provenance = (BUNDLED_VOICE.parent / "PROVENANCE.md").read_text(encoding="utf-8")
    assert "aucune personne réelle" in provenance and "Apache-2.0" in provenance


# ── cache sans réseau ─────────────────────────────────────────────────────────


def test_model_cached_reads_the_exact_files(hf_cache: Path) -> None:
    assert model_cached({}) is False
    _fake_cache(hf_cache)
    assert model_cached({}) is True


def test_one_missing_file_is_a_miss(hf_cache: Path) -> None:
    _fake_cache(hf_cache, skip=("speech_tokenizer/model.safetensors",))
    assert model_cached({}) is False


def test_offline_load_refuses_an_empty_cache_without_loading(hf_cache: Path, fake_qwen: Any) -> None:
    with pytest.raises(EngineUnavailableError, match="absent du cache"):
        Qwen3CloneTextToSpeech(allow_download=False)
    assert fake_qwen.loader.loaded == []  # le module n'a rien chargé


def test_offline_load_reads_the_local_snapshot_not_the_hub_id(hf_cache: Path, fake_qwen: Any) -> None:
    snap = _fake_cache(hf_cache)
    tts = Qwen3CloneTextToSpeech(allow_download=False)
    assert fake_qwen.loader.loaded == [str(snap)]
    tts.close()


def test_download_consent_loads_by_repo_id(fake_qwen: Any) -> None:
    Qwen3CloneTextToSpeech(allow_download=True).close()
    assert fake_qwen.loader.loaded == [MODEL_REPO]


def test_no_network_call_when_offline(hf_cache: Path, fake_qwen: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    def boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError("appel réseau")

    monkeypatch.setattr(socket.socket, "connect", boom)
    _fake_cache(hf_cache)
    Qwen3CloneTextToSpeech(allow_download=False).close()


# ── voix de l'utilisateur ─────────────────────────────────────────────────────


def test_url_voice_is_refused_even_with_download_consent(fake_qwen: Any) -> None:
    with pytest.raises(EngineUnavailableError, match="URL"):
        Qwen3CloneTextToSpeech(allow_download=True, voice="https://example.org/v.wav", ref_text="x")


def test_user_voice_needs_a_transcript(tmp_path: Path, fake_qwen: Any) -> None:
    wav = tmp_path / "ma-voix.wav"
    wav.write_bytes(b"RIFF")
    with pytest.raises(EngineUnavailableError, match="transcription"):
        Qwen3CloneTextToSpeech(allow_download=True, voice=str(wav))
    with pytest.raises(EngineUnavailableError, match="introuvable"):
        Qwen3CloneTextToSpeech(allow_download=True, voice=str(tmp_path / "absente.wav"), ref_text="x")


def test_user_voice_with_sidecar_text_is_used(tmp_path: Path, fake_qwen: Any) -> None:
    wav = tmp_path / "ma-voix.wav"
    wav.write_bytes(b"RIFF")
    wav.with_suffix(".txt").write_text("Bonjour à tous.\n", encoding="utf-8")
    tts = Qwen3CloneTextToSpeech(allow_download=True, voice=str(wav))
    tts.synthesize("Salut.")
    tts.close()
    assert fake_qwen.calls[0]["ref_audio"] == str(wav) and fake_qwen.calls[0]["ref_text"] == "Bonjour à tous."


# ── synthèse ──────────────────────────────────────────────────────────────────


def test_sentences_are_split() -> None:
    assert split_sentences("Un. Deux ! Trois ?\nQuatre") == ["Un.", "Deux !", "Trois ?", "Quatre"]
    assert split_sentences("  \n ") == []


def test_synthesis_is_sentence_by_sentence_with_the_bundled_voice(fake_qwen: Any) -> None:
    tts = Qwen3CloneTextToSpeech(allow_download=True, language="fr")
    speech = tts.synthesize("Bonjour. Comment allez-vous ?")
    tts.close()
    assert [c["text"] for c in fake_qwen.calls] == ["Bonjour.", "Comment allez-vous ?"]
    assert all(c["language"] == "French" and c["ref_audio"] == str(BUNDLED_VOICE) for c in fake_qwen.calls)
    assert fake_qwen.calls[0]["ref_text"].startswith("Les enfants")
    assert speech.sample_rate == 24000 and speech.audio
    with wave.open(io.BytesIO(speech.audio)) as wav:  # deux phrases + une pause entre elles
        assert wav.getnframes() == 2400 * 2 + 6000


def test_empty_text_gives_an_empty_wav(fake_qwen: Any) -> None:
    tts = Qwen3CloneTextToSpeech(allow_download=True)
    speech = tts.synthesize("   ")
    tts.close()
    assert fake_qwen.calls == [] and speech.audio


def test_synthesis_runs_off_the_calling_thread(fake_qwen: Any) -> None:
    tts = Qwen3CloneTextToSpeech(allow_download=True)
    tts.synthesize("Bonjour.")
    tts.close()
    assert fake_qwen.seen["thread"] is not threading.current_thread()
    assert fake_qwen.seen["thread"].name.startswith("grimoire-qwen3-tts")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="priorité par fil : Linux seulement")
def test_synthesis_runs_at_low_priority_without_slowing_the_caller(fake_qwen: Any) -> None:
    caller = os.getpriority(os.PRIO_PROCESS, threading.get_native_id())
    tts = Qwen3CloneTextToSpeech(allow_download=True)
    tts.synthesize("Bonjour.")
    tts.close()
    assert fake_qwen.seen["nice"] > caller
    assert os.getpriority(os.PRIO_PROCESS, threading.get_native_id()) == caller


def test_through_the_slot_and_unload(fake_qwen: Any) -> None:
    spec = get_spec("tts", "qwen3-tts-clone")
    slot = EngineSlot(spec, {"allow_download": True}, idle_unload_s=0)
    assert slot.run(lambda e: e.synthesize("Bonjour.")).audio
    assert slot.unload() is True
