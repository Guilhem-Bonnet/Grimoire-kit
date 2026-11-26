"""Magasin des suggestions d'un chantier (Atelier de spec, lot 2 de l'épique #1197, rôles MOA/MOE #1342).

L'agent (MOE) ne modifie jamais un bloc du document : il dépose une **suggestion** que l'humain (MOA) accepte
ou refuse. Ce module ne tient que le magasin : un fichier ``suggestions.json`` dans le dossier du chantier,
réécrit en entier et de façon atomique (:mod:`grimoire.core.fsio`) sous verrou. Il ne lit ni n'écrit jamais
un fichier de phase ; les règles de dépôt et de décision sont dans :mod:`grimoire.tools.workspace_atelier_suggestions`.

États : ``proposee`` (en attente), ``acceptee``, ``refusee``, ``perimee`` (le bloc visé a changé depuis le dépôt).
"""

from __future__ import annotations

import json
import secrets
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.core.fsio import atomic_write, locked

__all__ = [
    "AUTHOR_MAX",
    "ETATS",
    "FILENAME",
    "MAX_PENDING",
    "MOTIF_MAX",
    "POSTURES",
    "Suggestion",
    "load",
    "locked_store",
    "new_suggestion",
    "save",
    "store_path",
]

FILENAME = "suggestions.json"
ETATS: tuple[str, ...] = ("proposee", "acceptee", "refusee", "perimee")
POSTURES: tuple[str, ...] = ("explorer", "sceptique", "simplificateur")
MOTIF_MAX = 300
AUTHOR_MAX = 60
#: Suggestions en attente par chantier : de quoi travailler, pas de quoi noyer le document.
MAX_PENDING = 100


@dataclass(slots=True)
class Suggestion:
    """Une suggestion sur un bloc : ce qui était là (``avant``), ce qui est proposé (``texte``), pourquoi."""

    id: str
    phase: str
    block_id: str
    block_fingerprint: str
    avant: str
    texte: str
    motif: str
    posture: str
    auteur: str
    date: str
    etat: str = "proposee"
    decide_le: str = ""
    decide_motif: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def store_path(chantier_dir: Path) -> Path:
    return chantier_dir / FILENAME


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_suggestion(**fields: Any) -> Suggestion:
    """Une suggestion neuve, ``proposee``, avec son identifiant et sa date."""
    return Suggestion(id=f"sug-{secrets.token_hex(4)}", date=now(), **fields)


def load(chantier_dir: Path) -> list[Suggestion]:
    """Les suggestions du chantier, dans l'ordre de dépôt. Absent ou illisible : aucune (jamais d'erreur)."""
    try:
        data = json.loads(store_path(chantier_dir).read_text(encoding="utf-8"))
        rows = data["suggestions"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    known = set(Suggestion.__dataclass_fields__)
    out: list[Suggestion] = []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and all(isinstance(row.get(k), str) for k in ("id", "block_id", "texte")):
            try:
                out.append(Suggestion(**{k: v for k, v in row.items() if k in known}))
            except TypeError:  # champ requis absent : ligne ignorée, le reste reste lisible
                continue
    return out


def save(chantier_dir: Path, items: list[Suggestion]) -> None:
    """Réécrit le magasin (atomique). À appeler sous :func:`locked_store`."""
    payload = {"version": 1, "suggestions": [s.to_dict() for s in items]}
    atomic_write(store_path(chantier_dir), json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def locked_store(chantier_dir: Path) -> AbstractContextManager[None]:
    """Verrou exclusif du magasin du chantier (lecture-modification-écriture)."""
    return locked(store_path(chantier_dir))
