"""``model_cached`` des deux moteurs, exécuté pour de bon contre un faux cache Hugging Face (tmp_path)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import pytest

from grimoire.voice import faster_whisper_engine, pocket_tts_engine
from grimoire.voice.engines import EngineSlot, EngineUnavailableError, get_spec

REV = "a" * 40
FR_WEIGHTS = f"hf://kyutai/pocket-tts/languages/french/model.safetensors@{REV}"
FR_NO_CLONE = f"hf://kyutai/pocket-tts-without-voice-cloning/languages/french/model.safetensors@{REV}"
FR_TOKENIZER = f"hf://kyutai/pocket-tts-without-voice-cloning/languages/french/tokenizer.json@{REV}"
ESTELLE = f"hf://kyutai/tts-voices/unmute-prod-website/developpeuse-3.wav@{REV}"
CONFIG = f"""\
weights_path: {FR_WEIGHTS}
weights_path_without_voice_cloning: {FR_NO_CLONE}
flow_lm:
  lookup_table:
    tokenizer_path: {FR_TOKENIZER}
"""


def _put(cache: Path, repo: str, filename: str, revision: str = REV) -> None:
    """Dépose un fichier au format du cache Hugging Face : refs/, snapshots/<révision>/."""
    root = cache / ("models--" + repo.replace("/", "--"))
    (root / "refs").mkdir(parents=True, exist_ok=True)
    (root / "refs" / "main").write_text(revision)
    target = root / "snapshots" / revision / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"x")


@pytest.fixture
def hf_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from huggingface_hub import constants

    cache = tmp_path / "hub"
    cache.mkdir()
    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(cache))
    return cache


@pytest.fixture
def pocket_lib(fake_module: Any, tmp_path: Path) -> Path:
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "french.yaml").write_text(CONFIG)
    fake_module("pocket_tts")
    fake_module("pocket_tts.utils")
    fake_module("pocket_tts.utils.config", CONFIGS_DIR=configs)
    fake_module(
        "pocket_tts.utils.utils",
        _ORIGINS_OF_PREDEFINED_VOICES={"estelle": ESTELLE},
        get_predefined_voice=lambda language, name: (
            f"hf://kyutai/pocket-tts-without-voice-cloning/languages/{language}/embeddings/{name}.safetensors@{REV}"
        ),
    )
    fake_module("pocket_tts.default_parameters", get_default_voice_for_language=lambda _l: "estelle")
    return configs


def _fill_french(cache: Path) -> None:
    _put(cache, "kyutai/pocket-tts-without-voice-cloning", "languages/french/model.safetensors")
    _put(cache, "kyutai/pocket-tts-without-voice-cloning", "languages/french/tokenizer.json")
    _put(cache, "kyutai/tts-voices", "unmute-prod-website/developpeuse-3.wav")


def test_pocket_other_language_repo_is_not_enough(hf_cache, pocket_lib) -> None:
    """Le cas du relecteur : seul le dépôt anglais est en cache, la langue demandée est le français."""
    _put(hf_cache, "kyutai/pocket-tts-en", "model.safetensors")
    assert pocket_tts_engine.model_cached({"language": "fr"}) is False


def test_pocket_empty_cache_is_absent(hf_cache, pocket_lib) -> None:
    assert pocket_tts_engine.model_cached({"language": "fr"}) is False


def test_pocket_all_files_and_default_voice_cached(hf_cache, pocket_lib) -> None:
    _fill_french(hf_cache)
    assert pocket_tts_engine.model_cached({"language": "fr", "voice": "estelle"}) is True


def test_pocket_missing_tokenizer_is_absent(hf_cache, pocket_lib) -> None:
    _fill_french(hf_cache)
    (
        hf_cache
        / "models--kyutai--pocket-tts-without-voice-cloning/snapshots"
        / REV
        / "languages/french/tokenizer.json"
    ).unlink()
    assert pocket_tts_engine.model_cached({"language": "fr"}) is False


def test_pocket_either_weights_bundle_suffices(hf_cache, pocket_lib) -> None:
    _put(hf_cache, "kyutai/pocket-tts", "languages/french/model.safetensors")
    _put(hf_cache, "kyutai/pocket-tts-without-voice-cloning", "languages/french/tokenizer.json")
    assert pocket_tts_engine.model_cached({"language": "fr"}) is True


def test_pocket_voice_not_cached_is_absent(hf_cache, pocket_lib) -> None:
    _put(hf_cache, "kyutai/pocket-tts-without-voice-cloning", "languages/french/model.safetensors")
    _put(hf_cache, "kyutai/pocket-tts-without-voice-cloning", "languages/french/tokenizer.json")
    assert pocket_tts_engine.model_cached({"language": "fr", "voice": "estelle"}) is False


def test_pocket_precomputed_voice_embedding_counts(hf_cache, pocket_lib) -> None:
    _put(hf_cache, "kyutai/pocket-tts-without-voice-cloning", "languages/french/model.safetensors")
    _put(hf_cache, "kyutai/pocket-tts-without-voice-cloning", "languages/french/tokenizer.json")
    _put(hf_cache, "kyutai/pocket-tts-without-voice-cloning", "languages/french/embeddings/estelle.safetensors")
    assert pocket_tts_engine.model_cached({"language": "fr", "voice": "estelle"}) is True


def test_pocket_local_voice_file_and_unknown_name_need_no_network(hf_cache, pocket_lib, tmp_path) -> None:
    _fill_french(hf_cache)
    wav = tmp_path / "ma-voix.wav"
    wav.write_bytes(b"RIFF")
    assert pocket_tts_engine.model_cached({"language": "fr", "voice": str(wav)}) is True
    assert pocket_tts_engine.model_cached({"language": "fr", "voice": "inconnue"}) is True


def test_pocket_http_source_cannot_be_proven_cached(hf_cache, pocket_lib) -> None:
    (pocket_lib / "french.yaml").write_text(CONFIG + "  extra: https://exemple.test/x.bin\n")
    _fill_french(hf_cache)
    assert pocket_tts_engine.model_cached({"language": "fr"}) is False


def test_pocket_unknown_language_or_unreadable_config_is_unknown(hf_cache, pocket_lib) -> None:
    assert pocket_tts_engine.model_cached({"language": "xx"}) is None
    (pocket_lib / "french.yaml").write_text("a: [unclosed")
    assert pocket_tts_engine.model_cached({"language": "fr"}) is None


def test_pocket_without_library_is_unknown(monkeypatch) -> None:
    monkeypatch.setitem(__import__("sys").modules, "pocket_tts.utils.config", None)
    assert pocket_tts_engine.model_cached({"language": "fr"}) is None


def test_pocket_unreadable_cache_is_absent(pocket_lib, monkeypatch) -> None:
    import huggingface_hub

    def boom(*_a: Any, **_k: Any) -> None:
        raise PermissionError("cache illisible")

    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", boom)
    assert pocket_tts_engine.model_cached({"language": "fr"}) is False


# ── le moteur lui-même, sur le même faux cache : aucun appel réseau possible ──


class _Model:
    loads: ClassVar[list[str]] = []
    states: ClassVar[list[str]] = []
    sample_rate = 24000

    @classmethod
    def load_model(cls, language: str | None = None) -> _Model:
        cls.loads.append(str(language))
        return cls()

    def get_state_for_audio_prompt(self, voice: str) -> str:
        type(self).states.append(voice)
        return voice


@pytest.fixture
def engine_lib(pocket_lib, fake_module) -> type[_Model]:
    _Model.loads, _Model.states = [], []
    fake_module("pocket_tts", TTSModel=_Model)
    return _Model


def _slot(**options: Any) -> EngineSlot:
    return EngineSlot(get_spec("tts", "pocket-tts"), {"language": "fr", **options}, idle_unload_s=0)


def test_engine_refuses_other_language_cache_without_loading(hf_cache, engine_lib) -> None:
    _put(hf_cache, "kyutai/pocket-tts-en", "model.safetensors")
    with pytest.raises(EngineUnavailableError) as err:
        _slot().run(lambda e: None)
    assert "introuvable dans le cache" in err.value.reason and engine_lib.loads == []


def test_engine_loads_when_files_are_cached(hf_cache, engine_lib) -> None:
    _fill_french(hf_cache)
    _slot().run(lambda e: None)
    assert engine_lib.loads == ["french"] and engine_lib.states == ["estelle"]


def test_engine_refuses_an_uncached_later_voice(hf_cache, engine_lib) -> None:
    _fill_french(hf_cache)

    def use(engine: Any) -> None:
        engine._states.clear()
        engine._state("estelle")

    slot = _slot()
    slot.run(use)
    (hf_cache / "models--kyutai--tts-voices/snapshots" / REV / "unmute-prod-website/developpeuse-3.wav").unlink()
    with pytest.raises(EngineUnavailableError):
        slot.run(use)


def test_engine_unverifiable_cache_is_refused(hf_cache, engine_lib, monkeypatch) -> None:
    monkeypatch.setattr(pocket_tts_engine, "model_cached", lambda _o: None)
    with pytest.raises(EngineUnavailableError) as err:
        _slot().run(lambda e: None)
    assert "illisible" in err.value.reason and engine_lib.loads == []


def test_engine_with_consent_lets_load_errors_through(hf_cache, engine_lib, monkeypatch) -> None:
    monkeypatch.setattr(
        _Model, "load_model", classmethod(lambda cls, language=None: (_ for _ in ()).throw(OSError("réseau")))
    )
    with pytest.raises(OSError, match="réseau"):
        pocket_tts_engine.PocketTextToSpeech(language="fr", allow_download=True)


# ── faster-whisper ─────────────────────────────────────────────────────────────


def test_whisper_cached_snapshot_is_found(hf_cache) -> None:
    _put(hf_cache, "Systran/faster-whisper-small", "model.bin")
    assert faster_whisper_engine.model_cached({"model": "small"}) is True


def test_whisper_missing_snapshot_is_absent(hf_cache) -> None:
    assert faster_whisper_engine.model_cached({"model": "small"}) is False
    _put(hf_cache, "Systran/faster-whisper-large-v3", "model.bin")
    assert faster_whisper_engine.model_cached({"model": "small"}) is False


def test_whisper_local_dir_unknown_name_and_custom_repo(hf_cache, tmp_path) -> None:
    assert faster_whisper_engine.model_cached({"model": str(tmp_path)}) is True
    assert faster_whisper_engine.model_cached({"model": "mon-modele"}) is None
    assert faster_whisper_engine.model_cached({"model": "org/custom"}) is False
    _put(hf_cache, "org/custom", "model.bin")
    assert faster_whisper_engine.model_cached({"model": "org/custom"}) is True


def test_whisper_other_failure_is_unknown(monkeypatch) -> None:
    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "snapshot_download", lambda *_a, **_k: (_ for _ in ()).throw(MemoryError()))
    assert faster_whisper_engine.model_cached({"model": "small"}) is None


def test_whisper_with_consent_lets_load_errors_through(fake_module) -> None:
    class Boom:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            raise OSError("réseau")

    fake_module("faster_whisper", WhisperModel=Boom)
    with pytest.raises(OSError, match="réseau"):
        faster_whisper_engine.FasterWhisperSpeechToText(allow_download=True)


def test_pocket_sources_in_lists_are_required_and_no_weights_key_is_fine(hf_cache, pocket_lib) -> None:
    extra = f"hf://kyutai/pocket-tts-without-voice-cloning/languages/french/extra.bin@{REV}"
    (pocket_lib / "french.yaml").write_text(f"flow_lm:\n  files:\n    - {FR_TOKENIZER}\n    - {extra}\n")
    _put(hf_cache, "kyutai/pocket-tts-without-voice-cloning", "languages/french/tokenizer.json")
    assert pocket_tts_engine.model_cached({"language": "fr"}) is False
    _put(hf_cache, "kyutai/pocket-tts-without-voice-cloning", "languages/french/extra.bin")
    assert pocket_tts_engine.model_cached({"language": "fr"}) is True


def test_engine_close_releases_the_model(hf_cache, engine_lib) -> None:
    _fill_french(hf_cache)
    engine = pocket_tts_engine.PocketTextToSpeech(language="fr")
    engine.close()
    assert engine._model is None and engine._states == {}
