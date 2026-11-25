"""Reconnaître une dictée côté serveur : le jeton de transcription (#1378).

Le marqueur ``dictation`` que la page envoie ne suffit pas : une page modifiée, un autre client ou un oubli le
laisseraient partir sans lui. Le serveur garde donc, en mémoire seulement, ce qu'il vient de transcrire.

- :func:`issue` : ``voice/transcribe`` y dépose le texte transcrit et rend un jeton opaque
  (``secrets.token_urlsafe``), lié au projet et à la session du cockpit (le processus et son jeton de session), à
  l'empreinte du texte normalisé, pour :data:`TTL_S` secondes ; au plus :data:`MAX_ENTRIES` jetons vivants, les plus
  anciens sont oubliés.
- :func:`sent_as_dictation` : lu par ``chat/send`` et ``chat/permission`` à la place du seul marqueur. Le texte
  est une dictée si (1) le marqueur est présent (:func:`grimoire.voice.safety.marked_dictation`), ou (2) un jeton
  vivant de cette session est renvoyé (usage unique : il est consommé, un rejeu n'ajoute rien), ou (3) il n'y a ni
  marqueur ni jeton mais le texte, une fois normalisé, **égale** une transcription vivante de cette session, ou en
  **contient** une d'au moins :data:`MIN_INCLUDED` caractères (``oui``, ``ok`` ne sont donc reconnus que par
  égalité exacte). Une transcription qui a servi à reconnaître un envoi est consommée : ce qui est retapé ensuite
  au clavier est accepté (un envoi refusé comme dictée se refait au clavier).

La normalisation : NFKC, minuscules, mots (lettres et chiffres) séparés par une espace ; la ponctuation et la casse
ne comptent pas.

Limites assumées : une dictée entièrement réécrite au clavier n'est plus une dictée ; un client qui n'appelle
jamais ``voice/transcribe`` (et n'envoie ni marqueur ni jeton) est un clavier, pas visé ; après :data:`TTL_S`
secondes, ou dans une autre session du cockpit ou un autre projet, la transcription est oubliée et le texte est
traité comme une saisie au clavier. Ni texte transcrit ni jeton ne sont écrits (journal, fichier, ledger).
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
from typing import Any

from grimoire.voice.safety import marked_dictation

__all__ = ["MAX_ENTRIES", "MAX_TOKENS_PER_REQUEST", "MIN_INCLUDED", "TTL_S", "issue", "reset", "sent_as_dictation"]

#: Durée de vie d'une transcription, en secondes : le temps de relire, corriger et envoyer.
TTL_S = 300.0
#: Jetons vivants au plus, tous projets confondus : mémoire bornée.
MAX_ENTRIES = 64
#: Longueur normalisée minimale pour qu'une transcription incluse dans un texte plus long le fasse reconnaître.
MIN_INCLUDED = 12
#: Jetons lus au plus dans une requête.
MAX_TOKENS_PER_REQUEST = 8
_MAX_TEXT = 4000
_WORD = re.compile(r"[^\W_]+")


@dataclass(frozen=True, slots=True)
class _Entry:
    scope: str
    fingerprint: str
    text: str
    expires: float


_lock = threading.Lock()
_entries: OrderedDict[str, _Entry] = OrderedDict()
_clock = time.monotonic


def _normalize(text: str) -> str:
    return " ".join(_WORD.findall(unicodedata.normalize("NFKC", text).casefold()))[:_MAX_TEXT]


def _scope(root: Path) -> str:
    from grimoire.tools.request_guard import session_token

    session = hashlib.sha256(session_token().encode("utf-8")).hexdigest()[:16]
    return f"{root}|{session}"


def _expire(now: float) -> None:
    for key in [k for k, e in _entries.items() if e.expires <= now]:
        del _entries[key]


def reset() -> None:
    """Oublie toutes les transcriptions (tests, arrêt)."""
    with _lock:
        _entries.clear()


def issue(root: Path, text: str) -> str:
    """Retient ``text`` (transcrit pour ``root``) et rend son jeton ; ``""`` s'il ne reste aucun mot."""
    norm = _normalize(text)
    if not norm:
        return ""
    token = secrets.token_urlsafe(24)
    entry = _Entry(_scope(root), hashlib.sha256(norm.encode("utf-8")).hexdigest(), norm, _clock() + TTL_S)
    with _lock:
        _expire(_clock())
        _entries[token] = entry
        while len(_entries) > MAX_ENTRIES:
            _entries.popitem(last=False)
    return token


def _tokens(body: dict[str, Any]) -> list[str]:
    raw = body.get("dictation_tokens")
    if not isinstance(raw, list):
        return []
    return [t for t in raw[:MAX_TOKENS_PER_REQUEST] if isinstance(t, str) and t]


def sent_as_dictation(root: Path, body: dict[str, Any]) -> bool:
    """Le corps d'une requête du chat vient-il d'une dictée ? Voir le module : marqueur, jeton vivant, ou texte
    reconnu comme une transcription récente de cette session. Consomme les jetons et transcriptions reconnus."""
    marked = marked_dictation(body)
    scope = _scope(root)
    text = body.get("text")
    sent = _normalize(text) if isinstance(text, str) else ""
    sent_print = hashlib.sha256(sent.encode("utf-8")).hexdigest()
    hit = False
    with _lock:
        _expire(_clock())
        for token in _tokens(body):
            entry = _entries.get(token)
            if entry is not None and entry.scope == scope:
                del _entries[token]  # usage unique
                hit = True
        if sent:
            for key, entry in list(_entries.items()):
                same = entry.scope == scope and (
                    entry.fingerprint == sent_print or (len(entry.text) >= MIN_INCLUDED and entry.text in sent)
                )
                if same:
                    del _entries[key]
                    hit = True
    return marked or hit
