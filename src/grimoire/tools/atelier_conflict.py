"""Contrôle de concurrence PAR BLOC de l'Atelier de spec (épique #1197).

Avant, une édition de bloc comparait l'empreinte du fichier entier : qu'un agent touche un autre bloc pendant la
saisie, et la saisie était refusée. Ici le client envoie l'empreinte du bloc tel qu'il l'a lu (``fp``) et celles de
ses deux voisins immédiats (``prev``, ``next`` ; ``""`` au bord du fichier). Le serveur redécoupe le fichier
courant et retrouve le bloc :

1. par son empreinte : un seul bloc au texte identique, c'est lui, inchangé depuis la lecture ;
2. si plusieurs blocs jumeaux portent cette empreinte, les voisins les départagent ; sinon c'est ambigu ;
3. si aucun ne la porte, le bloc a changé ou disparu : on cherche la place entre ses deux voisins, qui doivent
   tous deux se retrouver et désigner une seule place. Rien d'autre n'est deviné.

Le module ne lit ni n'écrit aucun fichier : il décide, l'appelant écrit (:func:`grimoire.tools.workspace_atelier.edit_block`).
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass

from grimoire.tools.atelier_blocs import Block, fingerprint

__all__ = ["Located", "locate", "merge3", "neighbour_fields", "propose_merge"]

SAME = "same"  # le bloc est là, inchangé : on peut écrire
CHANGED = "changed"  # le bloc est là mais son texte a changé depuis la lecture
AMBIGUOUS = "ambiguous"  # plusieurs candidats : refus, pas de devinette
MISSING = "missing"  # plus de place identifiable pour ce bloc
#: Plafond de lignes par version pour proposer une fusion (``difflib`` est cubique sur des lignes très courtes).
MAX_MERGE_LINES = 200


@dataclass(frozen=True, slots=True)
class Located:
    """Résultat de :func:`locate` : ``kind`` et, sauf ``ambiguous``/``missing``, l'index du bloc dans le fichier."""

    kind: str
    index: int = -1


def _fps(blocks: list[Block]) -> list[str]:
    return [fingerprint(b.text) for b in blocks]


def neighbour_fields(blocks: list[Block]) -> list[dict[str, str | int]]:
    """Pour chaque bloc, son empreinte et celles de ses voisins : ce que le client renvoie à l'écriture."""
    fps = _fps(blocks)
    return [
        {
            "fp": fp,
            "prev": fps[i - 1] if i else "",
            "next": fps[i + 1] if i + 1 < len(fps) else "",
            "twins": fps.count(fp),  # combien de blocs portent cette empreinte à la lecture (1 = unique)
        }
        for i, fp in enumerate(fps)
    ]


def _edge_matches(fps: list[str], index: int, prev: str, nxt: str) -> bool:
    before = fps[index - 1] if index else ""
    after = fps[index + 1] if index + 1 < len(fps) else ""
    return before == prev and after == nxt


def _places(fps: list[str], prev: str, nxt: str) -> set[int]:
    """Les places où un bloc a pour voisins ``prev`` avant et ``nxt`` après (``""`` : le bord du fichier)."""
    after_prev = {0} if prev == "" else {i + 1 for i, value in enumerate(fps) if value == prev}
    before_next = {len(fps) - 1} if nxt == "" else {i - 1 for i, value in enumerate(fps) if value == nxt}
    return {i for i in after_prev & before_next if 0 <= i < len(fps)}


def locate(blocks: list[Block], fp: str, prev: str, nxt: str, twins: int | None = None) -> Located:
    """Retrouve dans *blocks* le bloc lu avec l'empreinte *fp* entre les voisins *prev* et *nxt*.

    *twins* : combien de blocs portaient cette empreinte à la lecture (``None`` : inconnu, traité avec prudence).
    Un candidat unique par empreinte n'est le bon que si l'empreinte était déjà unique à la lecture (le texte
    identique ne peut alors venir d'ailleurs) ou si ses voisins concordent ; sinon un jumeau a été modifié ou
    retiré et le survivant n'est peut-être pas celui qu'on éditait.
    """
    fps = _fps(blocks)
    same = [i for i, value in enumerate(fps) if value == fp]
    if len(same) == 1:
        if twins == 1 or _edge_matches(fps, same[0], prev, nxt):
            return Located(SAME, same[0])
        places = _places(fps, prev, nxt)
        if len(places) == 1:  # le jumeau qu'on éditait est là, modifié : c'est lui le conflit
            return Located(CHANGED, next(iter(places)))
        return Located(AMBIGUOUS)
    if len(same) > 1:
        narrowed = [i for i in same if _edge_matches(fps, i, prev, nxt)]
        return Located(SAME, narrowed[0]) if len(narrowed) == 1 else Located(AMBIGUOUS)
    # Le texte a changé (ou le bloc a disparu) : la place entre les deux voisins, si elle est unique.
    places = _places(fps, prev, nxt)
    if len(places) == 1:
        return Located(CHANGED, next(iter(places)))
    return Located(AMBIGUOUS if len(places) > 1 else MISSING)


def propose_merge(base: str, mine: str, theirs: str) -> str | None:
    """:func:`merge3`, sans proposition quand elle ne ferait rien de plus que la version courante."""
    merged = merge3(base, mine, theirs)
    return None if merged is None or fingerprint(merged) == fingerprint(theirs) else merged


def _regions(base: list[str], other: list[str]) -> list[tuple[int, int, list[str]]]:
    """Les plages de *base* que *other* remplace : ``(début, fin exclue, lignes de remplacement)``."""
    matcher = difflib.SequenceMatcher(None, base, other, autojunk=False)
    return [(i1, i2, other[j1:j2]) for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag != "equal"]


def merge3(base: str, mine: str, theirs: str) -> str | None:
    """Fusion par lignes à trois voies, ou ``None`` dès que ce n'est pas trivial.

    Trivial : les plages que ``mine`` et ``theirs`` changent par rapport à ``base`` ne se touchent pas (deux
    insertions au même point, ou deux plages adjacentes, comptent comme un chevauchement). Deux changements
    identiques ne valent qu'une fois. Les fins de ligne sont ramenées à ``\\n``.
    """
    lines = [t.replace("\r\n", "\n").split("\n") for t in (base, mine, theirs)]
    if any(len(part) > MAX_MERGE_LINES for part in lines):
        return None  # le diff est cubique dans le pire cas : au-delà, pas de proposition de fusion
    base_l, mine_l, theirs_l = lines
    ours = _regions(base_l, mine_l)
    others = _regions(base_l, theirs_l)
    merged: list[tuple[int, int, list[str]]] = list(ours)
    for region in others:
        if region in ours:
            continue
        if any(region[0] <= o[1] and o[0] <= region[1] for o in ours):
            return None
        merged.append(region)
    out: list[str] = []
    cursor = 0
    for start, end, replacement in sorted(merged, key=lambda r: (r[0], r[1])):
        out.extend(base_l[cursor:start])
        out.extend(replacement)
        cursor = end
    out.extend(base_l[cursor:])
    return "\n".join(out)
