"""Synthèse par Pocket TTS (Kyutai). Importé au chargement du moteur seulement (extra ``voice``)."""

from __future__ import annotations

import io
import wave
from pathlib import Path
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
            if not allow_download:
                self._require_cached(voice)
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

    def _require_cached(self, voice: str | None) -> None:
        """Refuse de charger tant que le modèle de la langue et la voix ne sont pas prouvés dans le cache."""
        wanted = voice or _default_voice(self._language)
        if _is_url(wanted):
            return  # refusée avec son propre message par _state, après le chargement du modèle
        cached = model_cached({"language": self._language, "voice": wanted})
        if cached is None:
            raise EngineUnavailableError(
                f"cache Pocket TTS « {self._language} » invalide ou illisible : rien n'est chargé sans votre "
                "accord de téléchargement (voice.allow_download: true)"
            )
        if cached is False:
            raise EngineUnavailableError(
                f"modèle Pocket TTS « {self._language} » ou voix « {wanted} » introuvable dans le cache local, "
                "et rien n'est téléchargé sans votre accord (voice.allow_download: true)"
            )

    def _state(self, voice: str) -> Any:
        if voice not in self._states:
            if not self._allow_download:
                if _is_url(voice):
                    raise EngineUnavailableError(
                        f"la voix « {voice} » est une URL : rien n'est téléchargé sans votre accord "
                        "(voice.allow_download: true) — nommez une voix du cache ou un fichier local"
                    )
                self._require_cached(voice)
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


def _hf_cached(source: str) -> bool:
    """Un fichier ``hf://dépôt/chemin[@révision]`` est-il déjà dans le cache local ? Aucun réseau."""
    from huggingface_hub import try_to_load_from_cache

    rest = source.removeprefix("hf://")
    repo_id = "/".join(rest.split("/")[:2])
    filename, _, revision = "/".join(rest.split("/")[2:]).partition("@")
    found = try_to_load_from_cache(repo_id, filename, revision=revision or None)
    return isinstance(found, str)


def _sources(node: Any, *, top: bool = True) -> tuple[list[str], list[str]]:
    """(sources toutes requises, sources dont une seule suffit) d'une configuration de modèle."""
    required: list[str] = []
    either: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if top and key in ("weights_path", "weights_path_without_voice_cloning"):
                if isinstance(value, str):
                    either.append(value)  # pocket-tts retombe sur l'autre paquet de poids
                continue
            sub_required, sub_either = _sources(value, top=False)
            required += sub_required
            either += sub_either
    elif isinstance(node, list):
        for item in node:
            sub_required, sub_either = _sources(item, top=False)
            required += sub_required
            either += sub_either
    elif isinstance(node, str) and node.startswith(("hf://", "http://", "https://")):
        required.append(node)
    return required, either


def model_cached(options: dict[str, Any]) -> bool | None:
    """Les fichiers exacts du modèle de la langue et de la voix sont-ils dans le cache local ? Ne charge rien.

    Les fichiers viennent de la configuration livrée avec pocket-tts pour la langue demandée : chacun est
    cherché par ``try_to_load_from_cache`` (aucun réseau). Une source ``http(s)`` n'a pas de cache vérifiable :
    elle compte comme absente. ``None`` : on ne sait pas (bibliothèque ou configuration introuvable).
    """
    try:
        import yaml  # type: ignore[import-untyped]  # dépendance de pocket-tts, sans stubs ici
        from huggingface_hub import try_to_load_from_cache  # noqa: F401
        from pocket_tts.utils.config import CONFIGS_DIR
    except ImportError:
        return None
    language = _pocket_language(options.get("language"))
    config = Path(CONFIGS_DIR) / f"{language}.yaml"
    try:
        required, either = _sources(yaml.safe_load(config.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError):
        return None
    try:
        if not all(s.startswith("hf://") and _hf_cached(s) for s in required):
            return False
        if either and not any(s.startswith("hf://") and _hf_cached(s) for s in either):
            return False
        return _voice_cached(language, options.get("voice"))
    except Exception:
        return False  # pas de dossier de cache lisible : rien n'a été téléchargé


def _voice_cached(language: str, voice: Any) -> bool:
    """Une voix nommée de pocket-tts est-elle dans le cache ? Un fichier local ou un nom inconnu ne télécharge rien."""
    name = str(voice or "")
    if not name or Path(name).expanduser().is_file():
        return True
    from pocket_tts.utils.utils import _ORIGINS_OF_PREDEFINED_VOICES, get_predefined_voice

    origin = _ORIGINS_OF_PREDEFINED_VOICES.get(name)
    if origin is None:
        return True  # ni voix livrée ni URL : la bibliothèque la lira comme un chemin, sans réseau
    return _hf_cached(origin) or _hf_cached(get_predefined_voice(language, name))


def _default_voice(language: str) -> str:
    from pocket_tts.default_parameters import get_default_voice_for_language

    return str(get_default_voice_for_language(language))
