"""Découpage d'un fichier de phase du cadrage en blocs typés (Atelier de spec, lot 1, #1240).

Un fichier de phase est du Markdown à structure connue (:mod:`grimoire.core.cadrage`) : un en-tête
(front matter, titre), puis des rubriques ``## …`` qui portent des paragraphes, des listes, des questions
ouvertes, des pistes écartées, des décisions et des exigences ``REQ-xxx``.

Le découpage **ne réécrit rien** : chaque bloc garde la plage exacte de texte qu'il occupe, les blancs entre
deux blocs restent dans ``gap`` et ne sont jamais touchés. Recomposer (:func:`recompose`) redonne donc le
fichier à l'octet, et :func:`replace_block` ne change que la plage d'un bloc.

Identifiant de bloc : ``<phase>.<empreinte du texte>[.<n>]``. Stable tant que le bloc n'est pas modifié,
même si un autre bloc l'est ; ``n`` départage deux blocs au texte identique.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "Block",
    "BlockNotFoundError",
    "find_block",
    "fingerprint",
    "recompose",
    "replace_block",
    "split",
]

_FENCE = re.compile(r"^\s{0,3}(```|~~~)")
_HEADING = re.compile(r"^(#{1,6})\s+\S")
_ITEM = re.compile(r"^([-*+]|\d+[.)])\s+")
_REQ = re.compile(r"^[-*+]\s+(?:\*\*)?(REQ-\d+)(?:\*\*)?\s*(?:\[([^\]]+)\])?\s*:?\s*(.*)$", re.DOTALL)
_DECISION = re.compile(r"^(?:[-*+]\s+)?(?:\*\*)?(?:DEC-\d+|D[ée]cision)\b", re.IGNORECASE)
_RAISON = re.compile(r"^(.*?)\s*(?:—|–|:)\s+(.+)$", re.DOTALL)
_QUESTIONS = ("question",)
_ECARTE = ("écart", "ecart")
_LOCKED = frozenset({"entete", "titre", "rubrique"})


class BlockNotFoundError(LookupError):
    """Aucun bloc ne porte cet identifiant : le fichier a changé depuis la lecture."""


@dataclass(frozen=True, slots=True)
class Block:
    """Un bloc : sa plage ``[start, end)`` dans le texte, son type et ce qu'on en tire."""

    id: str
    kind: str
    start: int
    end: int
    text: str
    gap: str = ""
    section: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def editable(self) -> bool:
        return self.kind not in _LOCKED

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind, "text": self.text, "section": self.section,
            "editable": self.editable, **self.data,
        }


def fingerprint(text: str) -> str:
    """Empreinte d'un texte (sha256 des octets UTF-8) : celle qu'une écriture attend."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _lines(text: str) -> list[tuple[int, str]]:
    """``(offset, ligne avec sa fin)``, coupé sur ``\\n`` seul : un ``\\r`` reste dans la ligne."""
    out: list[tuple[int, str]] = []
    pos = 0
    for raw in text.split("\n"):
        end = pos + len(raw) + 1
        out.append((pos, text[pos:end]))
        pos = end
    if out and out[-1][1] == "":
        out.pop()
    return out


def _bare(line: str) -> str:
    return line.rstrip("\r\n")


def _is_blank(line: str) -> bool:
    return not _bare(line).strip()


def _indented(line: str) -> bool:
    return bool(_bare(line)) and _bare(line)[0] in " \t"


def _front_matter_end(lines: list[tuple[int, str]]) -> int:
    """Index de la première ligne après le front matter, ou 0 s'il n'y en a pas."""
    if not lines or _bare(lines[0][1]).lstrip("﻿").strip() != "---":
        return 0
    for index in range(1, len(lines)):
        if _bare(lines[index][1]).strip() == "---":
            return index + 1
    return 0


def _spans(lines: list[tuple[int, str]]) -> list[tuple[int, int, str]]:
    """``(première ligne, dernière ligne exclue, genre brut)`` : en-tête, rubriques, paragraphes, listes."""
    spans: list[tuple[int, int, str]] = []
    index = _front_matter_end(lines)
    if index:
        spans.append((0, index, "entete"))
    total = len(lines)
    while index < total:
        line = _bare(lines[index][1])
        if _is_blank(lines[index][1]):
            index += 1
            continue
        start = index
        if _HEADING.match(line):
            index += 1
            level = len(_HEADING.match(line).group(1))  # type: ignore[union-attr]
            spans.append((start, index, "titre" if level == 1 else "rubrique"))
            continue
        fence = _FENCE.match(line)
        if fence:
            index += 1
            while index < total and not _bare(lines[index][1]).lstrip().startswith(fence.group(1)):
                index += 1
            index = min(index + 1, total)
            spans.append((start, index, "paragraphe"))
            continue
        if _ITEM.match(line):
            index += 1
            while index < total and not _is_blank(lines[index][1]):
                if _HEADING.match(_bare(lines[index][1])) or _FENCE.match(_bare(lines[index][1])):
                    break
                index += 1
            spans.append((start, index, "liste"))
            continue
        index += 1
        while index < total and not _is_blank(lines[index][1]):
            current = _bare(lines[index][1])
            if _HEADING.match(current) or _FENCE.match(current) or _ITEM.match(current):
                break
            index += 1
        spans.append((start, index, "paragraphe"))
    return spans


def _items(lines: list[tuple[int, str]], first: int, last: int) -> list[tuple[int, int]]:
    """Les éléments de premier niveau d'une liste : une ligne non indentée qui ouvre une puce, et ses suites."""
    starts = [i for i in range(first, last) if not _indented(lines[i][1]) and _ITEM.match(_bare(lines[i][1]))]
    if not starts or starts[0] != first:
        return [(first, last)]
    return [(s, starts[k + 1] if k + 1 < len(starts) else last) for k, s in enumerate(starts)]


def _item_text(raw: str) -> str:
    return _ITEM.sub("", raw.strip(), count=1)


def _requirement(raw: str) -> dict[str, Any] | None:
    match = _REQ.match(raw.strip())
    if not match:
        return None
    head = match.group(3).split("\n", 1)[0].strip()
    criteres = [
        _item_text(line) for line in raw.split("\n")[1:] if line.strip() and _ITEM.match(line.strip())
    ]
    return {"req": match.group(1), "priorite": (match.group(2) or "").strip(), "titre": head, "criteres": criteres}


def _discarded(raw: str) -> dict[str, Any]:
    body = _item_text(raw.split("\n", 1)[0]).replace("~~", "").strip()
    match = _RAISON.match(body)
    return {"piste": (match.group(1) if match else body).strip(), "raison": (match.group(2) if match else "").strip()}


def _kind_of(raw_kind: str, section: str, text: str) -> tuple[str, dict[str, Any]]:
    """Le type d'un élément selon la rubrique où il se trouve et ce qu'il dit."""
    if raw_kind != "item":
        return raw_kind, {}
    low = section.lower()
    requirement = _requirement(text)
    if requirement is not None:
        return "exigence", requirement
    if _DECISION.match(text.strip()):
        return "decision", {}
    if any(word in low for word in _QUESTIONS):
        return "question", {}
    if any(word in low for word in _ECARTE):
        return "ecartee", _discarded(text)
    return "liste", {}


def _build(phase: str, text: str) -> list[Block]:
    lines = _lines(text)
    pieces: list[tuple[int, int, str, str]] = []  # début, fin (en lignes), genre brut, rubrique
    section = ""
    for first, last, kind in _spans(lines):
        if kind == "rubrique":
            section = _bare(lines[first][1]).lstrip("#").strip()
            pieces.append((first, last, kind, section))
        elif kind == "liste":
            items = _items(lines, first, last)
            texts = ["".join(line for _, line in lines[a:b]).rstrip() for a, b in items]
            # Une liste ordinaire reste UN bloc ; elle ne se découpe en éléments que si l'un d'eux a un type propre.
            if any(_kind_of("item", section, t)[0] != "liste" for t in texts):
                pieces.extend((a, b, "item", section) for a, b in items)
            else:
                pieces.append((first, last, "liste", section))
        else:
            pieces.append((first, last, kind, section))
    blocks: list[Block] = []
    seen: dict[str, int] = {}
    cursor = 0
    for first, last, raw_kind, sect in pieces:
        start = lines[first][0]
        end = lines[last - 1][0] + len(lines[last - 1][1])
        # Les blancs de fin de bloc (lignes vides, fin de ligne) restent hors plage : ils appartiennent à l'écart.
        body = text[start:end]
        trimmed = body.rstrip()
        end = start + len(trimmed)
        chunk = text[start:end]
        kind, data = _kind_of(raw_kind, sect, chunk)
        if kind == "paragraphe" and chunk.lstrip().startswith(">") and "À compléter" in chunk:
            data = {**data, "placeholder": True}
        digest = fingerprint(chunk)[:10]
        nth = seen.get(digest, 0)
        seen[digest] = nth + 1
        block_id = f"{phase}.{digest}" + (f".{nth}" if nth else "")
        blocks.append(Block(block_id, kind, start, end, chunk, text[cursor:start], sect, data))
        cursor = end
    return blocks


def split(phase: str, text: str) -> list[Block]:
    """Les blocs d'un fichier de phase, dans l'ordre. Un texte vide n'a aucun bloc."""
    return _build(phase, text)


def recompose(text: str, blocks: list[Block]) -> str:
    """Le fichier, reconstruit depuis ses blocs et les blancs qui les séparent (le reste de *text* est la queue)."""
    tail = text[blocks[-1].end:] if blocks else text
    return "".join(b.gap + b.text for b in blocks) + tail


def find_block(blocks: list[Block], block_id: str) -> Block:
    for block in blocks:
        if block.id == block_id:
            return block
    raise BlockNotFoundError(block_id)


def replace_block(text: str, block: Block, new_text: str) -> str:
    """*text* dont seule la plage de *block* est remplacée par *new_text*.

    Les fins de ligne du fichier sont respectées : un fichier en CRLF reçoit un bloc en CRLF.
    """
    if "\r\n" in text:
        new_text = new_text.replace("\r\n", "\n").replace("\n", "\r\n")
    return text[:block.start] + new_text.rstrip() + text[block.end:]
