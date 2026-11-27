"""Synthèse par clonage de voix avec Qwen3-TTS 0.6B Base. Importé au chargement du moteur seulement.

Extra ``voice-qwen`` (``qwen-tts``). La voix de référence est livrée dans le paquet
(``grimoire/data/voice``) ; ``voice.tts.voice`` peut désigner un WAV local à la place.
Mesuré sur processeur : environ 6 s de calcul par seconde de parole, environ 6 Go de mémoire.
"""

from __future__ import annotations

import inspect
import io
import os
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grimoire.voice._offline import is_cache_miss
from grimoire.voice.engines import EngineUnavailableError, Speech, TextToSpeech, _one_line

MODEL_REPO = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
#: Fichiers du dépôt relevés dans un cache réel (2026-11) : tous requis pour charger sans réseau.
MODEL_FILES = (
    "config.json",
    "generation_config.json",
    "merges.txt",
    "model.safetensors",
    "preprocessor_config.json",
    "tokenizer_config.json",
    "vocab.json",
    "speech_tokenizer/config.json",
    "speech_tokenizer/model.safetensors",
    "speech_tokenizer/preprocessor_config.json",
)
#: Mémoire attendue, en Go, montrée par ``grimoire doctor``.
EXPECTED_MEMORY_GB = 6

#: Code de langue du projet → nom de langue de Qwen3-TTS.
_LANGUAGES = {
    "fr": "French",
    "en": "English",
    "de": "German",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "ru": "Russian",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
}

_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "voice"


def _qwen_language(language: str | None) -> str:
    code = (language or "fr").strip().lower().split("-")[0].split("_")[0]
    return _LANGUAGES.get(code, "Auto")


def _is_url(voice: str) -> bool:
    return "://" in voice


@dataclass(frozen=True, slots=True)
class Reference:
    """Une voix de référence : l'audio et ce qui y est dit."""

    audio: Path
    text: str


def resolve_reference(voice: str | None) -> Reference:
    """La référence livrée, ou le WAV local désigné par ``voice`` (avec son ``.txt`` voisin)."""
    if voice and _is_url(voice):
        raise EngineUnavailableError(
            f"la voix « {voice} » est une URL : rien n'est téléchargé — nommez un fichier WAV local "
            "(avec un .txt voisin portant sa transcription)"
        )
    audio = Path(voice).expanduser() if voice else _DATA_DIR / "reference-fr.wav"
    if not audio.is_file():
        raise EngineUnavailableError(f"voix de référence introuvable : {audio}")
    transcript = audio.with_suffix(".txt")
    try:
        text = transcript.read_text(encoding="utf-8").strip()
    except OSError:
        raise EngineUnavailableError(
            f"transcription de la voix de référence introuvable : {transcript} (le clonage en a besoin)"
        ) from None
    if not text:
        raise EngineUnavailableError(f"transcription de la voix de référence vide : {transcript}")
    return Reference(audio=audio, text=text)


def model_cached(options: dict[str, Any] | None = None) -> bool | None:
    """Les fichiers du modèle sont-ils tous dans le cache Hugging Face local ? Aucun réseau, rien chargé."""
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return None
    try:
        return all(isinstance(try_to_load_from_cache(MODEL_REPO, name), str) for name in MODEL_FILES)
    except Exception:
        return False  # pas de dossier de cache lisible : rien n'a été téléchargé


def _accepts_local_files_only(func: Any) -> bool:
    try:
        params = inspect.signature(func).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.name == "local_files_only" or p.kind is inspect.Parameter.VAR_KEYWORD for p in params)


def _threads(option: Any) -> int:
    if isinstance(option, int) and not isinstance(option, bool) and option >= 1:
        return option
    return max(1, (os.cpu_count() or 2) // 2)


class QwenCloneTextToSpeech(TextToSpeech):
    """Qwen3-TTS 0.6B Base sur CPU, clonant une voix de référence. Cache local sauf accord de téléchargement."""

    def __init__(
        self,
        *,
        language: str | None = None,
        voice: str | None = None,
        allow_download: bool = False,
        threads: int | None = None,
        **_options: Any,
    ) -> None:
        from qwen_tts import Qwen3TTSModel

        self._language = _qwen_language(language)
        self._voice = voice
        self._threads = _threads(threads)
        self._reference = resolve_reference(voice)  # refuse une URL ou un fichier absent avant tout chargement
        try:
            if not allow_download:
                cached = model_cached()
                if cached is False:
                    raise EngineUnavailableError(
                        f"modèle Qwen3-TTS « {MODEL_REPO} » introuvable dans le cache local, et rien n'est "
                        "téléchargé sans votre accord (voice.allow_download: true)"
                    )
            if allow_download:
                self._model = Qwen3TTSModel.from_pretrained(MODEL_REPO)
            else:
                # Chargement PAR CHEMIN : qwen-tts ne relaie local_files_only qu'au modèle, pas au processeur
                # (AutoProcessor), qui joindrait le hub avec le nom du dépôt. Un dossier local n'appelle rien.
                from huggingface_hub import snapshot_download

                path = snapshot_download(MODEL_REPO, local_files_only=True)  # LocalEntryNotFoundError si absent
                kwargs: dict[str, Any] = {}
                if _accepts_local_files_only(Qwen3TTSModel.from_pretrained):
                    kwargs["local_files_only"] = True
                self._model = Qwen3TTSModel.from_pretrained(path, **kwargs)
        except EngineUnavailableError:
            raise
        except Exception as exc:
            if not allow_download and is_cache_miss(exc):
                raise EngineUnavailableError(
                    f"modèle Qwen3-TTS « {MODEL_REPO} » introuvable dans le cache local, et rien n'est "
                    f"téléchargé sans votre accord (voice.allow_download: true) — {type(exc).__name__}"
                ) from None
            raise EngineUnavailableError(f"Qwen3-TTS n'a pas pu se charger ({_one_line(exc)})") from None

    def synthesize(self, text: str, *, voice: str | None = None) -> Speech:
        import numpy as np
        import torch  # déjà chargé par qwen-tts à ce stade

        reference = resolve_reference(voice) if voice else self._reference
        previous = torch.get_num_threads()
        torch.set_num_threads(self._threads)  # basse priorité : la machine reste utilisable pendant le calcul
        try:
            wavs, rate = self._model.generate_voice_clone(
                text=text,
                language=self._language,
                ref_audio=str(reference.audio),
                ref_text=reference.text,
            )
        finally:
            torch.set_num_threads(previous)
        wav = wavs[0]
        if hasattr(wav, "detach"):
            wav = wav.detach().cpu().numpy()
        samples = np.clip(np.asarray(wav, dtype="float32").reshape(-1), -1.0, 1.0)
        pcm = (samples * 32767).astype("int16").tobytes()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(int(rate))
            out.writeframes(pcm)
        return Speech(text=text, audio=buf.getvalue(), sample_rate=int(rate))

    def close(self) -> None:
        self._model = None
