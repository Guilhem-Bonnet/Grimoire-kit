"""Le message vocal entrant côté serveur : état du vocal, puis transcription locale d'un enregistrement.

Deux entrées pour les routes du cockpit (``voice/config`` en lecture, ``voice/transcribe`` en écriture) :

- :func:`voice_config` dit si le bouton d'enregistrement doit exister. Sans l'extra ``voice``, avec le
  mode ``text`` ou avec un moteur client (navigateur), la réponse est ``available: false`` et sa raison.
- :func:`transcribe` décode l'audio (WAV en base64, JSON comme toute écriture du cockpit), contrôle son
  en-tête et sa durée (:func:`grimoire.voice.safety.check_wav`), le confie au moteur choisi (lot V1) avec une
  amorce tirée du projet, puis supprime le fichier temporaire — y compris en cas d'échec. Le texte rendu est
  celui du moteur, tel quel : aucun LLM ne le réécrit. Seules des métadonnées (texte, langue, durée) en sortent.

Les moteurs sont ceux du pool du processus (:mod:`grimoire.voice.engine_pool`) : un seul modèle de
reconnaissance chargé pour tous les projets enrôlés.

Aucune bibliothèque audio n'est importée ici : le moteur ne se charge qu'au premier appel.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from grimoire.voice.engine_pool import PooledEngines, pool
from grimoire.voice.engines import VoiceError, availability, get_spec
from grimoire.voice.safety import ALLOWED_SAMPLE_RATES, check_wav
from grimoire.voice.settings import MAX_RECORD_CEILING_S, VoiceSettings, load_voice_settings

__all__ = [
    "MAX_BODY_BYTES",
    "SAMPLE_RATE",
    "VoiceUnavailableError",
    "ceiling_body_bytes",
    "declared_length",
    "discard_body",
    "project_vocabulary",
    "reset_engines",
    "transcribe",
    "voice_config",
]

SAMPLE_RATE = ALLOWED_SAMPLE_RATES[0]
_WAV_BYTES_PER_SECOND = SAMPLE_RATE * 2  # mono, 16 bits
_WAV_SLACK = 4096  # l'en-tête WAV et ses blocs annexes
#: Plafond absolu du corps d'une requête de transcription (base64 compris), quel que soit le réglage.
#: Il doit laisser passer la durée maximale réglable (voir :func:`ceiling_body_bytes`), sinon une durée
#: autorisée serait refusée par le serveur HTTP avant d'être lue.
MAX_BODY_BYTES = 16 * 1024 * 1024
_VOCAB_MAX = 24


class VoiceUnavailableError(FileNotFoundError):
    """Le vocal n'est pas disponible ici : la raison tient en une phrase, la route répond 404."""


def declared_length(headers: Any) -> int:
    """``Content-Length`` déclaré ; 0 s'il est absent ou illisible (la lecture du corps le refusera)."""
    try:
        return int(headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        return 0


def discard_body(rfile: Any, declared: int, *, limit: int = 64 * 1024 * 1024) -> None:
    """Lit sans le garder le corps refusé, pour que le client reçoive le refus plutôt qu'un RST (#857)."""
    left = min(declared, limit)
    with contextlib.suppress(OSError, ValueError):
        while left > 0 and (chunk := rfile.read(min(left, 1 << 20))):
            left -= len(chunk)


def reset_engines() -> None:
    """Décharge et oublie les moteurs du processus (tests, arrêt)."""
    pool.reset()


def _unavailable_reason(settings: VoiceSettings) -> str | None:
    if not settings.enabled:
        return "le mode vocal est éteint (interaction.mode: voice dans project-context.yaml pour l'allumer)"
    try:
        spec = get_spec("stt", settings.stt.engine)
    except VoiceError as exc:
        return exc.reason
    if not spec.modules:  # moteur client : le navigateur transcrit, le serveur ne reçoit rien à transcrire
        return f"le moteur « {spec.name} » transcrit dans le navigateur : pas de transcription locale"
    avail = availability(spec)
    return None if avail.ok else avail.reason


def voice_config(root: Path, _query: Any = None) -> dict[str, Any]:
    """Ce que la page doit savoir pour montrer (ou non) le bouton d'enregistrement."""
    settings = load_voice_settings(root)
    reason = _unavailable_reason(settings)
    return {
        "available": reason is None,
        "reason": reason or "",
        "mode": settings.mode,
        "max_seconds": settings.max_record_s,
        "sample_rate": SAMPLE_RATE,
        "problems": list(settings.problems),
    }


def max_audio_bytes(settings: VoiceSettings) -> int:
    return int(settings.max_record_s * _WAV_BYTES_PER_SECOND) + _WAV_SLACK


def ceiling_body_bytes(max_record_s: float = MAX_RECORD_CEILING_S) -> int:
    """Taille du corps JSON d'un enregistrement de ``max_record_s`` secondes, base64 compris (marge de 1 Kio)."""
    raw = int(max_record_s * _WAV_BYTES_PER_SECOND) + _WAV_SLACK
    return -(-raw // 3) * 4 + 1024


def project_vocabulary(root: Path) -> list[str]:
    """L'amorce du moteur : nom du projet, branche, fichiers récemment touchés. Au mieux, jamais bloquant."""
    words: list[str] = [root.name]
    for args in (["symbolic-ref", "--short", "-q", "HEAD"], ["log", "-n", "15", "--name-only", "--pretty=format:"]):
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            out = subprocess.run(
                ["git", "-C", str(root), *args],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            ).stdout
            words.extend(line.strip() for line in out.splitlines() if line.strip())
    seen: dict[str, None] = {}
    for word in words:
        for form in (word, Path(word).name):
            if form and form not in seen:
                seen[form] = None
    return list(seen)[:_VOCAB_MAX]


def _engines_for(_root: Path, settings: VoiceSettings) -> PooledEngines:
    """Les moteurs d'un projet : ceux du pool du processus, partagés par clé (moteur, options)."""
    return PooledEngines(settings)


def _decode(body: dict[str, Any], limit: int) -> bytes:
    raw = body.get("audio")
    if not isinstance(raw, str) or not raw:
        raise ValueError("audio requis : WAV 16 kHz mono encodé en base64")
    if len(raw) > limit * 4 // 3 + 8:
        raise ValueError(f"audio trop long : plafond {limit // _WAV_BYTES_PER_SECOND} s")
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("audio illisible : base64 invalide") from None
    if len(data) > limit:
        raise ValueError(f"audio trop long : plafond {limit // _WAV_BYTES_PER_SECOND} s")
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("audio illisible : un WAV est attendu")
    return data


def transcribe(root: Path, body: dict[str, Any]) -> dict[str, Any]:
    """Transcrit un enregistrement. ``VoiceUnavailableError`` (404) sans vocal, ``ValueError`` (400) si l'audio est refusé."""
    settings = load_voice_settings(root)
    reason = _unavailable_reason(settings)
    if reason is not None:
        raise VoiceUnavailableError(f"vocal indisponible : {reason}")
    data = _decode(body, max_audio_bytes(settings))
    check_wav(data, settings.max_record_s)
    engines = _engines_for(root, settings)
    handle = tempfile.NamedTemporaryFile(prefix="grimoire-stt-", suffix=".wav", delete=False)  # noqa: SIM115
    path = Path(handle.name)
    try:
        with handle:
            handle.write(data)
        del data
        try:
            transcript = engines.transcribe(path, vocabulary=project_vocabulary(root))
        except VoiceError as exc:
            raise ValueError(f"transcription impossible : {exc.reason}") from None
    finally:
        path.unlink(missing_ok=True)
    return {
        "ok": True,
        "text": transcript.text,
        "language": transcript.language,
        "duration_s": transcript.duration_s,
    }
