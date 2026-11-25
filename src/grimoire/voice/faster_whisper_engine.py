"""Reconnaissance par faster-whisper. Importé au chargement du moteur seulement (extra ``voice``)."""

from __future__ import annotations

import io
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from grimoire.voice._offline import is_cache_miss
from grimoire.voice.engines import EngineUnavailableError, SpeechToText, Transcript, _one_line

DEFAULT_MODEL = "small"


class FasterWhisperSpeechToText(SpeechToText):
    """Whisper en CTranslate2, sur CPU en int8 par défaut. Modèle pris dans le cache local sauf accord."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        device: str = "cpu",
        compute_type: str = "int8",
        allow_download: bool = False,
        language: str | None = None,
        **_options: Any,
    ) -> None:
        from faster_whisper import WhisperModel

        self.language = language
        try:
            self._model = WhisperModel(
                model,
                device=device,
                compute_type=compute_type,
                local_files_only=not allow_download,
            )
        except Exception as exc:
            if allow_download:
                raise
            if is_cache_miss(exc):
                raise EngineUnavailableError(
                    f"modèle Whisper « {model} » introuvable dans le cache local, et rien n'est téléchargé sans "
                    f"votre accord (voice.allow_download: true) — {type(exc).__name__}"
                ) from None
            raise EngineUnavailableError(f"Whisper « {model} » n'a pas pu se charger ({_one_line(exc)})") from None

    def transcribe(
        self,
        audio: bytes | Path,
        *,
        language: str | None = None,
        vocabulary: Sequence[str] = (),
    ) -> Transcript:
        source: Any = io.BytesIO(audio) if isinstance(audio, bytes) else str(audio)
        hotwords = " ".join(w for w in vocabulary if w.strip()) or None
        segments, info = self._model.transcribe(
            source,
            language=language or self.language,
            hotwords=hotwords,
            vad_filter=True,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return Transcript(
            text=text, language=getattr(info, "language", None), duration_s=getattr(info, "duration", None)
        )

    def close(self) -> None:
        self._model = None


_SYSTRAN_SIZES = frozenset(
    {
        "tiny",
        "tiny.en",
        "base",
        "base.en",
        "small",
        "small.en",
        "medium",
        "medium.en",
        "large-v1",
        "large-v2",
        "large-v3",
    }
)


def model_cached(options: dict[str, Any]) -> bool | None:
    """Le modèle choisi est-il dans le cache local ? Ne charge ni faster-whisper ni le modèle.

    ``None`` : on ne sait pas (nom de modèle hors des tailles connues, huggingface_hub absent).
    """
    model = str(options.get("model", DEFAULT_MODEL))
    if Path(model).is_dir():
        return True
    repo = model if "/" in model else (f"Systran/faster-whisper-{model}" if model in _SYSTRAN_SIZES else None)
    if repo is None:
        return None
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        return None
    try:
        snapshot_download(repo, local_files_only=True)
    except Exception as exc:
        return False if is_cache_miss(exc) else None
    return True
