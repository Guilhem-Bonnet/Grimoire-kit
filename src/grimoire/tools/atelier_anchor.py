"""Ancres des commentaires de l'Atelier de spec (lot 3 de l'épique #1197) : retrouver un passage après modification.

Un commentaire porte le bloc visé (identifiant et empreinte), l'extrait cité, sa position dans le bloc et le contexte
de chaque côté. Le document change sous le commentaire (l'humain réécrit, l'agent propose, une suggestion est
acceptée) ; :func:`locate` dit où le commentaire vit **aujourd'hui**, sans rien écrire.

Règle de réancrage, dans cet ordre, la première qui conclut gagne :

1. **Le bloc visé n'a pas changé** (un seul bloc de la phase a son empreinte, et il n'y avait pas d'homonyme à
   l'ancrage) : le commentaire reste attaché à ce bloc, que les blocs voisins aient changé ou non. État ``ancre``.
2. **Le bloc a des homonymes** (même texte ailleurs dans la phase) : seul celui dont les voisins immédiats sont ceux
   de l'ancrage convient, et il doit être unique ; sinon on ne sait pas lequel c'était : orphelin.
3. **Le bloc visé a changé** : l'extrait est cherché mot pour mot dans les blocs modifiables de la phase. Il est
   retrouvé (``retrouve``) quand **une seule** occurrence a le même contexte avant ET après (au bord du bloc, le
   contexte vide veut dire « au bord »), ou, à défaut de contexte commun, quand l'extrait (24 caractères au moins) n'a
   qu'une seule occurrence dans toute la phase.
4. **Sinon** (extrait disparu ou occurrences en concurrence) : ``orphelin``. Jamais de rapprochement par
   approximation : pas de distance d'édition, pas de « le plus ressemblant ».

Un orphelin garde son extrait d'origine et reste visible : ``near`` nomme le bloc encore présent qui a l'empreinte
d'origine quand l'orphelin vient d'un doute entre homonymes ; ``""`` sinon (le document l'affiche en fin de phase).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from grimoire.core.atelier_comments import CONTEXT_MAX
from grimoire.tools import atelier_blocs

#: Extrait minimal pour qu'une occurrence unique suffise à le retrouver quand son contexte a changé.
UNIQUE_MIN = 24


class Anchored(Protocol):
    """Ce que :func:`locate` lit d'un commentaire."""

    block_fingerprint: str
    quote: str
    start: int
    before: str
    after: str
    homonymes: int
    voisins: str


@dataclass(frozen=True, slots=True)
class Anchor:
    """Où vit le commentaire : ``ancre`` (bloc inchangé), ``retrouve`` (extrait retrouvé) ou ``orphelin``."""

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


def around(blocks: Sequence[atelier_blocs.Block], index: int) -> tuple[int, str]:
    """Ce qui distingue un bloc de ses homonymes : leur nombre et l'empreinte des voisins immédiats."""
    same = sum(1 for b in blocks if b.text == blocks[index].text)
    before = blocks[index - 1].text if index > 0 else ""
    after = blocks[index + 1].text if index + 1 < len(blocks) else ""
    return same, atelier_blocs.fingerprint(before + "\0" + after)


def occurrences(text: str, needle: str) -> list[int]:
    found: list[int] = []
    at = text.find(needle)
    while at >= 0:
        found.append(at)
        at = text.find(needle, at + len(needle))
    return found


def _same_context(text: str, at: int, item: Anchored) -> bool:
    """Le contexte d'une occurrence est celui de l'ancrage, sans tolérance (le bord compte comme un contexte)."""
    end = at + len(item.quote)
    before, after = context(text, at, end)
    return before == item.before and after == item.after


def locate(blocks: Sequence[atelier_blocs.Block], item: Anchored) -> Anchor:
    """Où le commentaire ``item`` vit dans ``blocks`` (les blocs de sa phase, tels qu'aujourd'hui). N'écrit rien."""
    same = [i for i, b in enumerate(blocks) if atelier_blocs.fingerprint(b.text) == item.block_fingerprint]
    if same:
        kept = same
        if len(same) > 1 or item.homonymes > 1:
            kept = [i for i in same if around(blocks, i)[1] == item.voisins]
            if len(kept) != 1:
                return Anchor("orphelin", near=blocks[same[0]].id)
        block = blocks[kept[0]]
        if block.text[item.start : item.start + len(item.quote)] == item.quote:
            return Anchor("ancre", block.id, item.start, item.start + len(item.quote))
        return Anchor("orphelin", near=block.id)  # position qui ne dit plus l'extrait : on ne devine pas
    seen = [(b, at) for b in blocks if b.editable for at in occurrences(b.text, item.quote)]
    exact = [(b, at) for b, at in seen if _same_context(b.text, at, item)]
    pick = exact or (seen if len(item.quote) >= UNIQUE_MIN else [])
    if len(pick) == 1:
        block, at = pick[0]
        return Anchor("retrouve", block.id, at, at + len(item.quote))
    return Anchor("orphelin")
