"""Garde-fous du mode vocal (#1343, lot V5) : audio entrant contrôlé, permissions jamais accordées par la voix.

- :func:`check_wav` : l'enregistrement reçu est lu par son en-tête, pas seulement par son poids. Seul un WAV
  PCM entier (format 1), mono, 16 bits, à une fréquence de :data:`ALLOWED_SAMPLE_RATES` passe ; la durée
  tirée de l'en-tête ET celle tirée des octets réellement présents restent sous le plafond du projet, et un
  en-tête qui ment sur une taille est refusé.
- :func:`permission_spoken` : ce que le message vocal sortant dit d'une demande de permission de l'agent.
  Un gabarit fixe, rempli à partir des seuls champs structurés de la demande (nature de l'outil, nom du
  programme, extension du fichier) : jamais le titre, l'explication, la commande ni le nom de fichier rédigés
  par l'agent. Il dit toujours que la
  confirmation se fait à l'écran — la voix n'accorde rien (voir ``workspace_chat.Conversation``).

Aucune bibliothèque audio n'est importée ici.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

__all__ = [
    "ALLOWED_SAMPLE_RATES",
    "PERMISSION_SPOKEN_SOURCE",
    "SCREEN_FIRST",
    "SCREEN_ONLY",
    "VOICE_CANNOT_APPROVE",
    "VOICE_RESERVED_ANSWER",
    "WavInfo",
    "announce_permission",
    "check_wav",
    "marked_dictation",
    "permission_spoken",
    "voice_may_answer",
]

#: Fréquences acceptées : 16 kHz seulement, celle que la page enregistre et que les moteurs attendent.
#: À 16 kHz, rien au-dessus de 8 kHz (la fréquence de Nyquist) ne peut figurer dans le signal : le filtrage
#: passe-bas sous 8 kHz est donc sans objet. Une fréquence plus haute est refusée plutôt que filtrée ici.
ALLOWED_SAMPLE_RATES: tuple[int, ...] = (16_000,)
_PCM = 1
_CHANNELS = 1
_BITS = 16
_BLOCK_ALIGN = _CHANNELS * _BITS // 8
#: Au plus quelques blocs annexes (LIST, fact…) avant les données : au-delà, le fichier est refusé.
_MAX_CHUNKS = 8
#: En deçà, rien à transcrire : un enregistrement vide ou d'un souffle est refusé.
MIN_SECONDS = 0.1

#: La phrase qu'un refus vocal rend : la voix saisit du texte, elle n'accorde rien.
VOICE_CANNOT_APPROVE = "une permission attend : elle se confirme par un clic à l'écran, jamais par un message dicté"
SCREEN_FIRST = "La confirmation se fait à l'écran."
SCREEN_ONLY = "Pour l'accorder ou la refuser, cliquez à l'écran : la voix ne confirme rien."
#: Le refus d'une réponse dictée à une question réservée à l'humain.
VOICE_RESERVED_ANSWER = (
    "réponse dictée refusée : cette question est réservée à l'humain, répondez au clavier ou par un clic"
)
#: Source des messages parlés de permission dans la boîte d'envoi vocale.
PERMISSION_SPOKEN_SOURCE = "permission"

#: Les natures d'outil ACP (``toolCall.kind``) et leur lecture : une liste fermée, rien d'autre n'est dit.
_KIND_LABEL = {
    "read": "lire un fichier",
    "edit": "modifier un fichier",
    "delete": "supprimer un fichier",
    "move": "déplacer un fichier",
    "search": "chercher dans le projet",
    "execute": "lancer une commande",
    "fetch": "lire une page web",
    "think": "réfléchir",
    "switch_mode": "changer de mode",
}
_DEFAULT_LABEL = "utiliser un outil"
_TARGET_KEYS = ("file_path", "path", "notebook_path", "filePath")
#: Seul le nom du programme se dit, et seulement s'il a la forme d'un nom d'exécutable (pas de chemin, pas de phrase).
#: En minuscules seulement : une majuscule trahit une phrase déguisée (« Dites-oui ») ; sinon « un programme ».
_BINARY = re.compile(r"[a-z0-9][a-z0-9_.+-]{0,23}")
#: D'un fichier, seule l'extension se dit, courte et en minuscules ; sinon « un fichier ».
_EXTENSION = re.compile(r"\.[a-z0-9]{1,5}")
#: Les enveloppes connues : on dit « par l'intermédiaire de sudo », jamais le mot suivant (choisi par l'agent).
#: ``bash``/``sh`` ne sont une enveloppe que suivis de ``-c`` ; seuls, ils sont le programme.
_WRAPPERS = frozenset({"sudo", "env", "nohup", "time", "xargs"})
_SHELLS = frozenset({"bash", "sh"})
#: Les natures de question qu'une dictée ne tranche jamais.
_VOICE_FORBIDDEN_KINDS = frozenset({"permission", "merge"})


@dataclass(frozen=True, slots=True)
class WavInfo:
    sample_rate: int
    data_bytes: int

    @property
    def duration_s(self) -> float:
        return self.data_bytes / (self.sample_rate * _BLOCK_ALIGN)


def _refuse(why: str) -> ValueError:
    return ValueError(f"audio refusé : {why}")


def check_wav(data: bytes, max_seconds: float) -> WavInfo:
    """Valide un enregistrement WAV et le rend décrit ; ``ValueError`` (lisible) sinon.

    Refusés : autre chose qu'un RIFF/WAVE, un format compressé ou flottant dans un conteneur WAV (y compris
    l'extension ``WAVE_FORMAT_EXTENSIBLE``), la stéréo, 8 ou 24 bits, une fréquence hors
    :data:`ALLOWED_SAMPLE_RATES`, une taille RIFF ou ``data`` qui ne correspond pas aux octets reçus, et toute
    durée (déclarée ou réelle) au-delà de ``max_seconds``.
    """
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise _refuse("un WAV (RIFF/WAVE) est attendu")
    (riff_size,) = struct.unpack_from("<I", data, 4)
    if riff_size != len(data) - 8:
        raise _refuse("la taille RIFF de l'en-tête ne correspond pas aux octets reçus")
    offset = 12
    fmt: tuple[int, int, int, int, int, int] | None = None
    for _ in range(_MAX_CHUNKS):
        if offset + 8 > len(data):
            break
        chunk_id = data[offset : offset + 4]
        (size,) = struct.unpack_from("<I", data, offset + 4)
        body = offset + 8
        if chunk_id == b"fmt ":
            if size < 16 or body + size > len(data):
                raise _refuse("bloc de format illisible")
            fmt = struct.unpack_from("<HHIIHH", data, body)
        elif chunk_id == b"data":
            if fmt is None:
                raise _refuse("bloc de données avant le bloc de format")
            return _check_data(fmt, size, len(data) - body, max_seconds)
        if body + size > len(data):
            raise _refuse("un bloc déclare plus d'octets que reçus")
        offset = body + size + (size & 1)
    raise _refuse("bloc de données absent")


def _check_data(fmt: tuple[int, int, int, int, int, int], declared: int, actual: int, max_seconds: float) -> WavInfo:
    tag, channels, rate, byte_rate, align, bits = fmt
    if tag != _PCM:
        raise _refuse(f"format {tag:#06x} : seul le PCM entier (format 1) est accepté, pas un audio compressé")
    if channels != _CHANNELS:
        raise _refuse(f"{channels} canaux : mono attendu")
    if bits != _BITS:
        raise _refuse(f"{bits} bits par échantillon : 16 bits attendus")
    if rate not in ALLOWED_SAMPLE_RATES:
        allowed = ", ".join(f"{r} Hz" for r in ALLOWED_SAMPLE_RATES)
        raise _refuse(f"fréquence {rate} Hz : {allowed} attendu")
    if align != _BLOCK_ALIGN or byte_rate != rate * _BLOCK_ALIGN:
        raise _refuse("en-tête incohérent (débit ou alignement)")
    # Égalité stricte : un bloc placé APRÈS ``data`` (LIST, id3…) est refusé lui aussi — la page n'en écrit pas.
    if declared != actual:
        raise _refuse("la taille des données de l'en-tête ne correspond pas aux octets reçus")
    if actual % _BLOCK_ALIGN:
        raise _refuse("données tronquées au milieu d'un échantillon")
    info = WavInfo(sample_rate=rate, data_bytes=actual)
    if info.duration_s < MIN_SECONDS:
        raise _refuse(f"enregistrement trop court ({info.duration_s:.2f} s, minimum {MIN_SECONDS:g} s)")
    if info.duration_s > max_seconds:
        raise ValueError(f"audio trop long : {info.duration_s:.1f} s, plafond {max_seconds:g} s")
    return info


# ── Permission lue à la voix ──────────────────────────────────────────────────


def _file_label(call: dict[str, Any]) -> str:
    """« un fichier .py » d'après l'extension du fichier visé, sinon « un fichier » ; jamais son nom (choisi par l'agent)."""
    raw = call.get("rawInput")
    candidates: list[Any] = []
    locations = call.get("locations")
    if isinstance(locations, list):
        candidates += [loc.get("path") for loc in locations[:1] if isinstance(loc, dict)]
    if isinstance(raw, dict):
        candidates += [raw.get(key) for key in _TARGET_KEYS]
    for value in candidates:
        if isinstance(value, str) and value.strip():
            name = PureWindowsPath(value).name if "\\" in value else PurePosixPath(value).name
            suffix = PurePosixPath(name).suffix
            return f"un fichier {suffix}" if _EXTENSION.fullmatch(suffix) else "un fichier"
    return ""


def _program(call: dict[str, Any]) -> str:
    """Ce qui se dit du programme lancé : « programme git », « un programme lancé par l'intermédiaire de sudo »,
    « un programme » (nom à une autre forme qu'un exécutable en minuscules), ou rien sans commande."""
    raw = call.get("rawInput")
    value = raw.get("command") if isinstance(raw, dict) else None
    words: list[str] = []
    if isinstance(value, list):
        words = [str(w) for w in value[:2]]
    elif isinstance(value, str):
        words = value.split(maxsplit=2)[:2]
    if not words:
        return ""
    first = words[0]
    if first in _WRAPPERS or (first in _SHELLS and words[1:] == ["-c"]):
        return f"un programme lancé par l'intermédiaire de {first}"
    return f"programme {first}" if _BINARY.fullmatch(first) else "un programme"


def permission_spoken(tool_call: Any) -> str:
    """Le message parlé d'une demande de permission : gabarit fixe, sans texte libre de l'agent.

    Lit ``kind`` (liste fermée), le nom du programme lancé (premier mot de ``rawInput.command``, s'il a la forme
    d'un nom d'exécutable en minuscules ; une enveloppe connue est nommée comme telle) et l'extension du fichier visé ; ignore le reste de la commande, le nom du fichier,
    ``title``, ``content`` et tout autre champ rédigé par l'agent. La consigne de l'écran ouvre et clôt le message ;
    le détail complet est à l'écran.
    """
    call = tool_call if isinstance(tool_call, dict) else {}
    kind = call.get("kind")
    label = _KIND_LABEL.get(kind, _DEFAULT_LABEL) if isinstance(kind, str) else _DEFAULT_LABEL
    parts = [f"{SCREEN_FIRST} Permission demandée : {label}"]
    program = _program(call)
    if program:
        parts.append(f", {program}")
    target = _file_label(call)
    if target:
        parts.append(f", sur {target}")
    return "".join(parts) + ". " + SCREEN_ONLY


def marked_dictation(body: dict[str, Any]) -> bool:
    """Le corps d'une requête du chat vient-il d'une dictée ? Dans le doute, oui (et la voix est refusée là où elle
    n'a pas sa place) : tout ``dictation`` présent autre que ``False``/``None`` (``True``, ``"true"``, ``1``, même
    ``"false"``), ou ``source == "voice"``. Seule fonction lue par ``chat/send`` et ``chat/permission``."""
    value = body.get("dictation")
    return (value is not None and value is not False) or body.get("source") == "voice"


def voice_may_answer(row: dict[str, Any]) -> bool:
    """Une question du chat peut-elle être tranchée par une réponse dictée ? Non si sa nature engage
    (``permission``, ``merge``) ou si elle relève d'une catégorie réservée (``reserved`` de ``pending_questions``).
    Sans ``reserved`` (ligne d'une autre provenance), on ferme : la question est jugée réservée."""
    return row.get("kind") not in _VOICE_FORBIDDEN_KINDS and row.get("reserved", True) is False


def announce_permission(root: Path, tool_call: Any) -> dict[str, Any] | None:
    """En mode vocal, dépose dans la boîte d'envoi le message parlé d'une permission ; ``None`` hors mode vocal."""
    from grimoire.voice import outbox_service
    from grimoire.voice.settings import load_voice_settings

    if not load_voice_settings(root).enabled:
        return None
    message = outbox_service._outbox(root).announce(permission_spoken(tool_call), PERMISSION_SPOKEN_SOURCE)
    return message.public() if message else None
