"""Les exigences identifiées du cadrage (W6-07b, ADP-9) : une convention minimale, lue sans rien deviner.

Le cadrage (:mod:`grimoire.core.cadrage`) est de la prose : ``cadrage check`` mesure que les sections sont
remplies, rien ne sait ce qu'elles exigent. Ce module lit **une** convention, rétrocompatible : une exigence
est une ligne qui commence par son identifiant ``REQ-<chiffres>`` — en liste, en titre ou en première colonne
d'un tableau. Tout le reste reste de la prose, valide, et n'est pas lu comme une exigence : un cadrage sans
identifiant ne fait rien échouer, mais ``cadrage plan`` dit qu'il ne voit rien à planifier au lieu de
prétendre couvrir ce qu'il ne lit pas.

Formes reconnues, dans ``04-exigences.md`` et ``05-cahier-des-charges.md`` ::

    - REQ-001 [Must] : l'utilisateur se connecte par lien magique
      - `pytest tests/test_login.py`
      - un test vérifie que le lien expire après 15 minutes
    ### REQ-002 — export CSV (Should)
    | ID      | Exigence         | Priorité | Critère d'acceptation          |
    | REQ-003 | Journal d'audit  | Could    | le schéma JSON du journal est validé |

- les sous-éléments d'une exigence (plus indentés qu'elle, ou sous son titre) sont ses critères ;
- dans une section dont le titre parle de **critères**, ``- REQ-001 : …`` ajoute un critère à REQ-001 ;
- dans une section **priorisation** / **MoSCoW** (pas « Exigences priorisées », qui définit), ``- REQ-001 :
  Must`` fixe sa priorité ;
- dans une section **hors périmètre**, ``- REQ-004 …`` la sort du plan (comme une priorité ``Won't``).

Règles de fusion, déterministes et écrites : une exigence répétée d'un fichier à l'autre est la même (ses
critères s'additionnent) ; si sa formulation diffère, celle du cahier des charges fait foi (c'est le
document opposable). Le même identifiant défini deux fois **dans un même fichier** avec deux formulations
différentes est un conflit : aucune tâche n'en sort tant qu'un humain ne l'a pas tranché.

Module pur : il lit des fichiers, n'écrit rien, n'importe pas le ledger.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grimoire.core.cadrage import CADRAGE_DIR, PLACEHOLDER

__all__ = [
    "REQUIREMENT_FILES",
    "REQ_ID",
    "CadrageRequirements",
    "Requirement",
    "cdc_accepted",
    "parse_requirements",
    "read_requirements",
]

#: L'identifiant d'une exigence : ``REQ-`` puis 1 à 4 chiffres (``REQ-1`` et ``REQ-001`` sont distincts).
REQ_ID = re.compile(r"\bREQ-\d{1,4}\b")
#: Les fichiers lus, dans l'ordre : le dernier fait foi sur la formulation (le cahier des charges).
REQUIREMENT_FILES: tuple[str, ...] = ("04-exigences.md", "05-cahier-des-charges.md")

_ANCHOR = re.compile(
    r"^(?P<indent>\s*)(?P<heading>#{1,6}\s+)?(?:(?:[-*+]|\d+[.)])\s+)?(?:\[[ xX]\]\s+)?"
    r"[*_`]{0,2}(?P<id>REQ-\d{1,4})\b[*_`]{0,2}\s*(?:[:—–.)-]\s*)?(?P<rest>.*)$"
)
_SUB_ITEM = re.compile(r"^(?P<indent>\s*)(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?(?P<text>.+)$")
_MOSCOW = re.compile(r"\b(must|should|could|won'?t|wont)\b(?:\s+have)?", re.IGNORECASE)
_BRACKETED_MOSCOW = re.compile(r"[\[(]\s*(must|should|could|won'?t|wont)(?:\s+have)?\s*[\])]", re.IGNORECASE)
_INLINE_CRITERION = re.compile(r"\s*(?:[—–-]\s*)?crit[èe]res?(?:\s+d['’]acceptation)?\s*:\s*", re.IGNORECASE)
_CRITERION_PREFIX = re.compile(r"^(?:crit[èe]res?(?:\s+d['’]acceptation)?|acceptation|ac)\s*:\s*", re.IGNORECASE)
_CELL_SPLIT = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TABLE_SEPARATOR = re.compile(r"^\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$")
_ACCEPTED_STATUSES = frozenset({"accepted", "accepte", "acceptee", "valide", "validee", "approved"})


def _fold(text: str) -> str:
    """Casse, accents et blancs neutralisés : la clé de comparaison des titres et des sections."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", stripped).strip().casefold()


def _priority_of(text: str) -> str:
    match = _MOSCOW.search(text)
    if match is None:
        return ""
    return match.group(1).lower().replace("'", "")


def _clean(text: str) -> str:
    return text.strip().strip("*_").strip()


@dataclass(slots=True)
class Requirement:
    """Une exigence identifiée : ce que le cadrage dit d'elle, et où."""

    id: str
    title: str = ""
    criteria: list[str] = field(default_factory=list)
    priority: str = ""
    out_of_scope: bool = False
    sources: list[str] = field(default_factory=list)
    #: Formulations concurrentes du même identifiant **dans un même fichier** (conflit à trancher).
    conflicts: list[str] = field(default_factory=list)

    @property
    def planned(self) -> bool:
        """Entre dans le plan : ni hors périmètre, ni ``Won't``."""
        return not self.out_of_scope and self.priority != "wont"

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id, "title": self.title, "priority": self.priority, "criteria": list(self.criteria),
            "sources": list(self.sources),
        }
        if self.out_of_scope:
            data["out_of_scope"] = True
        if self.conflicts:
            data["conflicts"] = list(self.conflicts)
        return data


@dataclass(frozen=True, slots=True)
class CadrageRequirements:
    """Ce que les fichiers d'exigences déclarent : les exigences, et les fichiers effectivement lus."""

    requirements: tuple[Requirement, ...]
    files_read: tuple[str, ...]

    @property
    def identified(self) -> bool:
        return bool(self.requirements)

    def by_id(self) -> dict[str, Requirement]:
        return {r.id: r for r in self.requirements}


class _Collector:
    def __init__(self) -> None:
        self.reqs: dict[str, Requirement] = {}
        #: (fichier, id) -> formulation définie dans ce fichier
        self.defined_in: dict[tuple[str, str], str] = {}

    def get(self, req_id: str) -> Requirement:
        return self.reqs.setdefault(req_id, Requirement(req_id))

    def add_criterion(self, req_id: str, text: str) -> None:
        text = _CRITERION_PREFIX.sub("", _clean(text)).strip()
        if not text or text.startswith(PLACEHOLDER):
            return
        req = self.get(req_id)
        if _fold(text) not in {_fold(c) for c in req.criteria}:
            req.criteria.append(text)

    def define(self, filename: str, line_no: int, req_id: str, rest: str) -> None:
        req = self.get(req_id)
        req.sources.append(f"{filename}:{line_no}")
        rest = rest.strip()
        criterion = ""
        split = _INLINE_CRITERION.search(rest)
        if split is not None:
            rest, criterion = rest[: split.start()], rest[split.end():]
        bracketed = _BRACKETED_MOSCOW.search(rest)
        if bracketed is not None:
            req.priority = req.priority or _priority_of(bracketed.group(0))
            rest = (rest[: bracketed.start()] + rest[bracketed.end():]).strip()
        title = _clean(rest).strip(" :—–-").strip()
        if criterion:
            self.add_criterion(req_id, criterion)
        if not title:
            return
        previous = self.defined_in.get((filename, req_id))
        if previous is not None and _fold(previous) != _fold(title):
            if title not in req.conflicts:
                req.conflicts.extend(t for t in (previous, title) if t not in req.conflicts)
            return
        self.defined_in[(filename, req_id)] = title
        # Le dernier fichier lu (le cahier des charges) fait foi sur la formulation.
        req.title = title


def _section_kind(title: str) -> str:
    folded = _fold(title)
    if "hors" in folded and "perim" in folded:
        return "out_of_scope"
    if "critere" in folded or "acceptation" in folded:
        return "criteria"
    if ("priori" in folded or "moscow" in folded) and "exigence" not in folded:
        return "priority"
    return "definition"


def _cells(line: str) -> list[str]:
    body = line.strip()
    body = body.removeprefix("|").removesuffix("|")
    return [c.strip() for c in body.split("|")]


def _column(headers: list[str], *needles: str) -> int:
    for index, header in enumerate(headers):
        folded = _fold(header)
        if any(n in folded for n in needles):
            return index
    return -1


def _parse_table_row(
    col: _Collector, filename: str, line_no: int, cells: list[str], headers: list[str], section: str
) -> None:
    id_index = next((i for i, c in enumerate(cells) if re.fullmatch(r"[*_`]{0,2}REQ-\d{1,4}[*_`]{0,2}", c.strip())), -1)
    if id_index < 0:
        return
    req_id = _clean(cells[id_index]).strip("`")
    crit_col = _column(headers, "critere", "acceptation", "acceptance")
    prio_col = _column(headers, "priorit", "moscow")
    title_col = _column(headers, "exigence", "titre", "libelle", "intitule", "description", "requirement")
    if title_col in (id_index, crit_col, prio_col):
        title_col = -1
    if title_col < 0:
        title_col = next((i for i in range(len(cells)) if i not in (id_index, crit_col, prio_col)), -1)

    def cell(index: int) -> str:
        return cells[index] if 0 <= index < len(cells) else ""

    if section == "out_of_scope":
        col.get(req_id).out_of_scope = True
        return
    if section == "priority":
        priority = _priority_of(cell(prio_col) or " ".join(cells))
        if priority:
            col.get(req_id).priority = priority
        return
    if section == "criteria":
        for part in _CELL_SPLIT.split(cell(crit_col) if crit_col >= 0 else cell(title_col)):
            col.add_criterion(req_id, part)
        return
    col.define(filename, line_no, req_id, cell(title_col))
    if prio_col >= 0 and (priority := _priority_of(cell(prio_col))):
        col.get(req_id).priority = priority
    if crit_col >= 0:
        for part in _CELL_SPLIT.split(cell(crit_col)):
            col.add_criterion(req_id, part)


def _parse_text(col: _Collector, filename: str, text: str) -> None:
    section = "definition"
    headers: list[str] = []
    in_fence = False
    current: str | None = None  # l'exigence dont on lit les critères en sous-liste
    current_indent = -1
    current_is_heading = False
    lines = text.splitlines()
    for line_no, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.startswith("## ") and not REQ_ID.match(stripped[3:].strip().strip("*_`")):
            section = _section_kind(line[3:])
            current, headers = None, []
            continue
        if stripped.startswith("|"):
            if _TABLE_SEPARATOR.match(stripped):
                continue
            cells = _cells(stripped)
            next_line = lines[line_no].strip() if line_no < len(lines) else ""
            if _TABLE_SEPARATOR.match(next_line):
                headers = cells
                continue
            _parse_table_row(col, filename, line_no, cells, headers, section)
            current = None
            continue
        anchor = _ANCHOR.match(line)
        if anchor is not None:
            req_id, rest = anchor.group("id"), anchor.group("rest")
            if section == "out_of_scope":
                col.get(req_id).out_of_scope = True
                current = None
            elif section == "priority":
                if priority := _priority_of(rest):
                    col.get(req_id).priority = priority
                current = None
            elif section == "criteria":
                col.add_criterion(req_id, rest)
                current = None
            else:
                col.define(filename, line_no, req_id, rest)
                current, current_indent = req_id, len(anchor.group("indent"))
                current_is_heading = bool(anchor.group("heading"))
            continue
        if current is None:
            continue
        sub = _SUB_ITEM.match(line)
        if sub is not None and (current_is_heading or len(sub.group("indent")) > current_indent):
            col.add_criterion(current, sub.group("text"))
            continue
        if stripped.startswith("#") or (stripped and not current_is_heading and not line[:1].isspace()):
            current = None


def parse_requirements(texts: dict[str, str]) -> CadrageRequirements:
    """Les exigences de textes déjà lus (nom de fichier -> contenu), dans l'ordre de :data:`REQUIREMENT_FILES`."""
    col = _Collector()
    read: list[str] = []
    for filename in REQUIREMENT_FILES:
        if filename in texts:
            read.append(filename)
            _parse_text(col, filename, texts[filename])
    reqs = sorted(col.reqs.values(), key=lambda r: (int(r.id.split("-")[1]), r.id))
    # Une exigence seulement citée (critère, priorité) sans définition nulle part reste visible : son titre
    # est vide, ce qui se dit, plutôt que de la faire disparaître.
    return CadrageRequirements(tuple(reqs), tuple(read))


def read_requirements(root: Path) -> CadrageRequirements:
    """Lit ``04-exigences.md`` et ``05-cahier-des-charges.md`` sous ``_grimoire/cadrage/`` (absents : ignorés)."""
    texts: dict[str, str] = {}
    for filename in REQUIREMENT_FILES:
        path = root / CADRAGE_DIR / filename
        if path.is_file():
            texts[filename] = path.read_text(encoding="utf-8", errors="replace")
    return parse_requirements(texts)


def cdc_accepted(root: Path) -> bool:
    """Le cahier des charges est marqué accepté (front matter ``status: accepted|accepté|validé``)."""
    path = root / CADRAGE_DIR / REQUIREMENT_FILES[-1]
    if not path.is_file():
        return False
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines or lines[0].strip() != "---":
        return False
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, _, value = line.partition(":")
        if key.strip() == "status":
            return _fold(value).strip().strip("'\"") in _ACCEPTED_STATUSES
    return False
