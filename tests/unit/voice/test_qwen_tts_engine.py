"""Adaptateur qwen3-tts-clone, contre un module ``qwen_tts`` simulé et un faux cache Hugging Face (tmp_path)."""

from __future__ import annotations

import sys
import types
import wave
from io import BytesIO
from pathlib import Path
from typing import Any, ClassVar

import pytest

from grimoire.voice import qwen_tts_engine
from grimoire.voice.engines import EngineSlot, EngineUnavailableError, get_spec
from grimoire.voice.qwen_tts_engine import MODEL_FILES, MODEL_REPO
from grimoire.voice.settings import default_tts, settings_from_mapping

REV = "b" * 40


class FakeModel:
    loads: ClassVar[list[dict[str, Any]]] = []
    calls: ClassVar[list[dict[str, Any]]] = []
    fail: Exception | None = None

    @classmethod
    def from_pretrained(cls, repo: str, **kwargs: Any) -> FakeModel:
        if cls.fail is not None:
            raise cls.fail
        cls.loads.append({"repo": repo, **kwargs})
        return cls()

    def generate_voice_clone(self, **kwargs: Any) -> tuple[list[Any], int]:
        FakeModel.calls.append(kwargs)
        return [[0.0, 0.5, -0.5, 2.0]], 24000


class FakeTorch(types.ModuleType):
    def __init__(self) -> None:
        super().__init__("torch")
        self.threads = 8
        self.history: list[int] = []

    def get_num_threads(self) -> int:
        return self.threads

    def set_num_threads(self, n: int) -> None:
        self.history.append(n)
        self.threads = n


@pytest.fixture(autouse=True)
def _reset() -> None:
    FakeModel.loads, FakeModel.calls, FakeModel.fail = [], [], None


@pytest.fixture
def fake_qwen(fake_module: Any, monkeypatch: pytest.MonkeyPatch) -> FakeTorch:
    pytest.importorskip("numpy")
    fake_module("qwen_tts", Qwen3TTSModel=FakeModel)
    torch = FakeTorch()
    monkeypatch.setitem(sys.modules, "torch", torch)
    return torch


@pytest.fixture
def hf_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from huggingface_hub import constants

    cache = tmp_path / "hub"
    cache.mkdir()
    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(cache))
    return cache


def _fill(cache: Path, files: tuple[str, ...] = MODEL_FILES) -> None:
    root = cache / ("models--" + MODEL_REPO.replace("/", "--"))
    (root / "refs").mkdir(parents=True, exist_ok=True)
    (root / "refs" / "main").write_text(REV)
    for name in files:
        target = root / "snapshots" / REV / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x")


def _slot(**options: Any) -> EngineSlot:
    return EngineSlot(get_spec("tts", "qwen3-tts-clone"), options, idle_unload_s=0)


def test_clone_call_uses_language_shipped_reference_and_transcript(fake_qwen, hf_cache) -> None:
    _fill(hf_cache)
    speech = _slot(language="fr").run(lambda e: e.synthesize("Bonjour"))
    call = FakeModel.calls[0]
    assert call["text"] == "Bonjour" and call["language"] == "French"
    assert call["ref_audio"].endswith("reference-fr.wav") and Path(call["ref_audio"]).is_file()
    assert call["ref_text"] == "Les enfants ont aimé ces beaux oiseaux, ils les ont observés pendant deux heures."
    assert speech.sample_rate == 24000 and speech.audio
    with wave.open(BytesIO(speech.audio)) as wav:
        assert wav.getframerate() == 24000 and wav.getnframes() == 4


def test_english_language(fake_qwen, hf_cache) -> None:
    _fill(hf_cache)
    _slot(language="en").run(lambda e: e.synthesize("Hello"))
    assert FakeModel.calls[0]["language"] == "English"


def test_loads_local_only_when_cache_is_complete(fake_qwen, hf_cache) -> None:
    _fill(hf_cache)
    _slot(language="fr").run(lambda e: None)
    assert len(FakeModel.loads) == 1
    load = FakeModel.loads[0]
    assert load["repo"] != MODEL_REPO  # un CHEMIN de snapshot : aucun appel au hub, processeur compris
    assert Path(load["repo"]).is_dir() and Path(load["repo"]).parent.name == "snapshots"
    assert str(hf_cache) in load["repo"] and load["local_files_only"] is True


def test_refuses_without_cache_and_without_network(fake_qwen, hf_cache) -> None:
    with pytest.raises(EngineUnavailableError, match="introuvable dans le cache local"):
        _slot(language="fr").run(lambda e: None)
    assert FakeModel.loads == []  # rien chargé, donc aucun réseau


def test_partial_cache_is_absent(fake_qwen, hf_cache) -> None:
    _fill(hf_cache, MODEL_FILES[:-1])
    with pytest.raises(EngineUnavailableError, match="introuvable dans le cache local"):
        _slot(language="fr").run(lambda e: None)
    assert qwen_tts_engine.model_cached() is False


def test_download_consent_skips_cache_check_and_offline_flag(fake_qwen, hf_cache) -> None:
    _slot(language="fr", allow_download=True).run(lambda e: None)
    assert FakeModel.loads == [{"repo": MODEL_REPO}]


def test_no_local_files_only_when_from_pretrained_rejects_it(fake_qwen, hf_cache) -> None:
    _fill(hf_cache)
    seen: list[str] = []

    class Strict:
        @classmethod
        def from_pretrained(cls, repo: str) -> Strict:
            seen.append(repo)
            return cls()

    sys.modules["qwen_tts"].Qwen3TTSModel = Strict  # type: ignore[attr-defined]
    _slot(language="fr").run(lambda e: None)
    assert len(seen) == 1 and seen[0] != MODEL_REPO and Path(seen[0]).is_dir()


def test_cache_miss_raised_by_loader_is_readable(fake_qwen, hf_cache) -> None:
    _fill(hf_cache)

    class LocalEntryNotFoundError(Exception):
        pass

    FakeModel.fail = LocalEntryNotFoundError("nope")
    with pytest.raises(EngineUnavailableError, match="introuvable dans le cache local"):
        _slot(language="fr").run(lambda e: None)


def test_other_load_failure_is_not_called_a_cache_miss(fake_qwen, hf_cache) -> None:
    _fill(hf_cache)
    FakeModel.fail = RuntimeError("boom")
    with pytest.raises(EngineUnavailableError) as err:
        _slot(language="fr").run(lambda e: None)
    assert "cache" not in err.value.reason and "boom" in err.value.reason


def test_custom_local_voice_replaces_reference(fake_qwen, hf_cache, tmp_path: Path) -> None:
    _fill(hf_cache)
    audio = tmp_path / "ma-voix.wav"
    audio.write_bytes(b"RIFF")
    audio.with_suffix(".txt").write_text("  Bonjour tout le monde.\n", encoding="utf-8")
    _slot(language="fr", voice=str(audio)).run(lambda e: e.synthesize("Salut"))
    assert FakeModel.calls[0]["ref_audio"] == str(audio)
    assert FakeModel.calls[0]["ref_text"] == "Bonjour tout le monde."


def test_custom_voice_without_transcript_is_refused(fake_qwen, hf_cache, tmp_path: Path) -> None:
    _fill(hf_cache)
    audio = tmp_path / "v.wav"
    audio.write_bytes(b"RIFF")
    with pytest.raises(EngineUnavailableError, match="transcription"):
        _slot(voice=str(audio)).run(lambda e: None)


def test_url_voice_is_refused_even_with_consent(fake_qwen, hf_cache) -> None:
    _fill(hf_cache)
    for consent in (False, True):
        with pytest.raises(EngineUnavailableError, match="URL"):
            _slot(voice="https://example.org/v.wav", allow_download=consent).run(lambda e: None)
    assert FakeModel.loads == []


def test_threads_are_limited_during_synthesis_then_restored(fake_qwen, hf_cache) -> None:
    _fill(hf_cache)
    _slot(language="fr", threads=3).run(lambda e: e.synthesize("a"))
    assert fake_qwen.history == [3, 8] and fake_qwen.threads == 8


def test_default_threads_are_half_the_cores(fake_qwen, hf_cache, monkeypatch: pytest.MonkeyPatch) -> None:
    _fill(hf_cache)
    monkeypatch.setattr("os.cpu_count", lambda: 12)
    _slot(language="fr").run(lambda e: e.synthesize("a"))
    assert fake_qwen.history[0] == 6


def test_default_engine_follows_installed_packages(monkeypatch: pytest.MonkeyPatch) -> None:
    def installed(*names: str) -> None:
        monkeypatch.setattr("grimoire.voice.engines._module_installed", lambda m: m in names)

    installed("qwen_tts", "pocket_tts")
    assert default_tts() == "qwen3-tts-clone"
    installed("pocket_tts")
    assert default_tts() == "pocket-tts"
    installed()
    assert default_tts() == "browser"
    assert settings_from_mapping({}).tts.engine == "browser"


def test_default_engine_spawns_no_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lu à chaque tour du chat en mode vocal : jamais de sous-processus (détection du gestionnaire de paquets)."""
    monkeypatch.setattr("grimoire.voice.engines._module_installed", lambda _m: False)

    def forbidden(*_a: object, **_k: object) -> None:
        raise AssertionError("sous-processus lancé")

    monkeypatch.setattr("subprocess.run", forbidden)
    assert default_tts() == "browser"


def test_explicit_engine_wins_over_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("grimoire.voice.engines._module_installed", lambda _m: True)
    assert settings_from_mapping({"voice": {"tts": "pocket-tts"}}).tts.engine == "pocket-tts"
    assert settings_from_mapping({"voice": {"tts": "browser"}}).tts.engine == "browser"


def test_registry_and_extra() -> None:
    spec = get_spec("tts", "qwen3-tts-clone")
    assert spec.modules == ("qwen_tts",) and spec.extra == "voice-qwen"
    assert "voice-qwen" in __import__("grimoire.voice.engines", fromlist=["x"]).install_hint(spec.extra)


def test_shipped_reference_files_exist() -> None:
    ref = qwen_tts_engine.resolve_reference(None)
    assert ref.audio.is_file() and ref.text.startswith("Les enfants")
