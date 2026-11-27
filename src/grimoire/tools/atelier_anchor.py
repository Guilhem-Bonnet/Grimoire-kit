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
   suit le bloc entier (``retrouve``) : le passage a changé, pas sa place.
3. **Le bloc visé a été réécrit et ses voisins aussi** (aucun bloc de la phase n'a son empreinte) : meilleure
   correspondance de texte entre l'ancre (contexte et extrait) et les blocs, au-dessus d'un seuil prudent et **unique**
   (le suivant est nettement plus bas), sinon orphelin.
4. **Sinon** : ``orphelin``. Jamais de ré-ancrage sur un bloc ambigu ni sur un homonyme.

Un fil « retrouvé » (:attr:`Comment.deplace`) le reste : la marque est écrite au magasin et ne s'efface pas.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
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
        return _whole(block, item)  # réécrit sur place : le fil suit le bloc (un extrait ambigu, lui, reste orphelin)
    return Anchor("orphelin")


def _whole(block: atelier_blocs.Block, item: Anchored) -> Anchor:
    """Ce que suit un fil dont l'extrait a été réécrit : le passage qui correspond à l'ancre (extrait et contexte de
    chaque côté, comparés au bloc), à défaut le bloc entier (borné à :data:`QUOTE_MAX`)."""
    window = item.before + item.quote + item.after
    begin, end = follow(window, block.text, len(item.before), len(item.before) + len(item.quote))
    return Anchor("retrouve", block.id, begin, end, quote=block.text[begin:end])


#: Similarité minimale (part de l'ancre retrouvée dans le bloc) et écart au second pour suivre un bloc réécrit.
SIMILAR_MIN = 0.8
SIMILAR_GAP = 0.15
_SIMILAR_TEXT_MAX = 3000


def _similarity(window: str, text: str) -> float:
    """Part de ``window`` que l'on retrouve (par blocs communs) dans ``text`` ; 0 si l'un est vide."""
    text = text[:_SIMILAR_TEXT_MAX]
    if not window or not text:
        return 0.0
    if SequenceMatcher(None, window, text).quick_ratio() * (len(window) + len(text)) / 2 < SIMILAR_MIN * len(window):
        return 0.0  # même pas assez de lettres communes : inutile de comparer
    matcher = SequenceMatcher(None, window, text, autojunk=False)
    return sum(m.size for m in matcher.get_matching_blocks()) / len(window)


def _by_similarity(blocks: Sequence[atelier_blocs.Block], item: Anchored) -> Anchor:
    """Le bloc qui ressemble le plus à l'ancre (contexte et extrait), s'il est seul à dépasser le seuil."""
    window = item.before + item.quote + item.after
    scored = sorted(((_similarity(window, b.text), i) for i, b in enumerate(blocks) if b.editable), reverse=True)
    if not scored or scored[0][0] < SIMILAR_MIN or (len(scored) > 1 and scored[1][0] > scored[0][0] - SIMILAR_GAP):
        return Anchor("orphelin")
    block = blocks[scored[0][1]]
    found = occurrences(block.text, item.quote)
    if len(found) == 1:
        return Anchor("retrouve", block.id, found[0], found[0] + len(item.quote))
    return _whole(block, item)


def follow(old: str, new: str, start: int, end: int) -> tuple[int, int]:
    """Où ``old[start:end]`` se lit dans ``new`` (le même bloc, réécrit) : la plage qui lui correspond, ou le texte
    entier de ``new`` (borné) quand rien ne subsiste. Appelé par la route d'édition, qui connaît les deux versions."""
    whole = (0, min(len(new), QUOTE_MAX))
    if not old or not new or len(old) > _FOLLOW_MAX or len(new) > _FOLLOW_MAX:
        return whole
    if start == 0 and old[:QUOTE_MAX] == old[start:end]:
        return whole  # le fil portait sur le bloc entier : il porte sur le bloc entier
    first = last = -1
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if i1 <= start < i2:
            first = j1 + (start - i1) if tag == "equal" else j1
        if i1 < end <= i2:
            last = j1 + (end - i1) if tag == "equal" else j2
    if first < 0 or last <= first or not new[first:last].strip():
        return whole
    return first, min(last, first + QUOTE_MAX)


_FOLLOW_MAX = 20_000
