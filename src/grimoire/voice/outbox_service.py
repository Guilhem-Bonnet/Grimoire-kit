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
from grimoire.voice.outbox import QUESTION_MAX_S, Outbox, asks_a_question, spoken_from_reply, spoken_question
from grimoire.voice.settings import VoiceSettings, load_voice_settings
from grimoire.voice.stt_service import VoiceUnavailableError, _engines_for

__all__ = [
    "outbox_play",
    "outbox_state",
    "record_pilot_question",
    "record_reply",
    "reset_outboxes",
    "resolve_pilot_question",
    "settle_interview_questions",
]

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


def record_reply(root: Path, reply: str, token: str | None = None, interview: bool = False) -> dict[str, Any] | None:
    """Tire le message parlé de la réponse de l'orchestrateur et le dépose. ``None`` hors mode vocal ou sans rien à dire.

    ``token`` : le jeton du tour donné dans la consigne ; seule la balise qui le porte est lue.
    ``interview`` : un entretien de cadrage est en cours (lot V4). Le message parlé reste alors bref
    (:data:`QUESTION_MAX_S`) ; s'il pose une question, il est déposé comme **question** (message distinct, en
    tête de la boîte), jamais fusionné dans un compte rendu non écouté.
    """
    settings = load_voice_settings(root)
    if not settings.enabled:
        return None
    limit = min(settings.spoken_max_s, QUESTION_MAX_S) if interview else settings.spoken_max_s
    text, source = spoken_from_reply(reply, limit, token)
    if not text:
        return None
    if interview and source == "tag" and asks_a_question(text):
        asked = _outbox(root).submit_question(text, (), None, "interview")
        return asked.public() if asked else None
    message = _outbox(root).submit(text, source)
    return message.public() if message else None


def settle_interview_questions(root: Path) -> list[dict[str, Any]]:
    """La personne vient de parler à l'orchestrateur : ses questions d'entretien en attente sont répondues."""
    return [m.public() for m in _outbox(root).resolve_question(None)]


def record_pilot_question(root: Path, row: dict[str, Any]) -> dict[str, Any] | None:
    """Une question à options du chat devient un message vocal bref marqué question. ``None`` hors mode vocal.

    ``row`` : la ligne du pilote (``task_id``, ``question_id``, ``text``, ``options``). Déposée une fois.
    """
    settings = load_voice_settings(root)
    if not settings.enabled:
        return None
    options = tuple(str(o) for o in row.get("options") or ())
    limit = min(settings.spoken_max_s, QUESTION_MAX_S)
    text = spoken_question(str(row.get("text", "")), options, limit)
    ref = (str(row.get("task_id", "")), str(row.get("question_id", "")))
    message = _outbox(root).submit_question(text, options, ref)
    return message.public() if message else None


def resolve_pilot_question(root: Path, task_id: str, question_id: str) -> list[dict[str, Any]]:
    return [m.public() for m in _outbox(root).resolve_question((task_id, question_id))]


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
