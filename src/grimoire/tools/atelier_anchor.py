"""Ancres des commentaires de l'Atelier de spec (lot 3 de l'épique #1197) : retrouver un passage après modification.

Un commentaire porte le bloc visé (identifiant, empreinte, **rang dans la phase et empreinte de ses deux voisins**),
l'extrait cité, sa position dans le bloc et le contexte de chaque côté. Le document change sous le commentaire ;
:func:`locate` dit où le commentaire vit **aujourd'hui**, sans rien écrire.

Principe : **un fil ne quitte jamais son bloc d'origine.** Un autre bloc qui porte le même texte ou la même empreinte
n'est pas ce bloc ; au moindre doute, le fil est orphelin. Règle, la première qui conclut gagne :

0. **Un orphelin le reste.** Un fil marqué orphelin au magasin (:attr:`Comment.orphelin`) ne se réancre plus de lui-même.
1. **Le bloc visé n'a pas changé** : un bloc de la phase a son empreinte *et* c'est bien sa place (même rang, ou au moins
   un voisin identique : un bloc inséré ou supprimé avant lui ne le détache pas). Les blocs voisins peuvent changer
   sans que le fil bouge. Un bloc de même texte mais d'une autre place (rang et voisins différents) ne compte pas.
   **Dès qu'un homonyme existe** (plusieurs blocs de la phase portent cette empreinte), seul celui dont les DEUX voisins
   sont ceux de l'ancrage convient, et il doit être unique : jamais de départage par le rang, sinon orphelin.
2. **Le bloc visé a été modifié sur place** : c'est le bloc compris entre les deux mêmes voisins (empreintes des deux
   identiques, et un seul bloc dans ce cas). L'extrait n'y est cherché que **dans ce bloc**, mot pour mot : retrouvé
   (``retrouve``) s'il y figure une seule fois, ou, s'il y figure plusieurs fois, si une seule occurrence a exactement le
   même contexte avant et après ; sinon orphelin.
   Si l'extrait n'y figure plus du tout (le bloc a été réécrit sur place, même place entre les mêmes voisins), le fil
   suit le passage qui lui correspond (mots entiers) **seulement si le nouveau texte ressemble assez à l'ancre**
   (:data:`REFIT_MIN`) : un bloc remplacé par un texte sans rapport entre les mêmes voisins reste orphelin.
3. **Le bloc visé a été réécrit et ses voisins aussi** (aucun bloc de la phase n'a son empreinte) : meilleure
   correspondance de texte entre l'ancre (contexte et extrait) et les blocs, au-dessus d'un seuil prudent
   (:data:`SIMILAR_MIN`) et **unique** (le suivant est nettement plus bas ; l'extrait ne figure pas dans plusieurs
   blocs), sinon orphelin. Les comparaisons se font par mots, sur des blocs plafonnés (:data:`BLOCK_TEXT_MAX`).
4. **Sinon** : ``orphelin``. Jamais de ré-ancrage sur un bloc ambigu ni sur un homonyme.

Un fil « retrouvé » (:attr:`Comment.deplace`) le reste : la marque est écrite au magasin et ne s'efface pas.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any, Protocol

from grimoire.core.atelier_comments import CONTEXT_MAX, QUOTE_MAX
from grimoire.tools import atelier_blocs


class Anchored(Protocol):
    """Ce que :func:`locate` lit d'un commentaire."""

    block_fingerprint: str
    quote: str
    start: int
    before: str
    after: str
    rank: int
    prev: str
    next: str
    orphelin: bool
    deplace: bool


@dataclass(frozen=True, slots=True)
class Anchor:
    """Où vit le commentaire : ``ancre`` (bloc inchangé), ``retrouve`` (bloc modifié, extrait retrouvé) ou ``orphelin``."""

    state: str
    block_id: str = ""
    start: int = 0
    end: int = 0
    near: str = ""
    #: Le passage tel qu'il se lit aujourd'hui, quand il diffère de l'extrait gardé (bloc réécrit) ; vide sinon.
    quote: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state, "block_id": self.block_id, "start": self.start, "end": self.end, "near": self.near,
            "quote": self.quote,
        }


def context(text: str, start: int, end: int) -> tuple[str, str]:
    """Ce qui précède et ce qui suit ``text[start:end]``, bornés à :data:`CONTEXT_MAX` caractères."""
    return text[max(0, start - CONTEXT_MAX) : start], text[end : end + CONTEXT_MAX]


def around(blocks: Sequence[atelier_blocs.Block], index: int) -> tuple[int, str, str]:
    """L'identité d'un bloc dans sa phase : son rang et l'empreinte de chacun de ses voisins (``""`` au bord)."""
    before = atelier_blocs.fingerprint(blocks[index - 1].text) if index > 0 else ""
    after = atelier_blocs.fingerprint(blocks[index + 1].text) if index + 1 < len(blocks) else ""
    return index, before, after


def occurrences(text: str, needle: str) -> list[int]:
    found: list[int] = []
    at = text.find(needle)
    while at >= 0:
        found.append(at)
        at = text.find(needle, at + len(needle))
    return found


def _fp(block: atelier_blocs.Block) -> str:
    return atelier_blocs.fingerprint(block.text)


def _in_place(blocks: Sequence[atelier_blocs.Block], index: int, item: Anchored) -> bool:
    """Le bloc ``index`` est à la place du bloc visé : même rang, ou au moins un voisin (non vide) identique."""
    _rank, before, after = around(blocks, index)
    return index == item.rank or (bool(before) and before == item.prev) or (bool(after) and after == item.next)


def locate(blocks: Sequence[atelier_blocs.Block], item: Anchored) -> Anchor:
    """Où le commentaire ``item`` vit dans ``blocks`` (les blocs de sa phase, tels qu'aujourd'hui). N'écrit rien."""
    if item.orphelin:
        return Anchor("orphelin")
    held = "retrouve" if item.deplace else "ancre"
    same = [i for i, b in enumerate(blocks) if _fp(b) == item.block_fingerprint]
    if len(same) > 1:
        # Des homonymes : seul celui dont les DEUX voisins sont ceux de l'ancrage convient, et il doit être unique.
        # Jamais de départage par le rang (une copie insérée avant l'original prend son rang).
        placed = [i for i in same if around(blocks, i)[1:] == (item.prev, item.next)]
        if len(placed) != 1:
            return Anchor("orphelin", near=blocks[same[0]].id)  # le doute entre des blocs identiques
    else:
        placed = [i for i in same if _in_place(blocks, i, item)]
    if len(placed) == 1:
        block = blocks[placed[0]]
        if block.text[item.start : item.start + len(item.quote)] == item.quote:
            return Anchor(held, block.id, item.start, item.start + len(item.quote))
        return Anchor("orphelin", near=block.id)  # position qui ne dit plus l'extrait : on ne devine pas
    slot = [
        i for i, b in enumerate(blocks)
        if b.editable and _fp(b) != item.block_fingerprint and around(blocks, i)[1:] == (item.prev, item.next)
    ]
    if len(slot) > 1 or (not slot and same):
        return Anchor("orphelin")
    if not slot:
        return _by_similarity(blocks, item)
    block = blocks[slot[0]]
    found = occurrences(block.text, item.quote)
    if len(found) > 1:
        found = [at for at in found if context(block.text, at, at + len(item.quote)) == (item.before, item.after)]
    if len(found) == 1:
        return Anchor("retrouve", block.id, found[0], found[0] + len(item.quote))
    if not occurrences(block.text, item.quote):
        # Réécrit sur place : le fil suit le bloc SI le nouveau texte ressemble assez à l'ancre ; un remplacement sans
        # rapport entre les mêmes voisins reste orphelin (un extrait ambigu, lui aussi).
        return _refit(block, item, REFIT_MIN)
    return Anchor("orphelin")


# ── Mots, similarité et suivi d'un bloc réécrit ─────────────────────────────
#
# Tout se compare par MOTS (``\w+``) : les extraits suivis tombent sur des frontières de mots, et le coût reste faible
# (un bloc de 4 000 caractères fait quelques centaines de mots). Les plafonds ci-dessous bornent le pire cas ; au-delà,
# on ne calcule pas de diff : l'extrait retrouvé mot pour mot, sinon orphelin.

#: Similarité minimale (part, en caractères de mots, de l'ancre retrouvée dans le bloc) pour suivre un bloc réécrit
#: sur place, et pour suivre par ressemblance quand les voisins ont changé aussi (plus exigeant).
REFIT_MIN = 0.6
SIMILAR_MIN = 0.8
SIMILAR_GAP = 0.2
#: Plafonds : taille d'un bloc comparé, nombre de candidats par fil, mots d'un diff.
BLOCK_TEXT_MAX = 4000
CANDIDATES_MAX = 300
_DIFF_TOKENS_MAX = 1500
_WORD = re.compile(r"\w+")


def _words(text: str) -> list[tuple[str, int, int]]:
    """``(mot, début, fin)`` de chaque mot de ``text``."""
    return [(m.group(), m.start(), m.end()) for m in _WORD.finditer(text)]


def _clip(text: str, begin: int, end: int) -> tuple[int, int]:
    """``[begin, end)`` borné à :data:`QUOTE_MAX` caractères, recoupé sur une fin de mot."""
    if end - begin <= QUOTE_MAX:
        return begin, end
    cut = max((w[2] for w in _words(text[begin : begin + QUOTE_MAX]) if w[2] < QUOTE_MAX), default=QUOTE_MAX)
    return begin, begin + cut


def _whole_span(text: str) -> tuple[int, int]:
    """Le texte entier (borné, sur une frontière de mot)."""
    return _clip(text, 0, len(text))


class Rewrite:
    """Le diff par mots entre l'ancien texte d'un bloc et le nouveau, calculé UNE fois pour tous les fils du bloc."""

    def __init__(self, old: str, new: str) -> None:
        self.old, self.new = old, new
        self._old, self._new = _words(old), _words(new)
        self.ops: list[Any] | None = None
        if self._old and self._new and max(len(self._old), len(self._new)) <= _DIFF_TOKENS_MAX and max(len(old), len(new)) <= BLOCK_TEXT_MAX * 5:
            matcher = SequenceMatcher(None, [w[0] for w in self._old], [w[0] for w in self._new], autojunk=False)
            self.ops = matcher.get_opcodes()

    def matched(self) -> int:
        """Caractères de mots de l'ancien texte qui se retrouvent dans le nouveau."""
        return sum(
            sum(w[2] - w[1] for w in self._old[i1:i2]) for tag, i1, i2, _j1, _j2 in (self.ops or []) if tag == "equal"
        )

    def span(self, start: int, end: int) -> tuple[int, int] | None:
        """Où ``old[start:end]`` se lit dans le nouveau texte (mots entiers) ; ``None`` si rien n'y correspond."""
        if self.ops is None:
            return None
        hit = [i for i, w in enumerate(self._old) if w[1] < end and w[2] > start]
        if not hit:
            return None
        first, last = hit[0], hit[-1]
        j_first = j_last = -1
        for tag, i1, i2, j1, j2 in self.ops:
            if i1 <= first < i2:
                j_first = j1 + (first - i1) if tag == "equal" else j1
            if i1 <= last < i2:
                j_last = j1 + (last - i1) + 1 if tag == "equal" else j2
        if j_first < 0 or j_last <= j_first or j_last > len(self._new):
            return None
        return _clip(self.new, self._new[j_first][1], self._new[j_last - 1][2])


def _window(item: Anchored) -> tuple[str, int, int]:
    """L'ancre telle qu'elle se lit : contexte avant, extrait, contexte après (et la place de l'extrait)."""
    return item.before + item.quote + item.after, len(item.before), len(item.before) + len(item.quote)


@lru_cache(maxsize=4096)
def _fit(window: str, begin: int, end: int, text: str) -> tuple[float, tuple[int, int]]:
    """``(similarité, plage)`` de l'ancre ``window`` (l'extrait est ``window[begin:end]``) dans ``text``. Mémorisé : la
    lecture des fils ne refait pas le même calcul. Bloc trop long ou sans mot commun : ``(0.0, bloc entier)``."""
    if not text or len(text) > BLOCK_TEXT_MAX or not window:
        return 0.0, _whole_span(text)
    rewrite = Rewrite(window, text)
    total = sum(w[2] - w[1] for w in rewrite._old)
    if not total or rewrite.ops is None:
        return 0.0, _whole_span(text)
    if begin == 0 and window[:QUOTE_MAX] == window[begin:end]:
        place = _whole_span(text)
    else:
        place = rewrite.span(begin, end) or _whole_span(text)
    return rewrite.matched() / total, place


def _refit(block: atelier_blocs.Block, item: Anchored, minimum: float) -> Anchor:
    """Suit ``item`` dans ``block`` (réécrit) si l'ancre y ressemble au moins à ``minimum`` ; sinon orphelin."""
    window, begin, end = _window(item)
    similarity, (a, b) = _fit(window, begin, end, block.text)
    if similarity < minimum:
        return Anchor("orphelin")
    return Anchor("retrouve", block.id, a, b, quote=block.text[a:b])


def _by_similarity(blocks: Sequence[atelier_blocs.Block], item: Anchored) -> Anchor:
    """Le bloc qui ressemble le plus à l'ancre (contexte et extrait), s'il est seul à dépasser le seuil."""
    window, begin, end = _window(item)
    candidates = [b for b in blocks if b.editable and len(b.text) <= BLOCK_TEXT_MAX][:CANDIDATES_MAX]
    if sum(1 for b in blocks if item.quote in b.text) > 1:
        return Anchor("orphelin")  # le même extrait dans plusieurs blocs : le doute, jamais un choix
    scored = sorted(((_fit(window, begin, end, b.text)[0], i) for i, b in enumerate(candidates)), reverse=True)
    if not scored or scored[0][0] < SIMILAR_MIN or (len(scored) > 1 and scored[1][0] > scored[0][0] - SIMILAR_GAP):
        return Anchor("orphelin")
    block = candidates[scored[0][1]]
    found = occurrences(block.text, item.quote)
    if len(found) == 1:
        return Anchor("retrouve", block.id, found[0], found[0] + len(item.quote))
    return _refit(block, item, SIMILAR_MIN)


def follow(rewrite: Rewrite | None, item: Anchored, new_text: str) -> tuple[int, int] | None:
    """Où le passage de ``item`` se lit dans ``new_text`` (le même bloc, réécrit par la route d'édition) : la plage, ou
    le texte entier (borné) quand rien ne subsiste ; ``None`` quand le bloc est trop gros pour un diff et que ni l'extrait
    mot pour mot ni une ressemblance suffisante ne le retrouvent (le fil devient orphelin, comme avant ce suivi)."""
    found = occurrences(new_text, item.quote)
    if len(found) == 1:
        return found[0], found[0] + len(item.quote)  # inchangé : rien à calculer
    start, end = item.start, item.start + len(item.quote)
    if rewrite is not None and rewrite.ops is not None:
        if start == 0 and rewrite.old[:QUOTE_MAX] == rewrite.old[start:end]:
            return _whole_span(new_text)  # le fil portait sur le bloc entier : il porte sur le bloc entier
        return rewrite.span(start, end) or _whole_span(new_text)
    bracketed = _bracket(item, new_text)
    if bracketed is not None:
        return bracketed
    window, begin, stop = _window(item)
    similarity, place = _fit(window, begin, stop, new_text)
    return place if similarity >= REFIT_MIN else None


def _bracket(item: Anchored, text: str) -> tuple[int, int] | None:
    """Sans diff (bloc trop gros) : le passage est ce qui reste entre la fin du contexte d'avant et le début du contexte
    d'après, quand chacun ne se lit qu'une fois (et dans le bon ordre), sans excéder :data:`QUOTE_MAX`."""
    before, after = item.before[-20:], item.after[:20]
    if len(before) < 8 or len(after) < 8:
        return None
    first, second = occurrences(text, before), occurrences(text, after)
    if len(first) != 1 or len(second) != 1:
        return None
    begin, end = first[0] + len(before), second[0]
    return (begin, end) if 0 < end - begin <= QUOTE_MAX and text[begin:end].strip() else None
