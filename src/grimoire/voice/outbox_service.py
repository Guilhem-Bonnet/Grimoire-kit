"""Le message vocal sortant côté serveur : une boîte d'envoi par projet, deux routes du cockpit (#1343 V3).

- :func:`outbox_state` (``GET voice/outbox``) : les messages parlés, leur texte, leur durée, écoutés ou non.
- :func:`outbox_play` (``POST voice/outbox/play``) : fige le message et rend son audio (WAV en base64), ou
  ``client_side`` quand le navigateur lit le texte avec sa propre voix. La synthèse se fait ici, à la lecture.
- :func:`record_reply` : appelé par le chat à la fin d'un tour, en mode vocal seulement.
"""

from __future__ import annotations

import base64
import threading
from pathlib import Path
from typing import Any

from grimoire.voice.engines import Speech, VoiceError
from grimoire.voice.outbox import Outbox, spoken_from_reply
from grimoire.voice.settings import VoiceSettings, load_voice_settings
from grimoire.voice.stt_service import VoiceUnavailableError, _engines_for

__all__ = ["outbox_play", "outbox_state", "record_reply", "reset_outboxes"]

_lock = threading.Lock()
_outboxes: dict[str, Outbox] = {}


def reset_outboxes() -> None:
    with _lock:
        _outboxes.clear()


def _outbox(root: Path) -> Outbox:
    key = str(root)
    with _lock:
        box = _outboxes.get(key)
        if box is None:
            box = _outboxes[key] = Outbox(lambda text: _synthesize(root, text))
        return box


def _synthesize(root: Path, text: str) -> Speech:
    settings = load_voice_settings(root)
    try:
        return _engines_for(root, settings).synthesize(text, voice=_voice(settings))
    except VoiceError as exc:
        raise ValueError(f"synthèse impossible : {exc.reason}") from None


def _voice(settings: VoiceSettings) -> str | None:
    value = settings.tts.options.get("voice")
    return value if isinstance(value, str) else None


def record_reply(root: Path, reply: str) -> dict[str, Any] | None:
    """Tire le message parlé de la réponse de l'orchestrateur et le dépose. ``None`` hors mode vocal ou sans rien à dire."""
    settings = load_voice_settings(root)
    if not settings.enabled:
        return None
    text, source = spoken_from_reply(reply, settings.spoken_max_s)
    if not text:
        return None
    message = _outbox(root).submit(text, source)
    return message.public() if message else None


def outbox_state(root: Path, _query: Any = None) -> dict[str, Any]:
    settings = load_voice_settings(root)
    state = _outbox(root).state() if settings.enabled else {"messages": []}
    return {"enabled": settings.enabled, "max_seconds": settings.spoken_max_s, **state}


def outbox_play(root: Path, body: dict[str, Any]) -> dict[str, Any]:
    settings = load_voice_settings(root)
    if not settings.enabled:
        raise VoiceUnavailableError("vocal indisponible : le mode vocal est éteint")
    message_id = body.get("id")
    if not isinstance(message_id, str) or not message_id:
        raise ValueError("id requis : le message parlé à lire")
    try:
        message, speech = _outbox(root).play(message_id)
    except KeyError:
        raise ValueError(f"message parlé inconnu : {message_id}") from None
    return {
        "ok": True,
        "message": message.public(),
        "client_side": speech.client_side,
        "text": message.text,
        "sample_rate": speech.sample_rate,
        "audio": base64.b64encode(speech.audio).decode("ascii") if speech.audio else None,
    }
