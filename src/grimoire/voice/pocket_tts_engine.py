"""Synthèse par Pocket TTS (Kyutai). Importé au chargement du moteur seulement (extra ``voice``)."""

from __future__ import annotations

import io
import wave
from typing import Any

from grimoire.voice._offline import is_cache_miss
from grimoire.voice.engines import EngineUnavailableError, Speech, TextToSpeech, _one_line

#: Code de langue du projet → modèle de langue de pocket-tts.
_LANGUAGES = {
    "fr": "french",
    "en": "english",
    "de": "german",
    "es": "spanish",
    "it": "italian",
    "pt": "portuguese",
    "nl": "dutch",
}


def _pocket_language(language: str | None) -> str:
    code = (language or "fr").strip().lower()
    return _LANGUAGES.get(code.split("-")[0].split("_")[0], code)


class PocketTextToSpeech(TextToSpeech):
    """Pocket TTS sur CPU. Poids et voix pris dans le cache local sauf accord de téléchargement."""

    def __init__(
        self,
        *,
        language: str | None = None,
        voice: str | None = None,
        allow_download: bool = False,
        **_options: Any,
    ) -> None:
        from pocket_tts import TTSModel

        self._allow_download = allow_download
        self._language = _pocket_language(language)
        self._states: dict[str, Any] = {}
        try:
            if not allow_download and model_cached({}) is False:  # prouvé absent : rien ne part sur le réseau
                raise EngineUnavailableError(
                    f"modèle Pocket TTS « {self._language} » introuvable dans le cache local, et rien n'est "
                    "téléchargé sans votre accord (voice.allow_download: true)"
                )
            self._model = TTSModel.load_model(language=self._language)
            self._default_voice = voice or _default_voice(self._language)
            self._state(self._default_voice)
        except Exception as exc:
            if allow_download:
                raise
            if isinstance(exc, EngineUnavailableError):
                raise
            if is_cache_miss(exc):
                raise EngineUnavailableError(
                    f"modèle Pocket TTS « {self._language} » introuvable dans le cache local, et rien n'est "
                    f"téléchargé sans votre accord (voice.allow_download: true) — {type(exc).__name__}"
                ) from None
            raise EngineUnavailableError(
                f"Pocket TTS « {self._language} » n'a pas pu se charger ({_one_line(exc)})"
            ) from None

    def _state(self, voice: str) -> Any:
        if voice not in self._states:
            if not self._allow_download and _is_url(voice):
                raise EngineUnavailableError(
                    f"la voix « {voice} » est une URL : rien n'est téléchargé sans votre accord "
                    "(voice.allow_download: true) — nommez une voix du cache ou un fichier local"
                )
            self._states[voice] = self._model.get_state_for_audio_prompt(voice)
        return self._states[voice]

    def synthesize(self, text: str, *, voice: str | None = None) -> Speech:
        import torch  # déjà chargé par pocket-tts à ce stade

        audio = self._model.generate_audio(self._state(voice or self._default_voice), text)
        rate = int(self._model.sample_rate)
        pcm = (audio.reshape(-1).clamp(-1.0, 1.0) * 32767).to(dtype=torch.int16).cpu().numpy().tobytes()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes(pcm)
        return Speech(text=text, audio=buf.getvalue(), sample_rate=rate)

    def close(self) -> None:
        self._states.clear()
        self._model = None


def _is_url(voice: str) -> bool:
    return "://" in voice


def model_cached(options: dict[str, Any]) -> bool | None:
    """Un dépôt Pocket TTS est-il dans le cache Hugging Face ? Ne charge rien.

    Le dépôt exact dépend de la langue et de la version de pocket-tts : on cherche un
    dépôt dont le nom contient « pocket-tts », faute de mieux.
    """
    try:
        from huggingface_hub import scan_cache_dir
    except ImportError:
        return None
    try:
        repos = scan_cache_dir().repos
    except Exception:
        return False  # pas de dossier de cache : rien n'a jamais été téléchargé
    return any("pocket-tts" in repo.repo_id.lower() or "pocket_tts" in repo.repo_id.lower() for repo in repos)


def _default_voice(language: str) -> str:
    from pocket_tts.default_parameters import get_default_voice_for_language

    return str(get_default_voice_for_language(language))
