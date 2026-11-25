"""Le message vocal sortant : résumé parlé, brouillon révisable jusqu'à l'écoute (#1343, lot V3).

Trois pièces, sans bibliothèque audio :

- :func:`spoken_instruction` : la consigne ajoutée au tour de l'orchestrateur en mode vocal — terminer
  sa réponse écrite par un message parlé court dans une balise ``<voix-JETON>…</voix-JETON>``. Le jeton
  (:func:`new_spoken_token`) est tiré au hasard à chaque tour : un contenu cité (fichier, sortie d'outil,
  exemple) ne peut pas le connaître, donc ne peut pas se faire passer pour le message de l'orchestrateur.
- :func:`spoken_from_reply` : le texte à dire. La balise au jeton du tour d'abord ; à défaut, des règles déterministes
  (retrait du code, des tableaux, des chemins et des liens, plafond de durée, mention « le détail est
  à l'écran »). Aucun LLM local de reformulation.
- :class:`Outbox` : le brouillon. Tant qu'un message n'a pas été écouté, une nouvelle réponse le
  **remplace** (un seul message à jour). À la première lecture il est figé : la suite devient un nouveau
  message, et l'audio entendu ne change jamais. Seul le texte est révisé ; la synthèse n'a lieu qu'à
  la lecture (une seule, réutilisée), jamais à chaque révision.
"""

from __future__ import annotations

import html
import itertools
import re
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from grimoire.voice.engines import Speech

__all__ = [
    "DETAIL_ON_SCREEN",
    "QUESTION_MAX_S",
    "Outbox",
    "OutboxMessage",
    "asks_a_question",
    "estimate_seconds",
    "new_spoken_token",
    "spoken_from_reply",
    "spoken_instruction",
    "spoken_question",
]

SPOKEN_TAG = "voix"
#: Débit moyen du français parlé : ~150 mots par minute.
WORDS_PER_SECOND = 2.5
#: Longueur moyenne d'un mot, espace comprise : un « mot » géant compte pour plusieurs mots.
CHARS_PER_WORD = 8
DETAIL_ON_SCREEN = "Le détail est à l'écran."
#: Plafond d'une question vocale (lot V4) : bien plus court qu'un compte rendu.
QUESTION_MAX_S = 15.0
OPTIONS_ON_SCREEN = "Les autres options sont à l'écran."

_FENCE_RE = re.compile(r"```.*?(?:```|\Z)|~~~.*?(?:~~~|\Z)|<pre\b.*?(?:</pre>|\Z)", re.DOTALL | re.IGNORECASE)
_COMMENT_RE = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)
#: Un élément ``<nom …>…</nom>`` et son contenu ; non fermé, il court jusqu'à la fin (on ne lit pas un contenu balisé).
#: Comme en HTML, ``<`` suivi d'une lettre (ou ``</`` d'une lettre) : « a < b » reste de la prose.
_ELEMENT_RE = re.compile(r"<([^\W\d_][^\s<>/]*)[^<>]*>.*?(?:</\1\s*>|\Z)", re.DOTALL | re.IGNORECASE)
_RESIDUAL_TAG_RE = re.compile(r"</?[^\W\d_][^<>\n]{0,200}>|</?[^\W\d_][^\s<>]*")
_QUOTE_LINE_RE = re.compile(r"^\s*(?:(?:[-*+]|\d+[.)])\s+)*>")
_TOKEN_CHARS = frozenset("0123456789abcdef")
#: Le repli ne garde qu'une poignée de phrases (~112 mots) : il ne lit jamais plus que ce début de réponse.
#: Borne aussi le coût des expressions rationnelles, quel que soit le contenu (une réponse peut faire des Mo).
CLEAN_MAX_CHARS = 8000
#: Au plus deux resynthèses si le texte est révisé pendant la synthèse ; ensuite on lit la dernière synthèse.
MAX_RESYNTHESES = 2
_INLINE_CODE_RE = re.compile(r"(`+)[^`\n]{0,500}?\1")
_LINK_RE = re.compile(r"!?\[([^\]\n]{0,300})\]\([^)\n]{0,1000}\)")
_URL_RE = re.compile(r"https?://\S+")
#: Un chemin ne commence qu'en bord de mot (pas de départ à chaque caractère d'une longue suite) et ses
#: segments sont possessifs : aucun retour arrière, coût linéaire.
_PATH_RE = re.compile(
    r"(?<![\w.@/~-])(?:~|\.{1,2})?/?(?:[\w.@-]++/)++[\w.@-]*+"
    r"|\b[\w-]+\.(?:py|js|mjs|ts|md|json|ya?ml|toml|sh|css|html|rs|txt)\b"
)
_SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+")


def estimate_seconds(text: str) -> float:
    """Durée estimée de lecture, à ~150 mots par minute."""
    return round(_units(text) / WORDS_PER_SECOND, 1)


def _units(text: str) -> int:
    """Mots, mais jamais moins que ``caractères / CHARS_PER_WORD`` : un mot de 100 000 signes n'est pas un mot."""
    return max(len(text.split()), -(-len(text) // CHARS_PER_WORD))


def new_spoken_token() -> str:
    """Un jeton neuf pour un tour : huit chiffres hexadécimaux, jamais réutilisé."""
    return secrets.token_hex(4)


def interview_instruction(chantier: str) -> str:
    """Ce que le mode vocal ajoute quand un entretien de cadrage est en cours pour le projet (lot V4)."""
    flag = "" if chantier == "principal" else f" --chantier {chantier}"
    return (
        f" Un entretien de cadrage est en cours : dans la balise, pose UNE seule question, courte "
        f"(~{QUESTION_MAX_S:g} s), formulée pour être entendue (ni liste, ni code, ni chemin), qui se termine "
        f"par un point d'interrogation ; elle s'affichera comme question en tête de la boîte vocale. "
        f"Quand la personne répond, sa réponse est une transcription : elle peut se tromper sur les noms propres "
        f"et les nombres. Reformule la réponse entendue, puis consigne-la comme une réponse écrite : "
        f"`grimoire cadrage interview{flag} answer <angle> \"<réponse>\"`. Si l'entretien est fini, ne pose "
        f"pas de question."
    )


def spoken_instruction(max_seconds: float, token: str, interview: str | None = None) -> str:
    """La consigne ajoutée au message de la personne en mode vocal, pour le tour au jeton ``token``.

    ``interview`` : le chantier dont l'entretien de cadrage est en cours (lot V4), s'il y en a un.
    """
    words = int(max_seconds * WORDS_PER_SECOND)
    tag = f"{SPOKEN_TAG}-{token}"
    extra = interview_instruction(interview) if interview else ""
    return (
        f"[Mode vocal] En plus de ta réponse écrite habituelle, termine ta réponse par la balise "
        f"<{tag}>…</{tag}>, une seule fois et rien après elle : un message parlé de deux ou trois phrases, "
        f"{words} mots au plus (~{max_seconds:g} s), sans code, sans tableau, sans chemin de fichier, et dis que "
        f"le détail est à l'écran s'il y en a. Ce nom de balise ne vaut que pour ce tour.{extra}"
    )


def _clean(text: str) -> tuple[str, bool]:
    """Retire ce qui ne se dit pas. Rend le texte et s'il a fallu retirer quelque chose d'utile.

    Code, commentaires et éléments balisés (avec leur contenu), balises résiduelles, accents graves, liens,
    chemins, tableaux et citations ``>`` ne sont jamais lus.
    """
    removed = False

    def drop(pattern: re.Pattern[str], value: str, repl: str = " ") -> str:
        nonlocal removed
        out, n = pattern.subn(repl, value)
        removed = removed or n > 0
        return out

    if len(text) > CLEAN_MAX_CHARS:  # avant toute expression rationnelle
        text, removed = text[:CLEAN_MAX_CHARS], True
    text = html.unescape(text.replace("\r\n", "\n").replace("\r", "\n"))
    text = drop(_FENCE_RE, text)
    text = drop(_COMMENT_RE, text)
    text = drop(_INLINE_CODE_RE, text)
    text = drop(_ELEMENT_RE, text)
    text = drop(_RESIDUAL_TAG_RE, text)
    text = _LINK_RE.sub(lambda m: m.group(1), text)
    text = drop(_URL_RE, text)
    text = drop(_PATH_RE, text)
    kept: list[str] = []
    for line in text.splitlines():
        if line.strip().startswith("|") or _QUOTE_LINE_RE.match(line):  # tableau, citation
            removed = True
            continue
        line = re.sub(r"^\s*(?:#{1,6}|[-*+]|\d+[.)])\s+", "", line)
        line = line.replace("**", "").replace("__", "").replace("*", "")
        if "`" in line:
            removed = True
            line = line.replace("`", " ")
        kept.append(line.strip())
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


def _final_tag(reply: str, token: str | None) -> tuple[str | None, bool]:
    """Le contenu de la balise au jeton du tour, et s'il y a ambiguïté.

    Acceptée seulement si ``<voix-JETON>`` et ``</voix-JETON>`` (casse exacte) apparaissent chacune une seule fois et que la
    réponse (espaces finaux retirés) se termine par la balise fermante. Toute autre balise (``<voix>``, un autre
    jeton) n'est que du texte. Le jeton présent mais hors de cette forme (recopié, doublé) : ambiguïté, repli.
    """
    if not token or len(token) != 8 or not _TOKEN_CHARS.issuperset(token):
        return None, False
    # Casse exacte, positions prises sur le texte lui-même (jamais sur une copie transformée), sans regex.
    opening, closing = f"<{SPOKEN_TAG}-{token}>", f"</{SPOKEN_TAG}-{token}>"
    n_open, n_close = reply.count(opening), reply.count(closing)
    if not n_open and not n_close:
        return None, False
    body = reply.rstrip()
    if n_open != 1 or n_close != 1 or not body.endswith(closing):
        return None, True
    start = body.find(opening) + len(opening)
    return body[start : len(body) - len(closing)], False


def spoken_from_reply(reply: str, max_seconds: float, token: str | None = None) -> tuple[str, str]:
    """Le texte à dire pour une réponse, et sa source (``"tag"`` ou ``"rules"``). ``("", "none")`` si la réponse est vide.

    ``token`` : le jeton du tour (:func:`new_spoken_token`) ; sans lui, aucune balise n'est lue.
    Quand il ne reste rien d'utile à dire, on dit que le détail est à l'écran plutôt que rien.
    """
    if not reply.strip():
        return "", "none"
    tag, ambiguous = _final_tag(reply, token)
    source = "tag"
    text, removed = _clean(tag) if tag is not None else ("", False)
    if not text:
        source = "rules"
        text, removed = _clean(reply)
        removed = removed or ambiguous
    if not text:
        return DETAIL_ON_SCREEN, "rules"
    text, cut = _cap(text, max_seconds)
    if (removed or cut) and "écran" not in text.lower():
        text = f"{text} {DETAIL_ON_SCREEN}"
    return text, source


def asks_a_question(text: str) -> bool:
    """Le message dit une question : il se termine par un point d'interrogation (hors mention « détail à l'écran »)."""
    body = text.strip().removesuffix(DETAIL_ON_SCREEN).rstrip()
    return body.endswith("?")


def spoken_question(text: str, options: tuple[str, ...] | list[str] = (), max_seconds: float = QUESTION_MAX_S) -> str:
    """Le texte parlé d'une question à options : la question, puis les options tant que le plafond le permet.

    Même nettoyage que le compte rendu (jamais de code ni de chemin). Les options qui ne tiennent pas sont
    renvoyées à l'écran, où elles restent cliquables ; la question, elle, n'est coupée qu'en dernier recours.
    """
    question, _ = _clean(text)
    question, _cut = _cap(question, max_seconds)
    cleaned = [c for c in (_clean(o)[0] for o in options) if c][:8]
    budget = max(1, int(max_seconds * WORDS_PER_SECOND))
    used = _units(question)
    spoken: list[str] = []
    for option in cleaned:
        if used + _units(option) + 2 > budget:
            break
        spoken.append(option)
        used += _units(option) + 1
    if not spoken:
        return f"{question} {OPTIONS_ON_SCREEN}".strip() if cleaned else question
    listed = spoken[0] if len(spoken) == 1 else f"{', '.join(spoken[:-1])} ou {spoken[-1]}"
    tail = f" {OPTIONS_ON_SCREEN}" if len(spoken) < len(cleaned) else ""
    return f"{question} Options : {listed}.{tail}"


@dataclass(slots=True)
class OutboxMessage:
    id: str
    text: str
    source: str
    revisions: int = 1
    played: bool = False  # figé : plus aucune révision
    audio: Speech | None = field(default=None, repr=False)
    kind: str = "report"  # « report » (compte rendu) ou « question » (lot V4)
    options: tuple[str, ...] = ()
    ref: tuple[str, str] | None = None  # (tâche, question) du pilote ; None pour une question de l'entretien
    answered: bool = False

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
            "kind": self.kind,
            "options": list(self.options),
            "ref": {"task_id": self.ref[0], "question_id": self.ref[1]} if self.ref else None,
            "answered": self.answered,
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
            # Un compte rendu ne remplace que le dernier COMPTE RENDU : une question reste un message distinct.
            last = next((m for m in reversed(self._messages) if m.kind == "report"), None)
            if last is not None and not last.played:
                last.text, last.source = text, source
                last.revisions += 1
                last.audio = None  # jamais d'audio d'un texte périmé
                return last
            return self._open(text, source)

    def submit_question(
        self, text: str, options: tuple[str, ...] = (), ref: tuple[str, str] | None = None, source: str = "question"
    ) -> OutboxMessage | None:
        """Dépose une question : toujours un message distinct, jamais fusionné dans un compte rendu.

        Une question du pilote (``ref``) n'est déposée qu'une fois, quel que soit le nombre de relevés.
        """
        text = text.strip()
        if not text:
            return None
        with self._lock:
            if ref is not None:
                known = next((m for m in self._messages if m.ref == ref), None)
                if known is not None:
                    return known
            return self._open(text, source, kind="question", options=tuple(options), ref=ref)

    def resolve_question(self, ref: tuple[str, str] | None = None) -> list[OutboxMessage]:
        """Marque répondue la question du pilote ``ref`` ; sans ``ref``, celles de l'entretien (la personne vient de répondre)."""
        with self._lock:
            hit = [m for m in self._messages if m.kind == "question" and m.ref == ref and not m.answered]
            for message in hit:
                message.answered = True
            return hit

    def _open(
        self, text: str, source: str, *, kind: str = "report", options: tuple[str, ...] = (),
        ref: tuple[str, str] | None = None,
    ) -> OutboxMessage:
        """Ouvre un nouveau message (verrou tenu par l'appelant). Une question sans réponse n'est jamais évincée."""
        message = OutboxMessage(
            id=f"v{next(self._ids)}", text=text, source=source, kind=kind, options=options, ref=ref
        )
        self._messages.append(message)
        while len(self._messages) > self._keep:
            victim = next((m for m in self._messages if not (m.kind == "question" and not m.answered)), None)
            if victim is None or victim is message:
                break
            self._messages.remove(victim)
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
            # Les questions sans réponse d'abord (la plus ancienne en tête), puis le reste dans l'ordre.
            waiting = [m for m in self._messages if m.kind == "question" and not m.answered]
            rest = [m for m in self._messages if m not in waiting]
            return {"messages": [m.public() for m in (*waiting, *rest)]}
