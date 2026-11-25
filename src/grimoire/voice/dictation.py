"""Reconnaître une dictée côté serveur : le jeton de transcription (#1378).

Le marqueur ``dictation`` que la page envoie ne suffit pas : une page modifiée, un autre client ou un oubli le
laisseraient partir sans lui. Le serveur garde donc, en mémoire seulement, ce qu'il vient de transcrire.

- :func:`issue` : ``voice/transcribe`` y dépose le texte transcrit et rend un jeton opaque
  (``secrets.token_urlsafe``), lié à la portée (projet résolu + session du cockpit : le processus et son jeton de
  session), à l'empreinte du texte normalisé, pour :data:`TTL_S` secondes ; au plus :data:`MAX_PER_SCOPE` jetons
  vivants par portée (les plus anciens de cette portée sont oubliés, un projet ne chasse pas les transcriptions d'un
  autre) et :data:`MAX_TOTAL` au total.
- :func:`recognize` : lu par ``chat/send`` et ``chat/permission`` à la place du seul marqueur. Le texte est une
  dictée si (1) le marqueur est présent (:func:`grimoire.voice.safety.marked_dictation`), ou (2) un jeton vivant de
  la portée est renvoyé, ou (3) il n'y a ni marqueur ni jeton mais le texte, une fois normalisé, **égale** une
  transcription vivante de la portée, ou en **contient** une d'au moins :data:`MIN_INCLUDED` caractères (``oui``,
  ``ok`` ne sont donc reconnus que par égalité exacte). Un texte de plus de :data:`MAX_SEARCHED` caractères
  normalisés n'est pas fouillé : dès qu'une transcription vivante existe dans la portée, il est traité comme dicté
  (échec fermé).
- :func:`consume` : appelé par l'appelant APRÈS un envoi accepté seulement. Une transcription (ou un jeton)
  reconnue lors d'un envoi refusé reste vivante jusqu'à son expiration : le même texte renvoyé sans marqueur est
  refusé de nouveau. Un envoi accepté la consomme : le même texte retapé ensuite est une saisie au clavier.

La normalisation : caractères de catégorie Unicode ``Cf`` (espace sans chasse, marques bidi…) retirés, NFKC,
minuscules, mots (lettres et chiffres) séparés par une espace ; la ponctuation et la casse ne comptent pas. Seule
la transcription retenue est tronquée (:data:`MAX_TEXT`), jamais le texte envoyé.

Limites assumées : une dictée entièrement réécrite au clavier n'est plus une dictée ; les homoglyphes (un « o »
cyrillique) ne sont pas ramenés à leur lettre latine ; un client qui n'appelle jamais ``voice/transcribe`` (et
n'envoie ni marqueur ni jeton) est un clavier, pas visé ; après :data:`TTL_S` secondes, ou dans une autre session
du cockpit ou un autre projet, la transcription est oubliée et le texte est traité comme une saisie au clavier ;
après un refus, retaper le même texte reste refusé jusqu'à l'expiration (attendre, cliquer, ou le formuler
autrement). Ni texte transcrit ni jeton ne sont écrits (journal, fichier, ledger) ; l'expiration est appliquée par
toute lecture du module et à l'ouverture du service vocal (:func:`expire`).
"""

from __future__ import annotations

import hashlib
import re
import secrets
import threading
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

from grimoire.voice.safety import marked_dictation

__all__ = [
    "MAX_PER_SCOPE",
    "MAX_SEARCHED",
    "MAX_TEXT",
    "MAX_TOKENS_PER_REQUEST",
    "MAX_TOTAL",
    "MIN_INCLUDED",
    "TTL_S",
    "Recognized",
    "consume",
    "expire",
    "issue",
    "recognize",
    "reset",
    "sent_as_dictation",
]

#: Durée de vie d'une transcription, en secondes : le temps de relire, corriger et envoyer.
TTL_S = 300.0
#: Jetons vivants au plus par portée (projet + session) : mémoire bornée sans qu'un projet chasse un autre.
MAX_PER_SCOPE = 16
#: Garde-fou global, toutes portées confondues.
MAX_TOTAL = 256
#: Longueur normalisée minimale pour qu'une transcription incluse dans un texte plus long le fasse reconnaître.
MIN_INCLUDED = 12
#: Jetons lus au plus dans une requête.
MAX_TOKENS_PER_REQUEST = 8
#: Longueur normalisée retenue d'une transcription (le reste est ignoré).
MAX_TEXT = 4000
#: Au-delà, le texte envoyé n'est pas fouillé : traité comme dicté s'il existe une transcription vivante.
MAX_SEARCHED = 200_000
_WORD = re.compile(r"[^\W_]+")


@dataclass(frozen=True, slots=True)
class _Entry:
    scope: str
    fingerprint: str
    text: str
    expires: float


class Recognized(NamedTuple):
    """``dictated`` : le corps vient d'une dictée ; ``keys`` : les jetons internes reconnus, à consommer par
    :func:`consume` une fois l'envoi accepté (vide si seul le marqueur l'a dit)."""

    dictated: bool
    keys: tuple[str, ...]


_lock = threading.Lock()
_entries: OrderedDict[str, _Entry] = OrderedDict()
_clock = time.monotonic


def _normalize(text: str, limit: int | None = None) -> str:
    plain = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    words = " ".join(_WORD.findall(unicodedata.normalize("NFKC", plain).casefold()))
    return words if limit is None else words[:limit]


def _scope(root: Path) -> str:
    from grimoire.tools.request_guard import session_token

    session = hashlib.sha256(session_token().encode("utf-8")).hexdigest()[:16]
    try:
        resolved = root.resolve()
    except OSError:
        resolved = root
    return f"{resolved}|{session}"


def _expire_locked() -> None:
    now = _clock()
    for key in [k for k, e in _entries.items() if e.expires <= now]:
        del _entries[key]


def expire() -> None:
    """Oublie les transcriptions échues (appelé à l'ouverture du service vocal et par toute lecture du module)."""
    with _lock:
        _expire_locked()


def reset() -> None:
    """Oublie toutes les transcriptions (tests, arrêt)."""
    with _lock:
        _entries.clear()


def issue(root: Path, text: str) -> str:
    """Retient ``text`` (transcrit pour ``root``) et rend son jeton ; ``""`` s'il ne reste aucun mot."""
    norm = _normalize(text, MAX_TEXT)
    if not norm:
        return ""
    token = secrets.token_urlsafe(24)
    scope = _scope(root)
    entry = _Entry(scope, hashlib.sha256(norm.encode("utf-8")).hexdigest(), norm, _clock() + TTL_S)
    with _lock:
        _expire_locked()
        _entries[token] = entry
        mine = [k for k, e in _entries.items() if e.scope == scope]
        for key in mine[: max(0, len(mine) - MAX_PER_SCOPE)]:
            del _entries[key]
        while len(_entries) > MAX_TOTAL:
            _entries.popitem(last=False)
    return token


def _tokens(body: dict[str, Any]) -> list[str]:
    raw = body.get("dictation_tokens")
    if not isinstance(raw, list):
        return []
    return [t for t in raw[:MAX_TOKENS_PER_REQUEST] if isinstance(t, str) and t]


def recognize(root: Path, body: dict[str, Any]) -> Recognized:
    """Le corps d'une requête du chat vient-il d'une dictée ? Voir le module. Ne consomme rien."""
    marked = marked_dictation(body)
    scope = _scope(root)
    text = body.get("text")
    sent = _normalize(text) if isinstance(text, str) else ""
    keys: list[str] = []
    with _lock:
        _expire_locked()
        mine = {k: e for k, e in _entries.items() if e.scope == scope}
        keys.extend(token for token in _tokens(body) if token in mine)
        if len(sent) > MAX_SEARCHED:  # trop long pour être fouillé : échec fermé
            keys.extend(mine)
        elif sent:
            sent_print = hashlib.sha256(sent[:MAX_TEXT].encode("utf-8")).hexdigest()
            for key, entry in mine.items():
                if entry.fingerprint == sent_print or (len(entry.text) >= MIN_INCLUDED and entry.text in sent):
                    keys.append(key)
    return Recognized(marked or bool(keys), tuple(dict.fromkeys(keys)))


def consume(keys: tuple[str, ...]) -> None:
    """Consomme les transcriptions reconnues, une fois l'envoi accepté."""
    with _lock:
        for key in keys:
            _entries.pop(key, None)


def sent_as_dictation(root: Path, body: dict[str, Any]) -> bool:
    """Raccourci de :func:`recognize` : vrai si le corps vient d'une dictée. Ne consomme rien."""
    return recognize(root, body).dictated
