"""Le message vocal sortant : résumé parlé, brouillon révisable jusqu'à l'écoute (#1343, lot V3).

Trois pièces, sans bibliothèque audio :

- :func:`spoken_instruction` : la consigne ajoutée au tour de l'orchestrateur en mode vocal — un
  message parlé court dans une balise ``<voix>…</voix>``, en plus de sa réponse écrite.
- :func:`spoken_from_reply` : le texte à dire. La balise d'abord ; à défaut, des règles déterministes
  (retrait du code, des tableaux, des chemins et des liens, plafond de durée, mention « le détail est
  à l'écran »). Aucun LLM local de reformulation.
- :class:`Outbox` : le brouillon. Tant qu'un message n'a pas été écouté, une nouvelle réponse le
  **remplace** (un seul message à jour). À la première lecture il est figé : la suite devient un nouveau
  message, et l'audio entendu ne change jamais. Seul le texte est révisé ; la synthèse n'a lieu qu'à
  la lecture (une seule, réutilisée), jamais à chaque révision.
"""

from __future__ import annotations

import itertools
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from grimoire.voice.engines import Speech

__all__ = [
    "DETAIL_ON_SCREEN",
    "Outbox",
    "OutboxMessage",
    "estimate_seconds",
    "spoken_from_reply",
    "spoken_instruction",
]

SPOKEN_TAG = "voix"
#: Débit moyen du français parlé : ~150 mots par minute.
WORDS_PER_SECOND = 2.5
#: Longueur moyenne d'un mot, espace comprise : un « mot » géant compte pour plusieurs mots.
CHARS_PER_WORD = 8
DETAIL_ON_SCREEN = "Le détail est à l'écran."

_TAG_RE = re.compile(rf"<{SPOKEN_TAG}>(.*?)</{SPOKEN_TAG}>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"```.*?(?:```|\Z)|~~~.*?(?:~~~|\Z)|<pre\b.*?(?:</pre>|\Z)", re.DOTALL | re.IGNORECASE)
_FENCE_LINE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_OPEN_TAG_RE = re.compile(rf"^<{SPOKEN_TAG}>", re.IGNORECASE | re.MULTILINE)
_CLOSE_TAG = f"</{SPOKEN_TAG}>"
_ANY_TAG_RE = re.compile(rf"</?{SPOKEN_TAG}>", re.IGNORECASE)
_QUOTE_LINE_RE = re.compile(r"^ {0,3}>")
#: Au plus deux resynthèses si le texte est révisé pendant la synthèse ; ensuite on lit la dernière synthèse.
MAX_RESYNTHESES = 2
_INLINE_CODE_RE = re.compile(r"(`+)[^`\n]*?\1")
_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_URL_RE = re.compile(r"https?://\S+")
_PATH_RE = re.compile(
    r"(?<![\w])(?:~|\.{1,2})?/?(?:[\w.@-]+/)+[\w.@-]*|\b[\w-]+\.(?:py|js|mjs|ts|md|json|ya?ml|toml|sh|css|html|rs|txt)\b"
)
_SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+")


def estimate_seconds(text: str) -> float:
    """Durée estimée de lecture, à ~150 mots par minute."""
    return round(_units(text) / WORDS_PER_SECOND, 1)


def _units(text: str) -> int:
    """Mots, mais jamais moins que ``caractères / CHARS_PER_WORD`` : un mot de 100 000 signes n'est pas un mot."""
    return max(len(text.split()), -(-len(text) // CHARS_PER_WORD))


def spoken_instruction(max_seconds: float) -> str:
    """La consigne ajoutée au message de la personne en mode vocal."""
    words = int(max_seconds * WORDS_PER_SECOND)
    return (
        f"[Mode vocal] En plus de ta réponse écrite habituelle, termine ta réponse par la balise "
        f"<{SPOKEN_TAG}>…</{SPOKEN_TAG}>, seule sur ses lignes et rien après elle : un message parlé de deux ou "
        f"trois phrases, {words} mots au plus (~{max_seconds:g} s), sans code, sans tableau, sans chemin de "
        f"fichier, et dis que le détail est à l'écran s'il y en a. N'écris cette balise nulle part ailleurs."
    )


def _clean(text: str) -> tuple[str, bool]:
    """Retire ce qui ne se dit pas. Rend le texte et s'il a fallu retirer quelque chose d'utile."""
    removed = False

    def drop(pattern: re.Pattern[str], value: str, repl: str = " ") -> str:
        nonlocal removed
        out, n = pattern.subn(repl, value)
        removed = removed or n > 0
        return out

    text = drop(_FENCE_RE, text)
    text = drop(_INLINE_CODE_RE, text)
    text = _LINK_RE.sub(lambda m: m.group(1), text)
    text = drop(_URL_RE, text)
    text = drop(_PATH_RE, text)
    kept: list[str] = []
    for line in text.splitlines():
        if line.strip().startswith("|"):  # ligne de tableau
            removed = True
            continue
        line = re.sub(r"^\s*(?:#{1,6}|[-*+]|\d+[.)]|>)\s+", "", line)
        kept.append(line.replace("**", "").replace("__", "").replace("*", "").strip())
    return " ".join(" ".join(kept).split()), removed


def _cap(text: str, max_seconds: float) -> tuple[str, bool]:
    budget = max(1, int(max_seconds * WORDS_PER_SECOND))
    if _units(text) <= budget:
        return text, False
    out: list[str] = []
    used = 0
    for sentence in _SENTENCE_RE.split(text):
        n = _units(sentence)
        if used + n > budget:
            break
        out.append(sentence)
        used += n
    if not out:  # une seule phrase plus longue que le plafond : coupe au mot, puis au caractère
        head = text[: budget * CHARS_PER_WORD]
        words = head.split()[:budget]
        if len(words) > 1 and not text[len(head) : len(head) + 1].isspace():
            words = words[:-1]  # le dernier mot est coupé en deux : on ne le dit pas
        out = [" ".join(words).rstrip(",;:") + "…"]
    return " ".join(out), True


def _open_block_at(prefix: str) -> bool:
    """Vrai si ``prefix`` s'achève dans un bloc non fermé : clôture ``` ou ~~~, ``<pre>``, ``<code>``, commentaire HTML."""
    fence = ""
    for line in prefix.splitlines():
        m = _FENCE_LINE_RE.match(line)
        if not m:
            continue
        marker = m.group(1)
        if not fence:
            fence = marker
        elif marker[0] == fence[0] and len(marker) >= len(fence) and not m.group(2).strip():
            fence = ""
    if fence:
        return True
    lowered = prefix.lower()
    return any(
        lowered.count(o) > lowered.count(c) for o, c in (("<pre", "</pre>"), ("<code", "</code>"), ("<!--", "-->"))
    )


def _top_level(reply: str, start: int) -> bool:
    """Vrai si la balise ouvrante en ``start`` (début de ligne, sans retrait) n'est ni dans un bloc ouvert ni la suite d'une citation."""
    prefix = reply[:start]
    if _open_block_at(prefix):
        return False
    previous = next((line for line in reversed(prefix.splitlines()) if line.strip()), "")
    return not _QUOTE_LINE_RE.match(previous)  # continuation paresseuse d'un ``>`` : encore la citation


def _final_tag(reply: str) -> str | None:
    """Le contenu de la balise parlée, seulement quand elle termine la réponse sans ambiguïté.

    Règle de position, pas de grammaire Markdown devinée : la réponse (espaces finaux retirés) se termine par
    ``</voix>`` ; la balise ouvrante correspondante commence une ligne, sans retrait (donc ni code indenté ni
    code en ligne ni citation), hors de tout bloc ouvert ; c'est la **seule** balise ouvrante de ce genre dans la
    réponse ; son contenu ne contient ni autre balise ni clôture de bloc. Sinon ``None`` : repli déterministe.
    Une balise recopiée dans un bloc de code ou une citation ne compte pas ; une balise recopiée en clair en début
    de ligne, en plus de la vraie, rend la réponse ambiguë et on ne lit aucune des deux.
    """
    body = reply.rstrip()
    if not body.lower().endswith(_CLOSE_TAG):
        return None
    starts = [m.start() for m in _OPEN_TAG_RE.finditer(body) if _top_level(body, m.start())]
    if len(starts) != 1:
        return None
    content = body[starts[0] + len(SPOKEN_TAG) + 2 : -len(_CLOSE_TAG)]
    if _ANY_TAG_RE.search(content) or "```" in content or "~~~" in content:
        return None
    return content


def spoken_from_reply(reply: str, max_seconds: float) -> tuple[str, str]:
    """Le texte à dire pour une réponse, et sa source (``"tag"`` ou ``"rules"``). ``("", "none")`` s'il n'y a rien à dire."""
    tag = _final_tag(reply)  # une balise citée ou hors position n'est jamais lue : repli déterministe
    source = "tag"
    if tag is not None:
        text, removed = _clean(tag)
    else:
        text, removed = "", False
    if not text:
        source = "rules"
        text, removed = _clean(_TAG_RE.sub(" ", reply))
    if not text:
        return "", "none"
    text, cut = _cap(text, max_seconds)
    if (removed or cut) and "écran" not in text.lower():
        text = f"{text} {DETAIL_ON_SCREEN}"
    return text, source


@dataclass(slots=True)
class OutboxMessage:
    id: str
    text: str
    source: str
    revisions: int = 1
    played: bool = False  # figé : plus aucune révision
    audio: Speech | None = field(default=None, repr=False)

    @property
    def duration_s(self) -> float:
        return estimate_seconds(self.text)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "source": self.source,
            "revisions": self.revisions,
            "played": self.played,
            "duration_s": self.duration_s,
        }


class Outbox:
    """Les messages parlés d'un projet, gardés en mémoire : une boîte d'envoi est perdue au redémarrage. ``synthesize`` : ``texte -> Speech`` (le moteur de synthèse)."""

    def __init__(self, synthesize: Callable[[str], Speech], *, keep: int = 20) -> None:
        self._synthesize = synthesize
        self._keep = keep
        self._lock = threading.RLock()
        self._messages: list[OutboxMessage] = []
        self._ids = itertools.count(1)

    def submit(self, text: str, source: str = "rules") -> OutboxMessage | None:
        """Dépose le texte d'une nouvelle réponse : remplace le message non écouté, sinon en ouvre un."""
        text = text.strip()
        if not text:
            return None
        with self._lock:
            last = self._messages[-1] if self._messages else None
            if last is not None and not last.played:
                last.text, last.source = text, source
                last.revisions += 1
                last.audio = None  # jamais d'audio d'un texte périmé
                return last
            return self._open(text, source)

    def _open(self, text: str, source: str) -> OutboxMessage:
        """Ouvre un nouveau message (verrou tenu par l'appelant)."""
        message = OutboxMessage(id=f"v{next(self._ids)}", text=text, source=source)
        self._messages.append(message)
        del self._messages[: -self._keep]
        return message

    def get(self, message_id: str) -> OutboxMessage | None:
        with self._lock:
            return next((m for m in self._messages if m.id == message_id), None)

    def play(self, message_id: str) -> tuple[OutboxMessage, Speech]:
        """Fige le message et rend son audio, synthétisé à ce moment-là seulement, une fois.

        La synthèse tourne hors verrou (elle ne bloque pas ``submit``) ; le message n'est figé qu'après
        une synthèse réussie. Si le texte a été révisé pendant ce temps, on resynthétise le nouveau, au plus
        :data:`MAX_RESYNTHESES` fois : ensuite on lit la dernière version synthétisée et le texte plus récent
        devient un nouveau message.
        """
        attempt = 0
        while True:
            with self._lock:
                message = self.get(message_id)
                if message is None:
                    raise KeyError(message_id)
                if message.audio is not None:
                    message.played = True
                    return message, message.audio
                text, source, revisions = message.text, message.source, message.revisions
            speech = self._synthesize(text)  # échec : le message reste révisable
            with self._lock:
                if message.revisions == revisions or message.played:
                    message.audio = message.audio or speech
                    message.played = True
                    return message, message.audio
                if attempt >= MAX_RESYNTHESES:
                    newer, newer_source = message.text, message.source
                    message.text, message.source, message.revisions = text, source, revisions
                    message.audio, message.played = speech, True
                    self._open(newer, newer_source)
                    return message, speech
            attempt += 1

    def state(self) -> dict[str, Any]:
        with self._lock:
            return {"messages": [m.public() for m in self._messages]}
