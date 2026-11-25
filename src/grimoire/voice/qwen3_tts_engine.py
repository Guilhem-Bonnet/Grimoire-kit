"""Synthèse par Qwen3-TTS 0.6B Base en clonage d'une voix de référence (extra ``voice-qwen``).

Importé au chargement du moteur seulement. Le modèle parle avec la voix d'un WAV court et de sa
transcription : celle livrée dans le paquet (``assets/``, voix générée, aucune personne réelle), ou
un fichier local de l'utilisateur (``voice`` ; jamais une URL).

- Hors ligne par défaut : les fichiers exacts du modèle sont prouvés dans le cache Hugging Face
  avant tout chargement, puis le modèle est lu depuis ce dossier local (aucun appel réseau).
- Hors du fil appelant : chargement et synthèse tournent sur un fil dédié, en basse priorité
  (``setpriority`` du fil sous Linux), phrase par phrase.
"""

from __future__ import annotations

import io
import os
import re
import sys
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, TypeVar

from grimoire.voice._offline import is_cache_miss
from grimoire.voice.engines import EngineUnavailableError, Speech, TextToSpeech, _one_line

_T = TypeVar("_T")

MODEL_REPO = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
#: Les fichiers du dépôt sans lesquels le chargement irait au réseau (modèle, tokenizer, tokenizer de parole).
MODEL_FILES = (
    "config.json",
    "generation_config.json",
    "model.safetensors",
    "preprocessor_config.json",
    "tokenizer_config.json",
    "vocab.json",
    "merges.txt",
    "speech_tokenizer/config.json",
    "speech_tokenizer/configuration.json",
    "speech_tokenizer/preprocessor_config.json",
    "speech_tokenizer/model.safetensors",
)
#: Priorité du fil de synthèse (valeur de « nice » ; 19 = la plus basse).
NICE_LEVEL = 10
#: Silence entre deux phrases, en secondes.
_PAUSE_S = 0.25

_ASSETS = Path(__file__).parent / "assets"
BUNDLED_VOICE = _ASSETS / "ref-voix-fr.wav"
BUNDLED_TEXT = _ASSETS / "ref-voix-fr.txt"

_LANGUAGES = {
    "fr": "French",
    "en": "English",
    "de": "German",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "ru": "Russian",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
}
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+|\n+")


def _qwen_language(language: str | None) -> str:
    code = (language or "fr").strip().lower()
    return _LANGUAGES.get(code.split("-")[0].split("_")[0], code.capitalize())


def split_sentences(text: str) -> list[str]:
    """Le texte en phrases, sans vide ; une synthèse par phrase garde la mémoire et la latence bornées."""
    return [part.strip() for part in _SENTENCE_END.split(text) if part.strip()]


def lower_current_thread_priority() -> bool:
    """Baisse la priorité du fil appelant seulement (Linux : ``setpriority`` sur son identifiant). Vrai si fait.

    Ailleurs, un identifiant de fil n'est pas accepté de la même façon (macOS viserait tout le
    processus, donc le serveur) : on ne touche à rien plutôt que de ralentir le serveur.
    """
    if not sys.platform.startswith("linux") or not hasattr(os, "setpriority"):
        return False
    try:
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), NICE_LEVEL)
    except OSError:
        return False
    return True


def _is_url(voice: str) -> bool:
    return "://" in voice


class Qwen3CloneTextToSpeech(TextToSpeech):
    """Qwen3-TTS 0.6B Base sur CPU, voix clonée depuis un WAV de référence."""

    def __init__(
        self,
        *,
        language: str | None = None,
        voice: str | None = None,
        ref_text: str | None = None,
        allow_download: bool = False,
        **_options: Any,
    ) -> None:
        self._language = _qwen_language(language)
        self._allow_download = allow_download
        self._ref_text = ref_text
        self._default_voice = self._resolve_voice(voice, ref_text)
        self._pool: ThreadPoolExecutor | None = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="grimoire-qwen3-tts", initializer=lower_current_thread_priority
        )
        try:
            self._model = self._pool.submit(self._load).result()
        except BaseException:
            self.close()
            raise

    # ── chargement ────────────────────────────────────────────────────────────

    def _load(self) -> Any:
        from qwen_tts import Qwen3TTSModel

        source = MODEL_REPO
        try:
            if not self._allow_download:
                source = self._cached_snapshot()
            return Qwen3TTSModel.from_pretrained(source)
        except Exception as exc:
            if self._allow_download or isinstance(exc, EngineUnavailableError):
                raise
            if is_cache_miss(exc):
                raise EngineUnavailableError(
                    f"modèle Qwen3-TTS « {MODEL_REPO} » absent du cache, et rien n'est téléchargé sans votre "
                    f"accord (voice.allow_download: true) — {type(exc).__name__}"
                ) from None
            raise EngineUnavailableError(f"Qwen3-TTS n'a pas pu se charger ({_one_line(exc)})") from None

    def _cached_snapshot(self) -> str:
        """Le dossier local du modèle, après avoir prouvé que tous ses fichiers sont dans le cache."""
        cached = model_cached({})
        if cached is None:
            raise EngineUnavailableError(
                "cache Qwen3-TTS illisible ou huggingface_hub absent : rien n'est chargé sans votre accord "
                "de téléchargement (voice.allow_download: true)"
            )
        if not cached:
            raise EngineUnavailableError(
                f"modèle Qwen3-TTS « {MODEL_REPO} » absent du cache, et rien n'est téléchargé sans votre accord "
                "(voice.allow_download: true)"
            )
        from huggingface_hub import snapshot_download

        return str(snapshot_download(MODEL_REPO, local_files_only=True))

    # ── voix de référence ─────────────────────────────────────────────────────

    @staticmethod
    def _resolve_voice(voice: str | None, ref_text: str | None) -> tuple[str, str]:
        """(WAV, transcription) : la voix livrée sans ``voice``, sinon un fichier local et son texte."""
        if not voice:
            return str(BUNDLED_VOICE), BUNDLED_TEXT.read_text(encoding="utf-8").strip()
        if _is_url(voice):
            raise EngineUnavailableError(
                f"la voix « {voice} » est une URL : seule une voix de référence locale est acceptée (un fichier WAV)"
            )
        path = Path(voice).expanduser()
        if not path.is_file():
            raise EngineUnavailableError(f"voix de référence « {voice} » introuvable : indiquez un fichier WAV local")
        text = (ref_text or "").strip()
        if not text:
            sidecar = path.with_suffix(".txt")
            if sidecar.is_file():
                text = sidecar.read_text(encoding="utf-8").strip()
        if not text:
            raise EngineUnavailableError(
                f"la voix de référence « {voice} » a besoin de sa transcription : "
                f"voice.tts.ref_text, ou un fichier {path.with_suffix('.txt').name} à côté du WAV"
            )
        return str(path), text

    # ── synthèse ──────────────────────────────────────────────────────────────

    def synthesize(self, text: str, *, voice: str | None = None) -> Speech:
        ref_audio, ref_text = self._default_voice if voice is None else self._resolve_voice(voice, None)
        sentences = split_sentences(text)
        if not sentences or self._pool is None:
            return _wav_speech(text, [], 24000)
        return self._pool.submit(self._speak, text, sentences, ref_audio, ref_text).result()

    def _speak(self, text: str, sentences: list[str], ref_audio: str, ref_text: str) -> Speech:
        import numpy as np

        chunks: list[Any] = []
        rate = 24000
        for sentence in sentences:
            wavs, rate = self._model.generate_voice_clone(
                text=sentence, language=self._language, ref_audio=ref_audio, ref_text=ref_text
            )
            chunks.append(np.asarray(wavs[0], dtype=np.float32).reshape(-1))
            chunks.append(np.zeros(int(int(rate) * _PAUSE_S), dtype=np.float32))
        return _wav_speech(text, chunks[:-1], int(rate))

    def close(self) -> None:
        pool, self._pool = self._pool, None
        self._model = None
        if pool is not None:
            pool.shutdown(wait=False)


def _wav_speech(text: str, chunks: list[Any], rate: int) -> Speech:
    import numpy as np

    samples = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return Speech(text=text, audio=buf.getvalue(), sample_rate=rate)


def model_cached(options: dict[str, Any]) -> bool | None:
    """Les fichiers du modèle 0.6B Base sont-ils tous dans le cache Hugging Face local ? Aucun réseau, rien chargé.

    ``None`` : on ne sait pas (huggingface_hub absent).
    """
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return None
    try:
        return all(isinstance(try_to_load_from_cache(MODEL_REPO, name), str) for name in MODEL_FILES)
    except Exception:
        return False  # pas de dossier de cache lisible : rien n'a été téléchargé
