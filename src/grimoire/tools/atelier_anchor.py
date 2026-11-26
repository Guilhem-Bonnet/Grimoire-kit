"""Ancres des commentaires de l'Atelier de spec (lot 3 de l'épique #1197) : retrouver un passage après modification.

Un commentaire porte le bloc visé (identifiant, empreinte, **rang dans la phase et empreinte de ses deux voisins**),
l'extrait cité, sa position dans le bloc et le contexte de chaque côté. Le document change sous le commentaire ;
:func:`locate` dit où le commentaire vit **aujourd'hui**, sans rien écrire.

Principe : **un fil ne quitte jamais son bloc d'origine.** Un autre bloc qui porte le même texte ou la même empreinte
n'est pas ce bloc ; au moindre doute, le fil est orphelin. Règle, la première qui conclut gagne :

0. **Un orphelin le reste.** Un fil marqué orphelin au magasin (:attr:`Comment.orphelin`) ne se réancre plus de lui-même.
1. **Le bloc visé n'a pas changé** : un bloc de la phase a son empreinte *et* c'est bien sa place (même rang, ou au moins
   un voisin identique : un bloc inséré ou supprimé avant lui ne le détache pas). Il doit être le seul dans ce cas, ou
   le seul au même rang. Les blocs voisins peuvent changer sans que le fil bouge. Un bloc de même texte mais d'une
   autre place (rang et voisins différents) ne compte pas.
2. **Le bloc visé a été modifié sur place** : c'est le bloc compris entre les deux mêmes voisins (empreintes des deux
   identiques, et un seul bloc dans ce cas). L'extrait n'y est cherché que **dans ce bloc**, mot pour mot : retrouvé
   (``retrouve``) s'il y figure une seule fois, ou, s'il y figure plusieurs fois, si une seule occurrence a exactement le
   même contexte avant et après ; sinon orphelin.
3. **Sinon** : ``orphelin``. Pas de recherche dans les autres blocs, pas de distance d'édition, pas d'unicité « dans la
   phase ».

Un fil « retrouvé » (:attr:`Comment.deplace`) le reste : la marque est écrite au magasin et ne s'efface pas.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from grimoire.core.atelier_comments import CONTEXT_MAX
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

    def to_dict(self) -> dict[str, Any]:
        return {"state": self.state, "block_id": self.block_id, "start": self.start, "end": self.end, "near": self.near}


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
    placed = [i for i in same if _in_place(blocks, i, item)]
    if len(placed) > 1:
        placed = [i for i in placed if i == item.rank]
        if len(placed) != 1:
            return Anchor("orphelin", near=blocks[same[0]].id)  # le doute entre des blocs identiques
    if len(placed) == 1:
        block = blocks[placed[0]]
        if block.text[item.start : item.start + len(item.quote)] == item.quote:
            return Anchor(held, block.id, item.start, item.start + len(item.quote))
        return Anchor("orphelin", near=block.id)  # position qui ne dit plus l'extrait : on ne devine pas
    slot = [
        i for i, b in enumerate(blocks)
        if b.editable and _fp(b) != item.block_fingerprint and around(blocks, i)[1:] == (item.prev, item.next)
    ]
    if len(slot) != 1:
        return Anchor("orphelin")
    block = blocks[slot[0]]
    found = occurrences(block.text, item.quote)
    if len(found) > 1:
        found = [at for at in found if context(block.text, at, at + len(item.quote)) == (item.before, item.after)]
    if len(found) == 1:
        return Anchor("retrouve", block.id, found[0], found[0] + len(item.quote))
    return Anchor("orphelin")
