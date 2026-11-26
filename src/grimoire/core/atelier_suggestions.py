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

from grimoire.core.cadrage import PHASES
from grimoire.core.fsio import atomic_write, locked

__all__ = [
    "AUTHOR_MAX",
    "ETATS",
    "FILENAME",
    "MAX_ENTRIES",
    "MAX_PENDING",
    "MOTIF_MAX",
    "POSTURES",
    "StoreError",
    "Suggestion",
    "load",
    "locked_store",
    "new_suggestion",
    "read",
    "save",
    "set_aside",
    "store_path",
]

FILENAME = "suggestions.json"
ETATS: tuple[str, ...] = ("proposee", "acceptee", "refusee", "perimee")
POSTURES: tuple[str, ...] = ("explorer", "sceptique", "simplificateur")
MOTIF_MAX = 300
AUTHOR_MAX = 60
#: Suggestions en attente par chantier : de quoi travailler, pas de quoi noyer le document.
MAX_PENDING = 100
#: Entrées au total (tranchées et périmées comprises) : les plus anciennes non valides partent d'abord.
MAX_ENTRIES = 300


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
    #: Combien de blocs de la phase avaient ce même texte au dépôt, et l'empreinte de leurs voisins : de quoi ne
    #: pas confondre le bloc visé avec un homonyme quand les rangs se décalent (voir ``workspace_atelier_suggestions``).
    homonymes: int = 1
    voisins: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def store_path(chantier_dir: Path) -> Path:
    return chantier_dir / FILENAME


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_suggestion(**fields: Any) -> Suggestion:
    """Une suggestion neuve, ``proposee``, avec son identifiant et sa date."""
    return Suggestion(id=f"sug-{secrets.token_hex(4)}", date=now(), **fields)


class StoreError(ValueError):
    """Le magasin existe mais ne se lit pas (droits, périphérique) : on n'écrase rien."""


def read(chantier_dir: Path) -> tuple[list[Suggestion], int, bytes | None]:
    """``(suggestions valides, lignes ignorées, octets d'un magasin abîmé)``.

    Une ligne à la phase ou à l'état inconnus, ou aux champs mal typés, est ignorée (et comptée) : elle ne
    casse ni le document ni la décision. Un fichier qui n'est pas un magasin (JSON tronqué, mauvaise forme) rend
    ses octets en troisième valeur : l'appelant qui réécrit doit les mettre de côté (:func:`set_aside`).
    """
    path = store_path(chantier_dir)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return [], 0, None
    except OSError as exc:
        raise StoreError(f"magasin des suggestions illisible : {exc}") from exc
    try:
        rows = json.loads(raw.decode("utf-8"))["suggestions"]
    except (ValueError, KeyError, TypeError):
        return [], 0, raw
    if not isinstance(rows, list):
        return [], 0, raw
    known = set(Suggestion.__dataclass_fields__)
    phases = {p.id for p in PHASES}
    out: list[Suggestion] = []
    ignored = 0
    for row in rows:
        try:
            item = Suggestion(**{k: v for k, v in row.items() if k in known})
        except (TypeError, AttributeError):  # pas un objet, ou champ requis absent
            ignored += 1
            continue
        strings = (v for k, v in item.to_dict().items() if k != "homonymes")
        if (
            item.phase not in phases
            or item.etat not in ETATS
            or not isinstance(item.homonymes, int)
            or not all(isinstance(v, str) for v in strings)
        ):
            ignored += 1
            continue
        out.append(item)
    return out, ignored, None


def load(chantier_dir: Path) -> list[Suggestion]:
    """Les suggestions valides du chantier, dans l'ordre de dépôt. Absent ou abîmé : aucune."""
    try:
        return read(chantier_dir)[0]
    except StoreError:
        return []


def set_aside(chantier_dir: Path, raw: bytes) -> str:
    """Garde les octets d'un magasin abîmé sous un nom daté (atomique) ; rend ce nom."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    name = f"{FILENAME}.abime-{stamp}"
    atomic_write(chantier_dir / name, raw)
    return name


def save(chantier_dir: Path, items: list[Suggestion]) -> None:
    """Réécrit le magasin (atomique). À appeler sous :func:`locked_store`."""
    payload = {"version": 1, "suggestions": [s.to_dict() for s in items]}
    atomic_write(store_path(chantier_dir), json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def locked_store(chantier_dir: Path) -> AbstractContextManager[None]:
    """Verrou exclusif du magasin du chantier (lecture-modification-écriture)."""
    return locked(store_path(chantier_dir))
