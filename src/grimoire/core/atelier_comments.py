"""Magasin des commentaires d'un chantier (Atelier de spec, lot 3 de l'épique #1197, rôles MOA/MOE #1342).

L'humain et l'agent se parlent par des **commentaires ancrés à un passage** d'un bloc du document. Ce module ne
tient que le magasin : un fichier ``comments.json`` dans le dossier du chantier, réécrit en entier et de façon
atomique (:mod:`grimoire.core.fsio`) sous verrou, comme ``suggestions.json`` (lot 2). Il ne lit ni n'écrit jamais
un fichier de phase ; les règles de dépôt et de réancrage sont dans
:mod:`grimoire.tools.workspace_atelier_comments` et :mod:`grimoire.tools.atelier_anchor`.

Un commentaire est un **fil** : le message d'origine, puis ses réponses. États : ``ouvert``, ``resolu``.
L'auteur (``humain`` ou ``agent``) est toujours celui du canal, jamais celui que l'appelant dirait.
"""

from __future__ import annotations

import json
import secrets
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.core.cadrage import PHASES
from grimoire.core.fsio import atomic_write, locked
from grimoire.core.regular_file import open_regular

__all__ = [
    "AUTEURS",
    "ETATS",
    "FILENAME",
    "MAX_ENTRIES",
    "MAX_HISTORY",
    "MAX_OPEN",
    "MAX_REPLIES",
    "QUOTE_MAX",
    "TEXT_MAX",
    "Comment",
    "StoreError",
    "load",
    "locked_store",
    "new_comment",
    "new_reply",
    "read",
    "save",
    "set_aside",
    "store_path",
]

FILENAME = "comments.json"
ETATS: tuple[str, ...] = ("ouvert", "resolu")
AUTEURS: tuple[str, ...] = ("humain", "agent")
TEXT_MAX = 2000
#: Longueur de l'extrait cité (le texte surligné) et du contexte gardé de chaque côté pour le retrouver.
QUOTE_MAX = 300
CONTEXT_MAX = 40
#: Réponses par fil : un fil sert à se parler, pas à tenir un journal.
MAX_REPLIES = 50
#: Rattachements gardés dans l'historique d'un fil (les plus récents) : une trace, pas un journal.
MAX_HISTORY = 20
#: Fils ouverts par chantier : de quoi travailler, pas de quoi noyer le document.
MAX_OPEN = 100
#: Fils au total (résolus compris) : les résolus les plus anciens partent d'abord.
MAX_ENTRIES = 300
_REPLY_KEYS = ("id", "auteur", "date", "texte")


@dataclass(slots=True)
class Comment:
    """Un fil sur un passage : ce qui est cité, où c'était (l'ancre), qui parle, et les réponses."""

    id: str
    phase: str
    block_id: str
    block_fingerprint: str
    quote: str
    start: int
    before: str
    after: str
    auteur: str
    date: str
    texte: str
    etat: str = "ouvert"
    resolu_le: str = ""
    #: Identifiant d'une suggestion (lot 2) que ce commentaire accompagne ; vide sinon.
    suggestion: str = ""
    #: Identité du bloc visé : son rang dans la phase et l'empreinte de ses deux voisins, suivis à chaque réécriture
    #: faite par le cockpit (voir ``atelier_anchor``). Un autre bloc qui porte le même texte n'est jamais ce bloc.
    rank: int = 0
    prev: str = ""
    next: str = ""
    #: Un fil devenu orphelin le reste (écrit au magasin) : il ne se réancre plus de lui-même.
    orphelin: bool = False
    #: L'extrait a bougé dans son bloc depuis le commentaire : la marque reste, elle ne s'efface pas.
    deplace: bool = False
    replies: list[dict[str, str]] = field(default_factory=list)
    #: Les rattachements (« Rattacher ici ») : date, ancienne ancre, nouvelle ancre. Écrit par le serveur seul.
    historique: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def store_path(chantier_dir: Path) -> Path:
    return chantier_dir / FILENAME


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_comment(**fields: Any) -> Comment:
    """Un fil neuf, ``ouvert``, avec son identifiant et sa date."""
    return Comment(id=f"com-{secrets.token_hex(4)}", date=now(), **fields)


def new_reply(auteur: str, texte: str) -> dict[str, str]:
    """Une réponse neuve ; l'auteur est celui du canal."""
    return {"id": f"rep-{secrets.token_hex(4)}", "auteur": auteur, "date": now(), "texte": texte}


class StoreError(ValueError):
    """Le magasin existe mais ne se lit pas (droits, périphérique, lien) : on n'écrase rien."""


def _anchor_ok(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("block_id"), str)
        and isinstance(value.get("quote"), str)
        and isinstance(value.get("start"), int)
        and not isinstance(value.get("start"), bool)
    )


def _history_ok(rows: Any) -> bool:
    return isinstance(rows, list) and all(
        isinstance(r, dict) and isinstance(r.get("date"), str) and _anchor_ok(r.get("avant")) and _anchor_ok(r.get("apres"))
        for r in rows
    )


def _valid(item: Comment, phases: set[str]) -> bool:
    texts = (
        item.id, item.phase, item.block_id, item.block_fingerprint, item.quote, item.before, item.after,
        item.auteur, item.date, item.texte, item.etat, item.resolu_le, item.suggestion, item.prev, item.next,
    )
    replies = item.replies
    return (
        item.phase in phases
        and item.etat in ETATS
        and item.auteur in AUTEURS
        and all(isinstance(v, str) for v in texts)
        and isinstance(item.start, int)
        and not isinstance(item.start, bool)
        and item.start >= 0
        and isinstance(item.rank, int)
        and not isinstance(item.rank, bool)
        and item.rank >= 0
        and isinstance(item.orphelin, bool)
        and isinstance(item.deplace, bool)
        and bool(item.quote.strip())
        and _history_ok(item.historique)
        and isinstance(replies, list)
        and all(
            isinstance(r, dict) and all(isinstance(r.get(k), str) for k in _REPLY_KEYS) and r["auteur"] in AUTEURS
            for r in replies
        )
    )


def read(chantier_dir: Path) -> tuple[list[Comment], int, bytes | None]:
    """``(fils valides, lignes ignorées, octets d'un magasin abîmé)``.

    Une ligne à la phase, à l'état ou à l'auteur inconnus, ou aux champs mal typés, est ignorée (et comptée) : elle
    ne casse ni le document ni le geste. Un fichier qui n'est pas un magasin rend ses octets en troisième valeur :
    l'appelant qui réécrit doit les mettre de côté (:func:`set_aside`).
    """
    path = store_path(chantier_dir)
    try:
        with open_regular(path) as handle:
            raw = handle.read()
    except FileNotFoundError:
        return [], 0, None
    except (OSError, ValueError) as exc:  # lien, FIFO, droits : on n'écrase rien
        raise StoreError(f"magasin des commentaires illisible : {exc}") from exc
    try:
        rows = json.loads(raw.decode("utf-8"))["comments"]
    except (ValueError, KeyError, TypeError):
        return [], 0, raw
    if not isinstance(rows, list):
        return [], 0, raw
    known = set(Comment.__dataclass_fields__)
    phases = {p.id for p in PHASES}
    out: list[Comment] = []
    ignored = 0
    for row in rows:
        try:
            item = Comment(**{k: v for k, v in row.items() if k in known})
        except (TypeError, AttributeError):  # pas un objet, ou champ requis absent
            ignored += 1
            continue
        if not _valid(item, phases):
            ignored += 1
            continue
        out.append(item)
    return out, ignored, None


def load(chantier_dir: Path) -> list[Comment]:
    """Les fils valides du chantier, dans l'ordre de dépôt. Absent ou abîmé : aucun."""
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


def save(chantier_dir: Path, items: list[Comment]) -> None:
    """Réécrit le magasin (atomique). À appeler sous :func:`locked_store`."""
    payload = {"version": 1, "comments": [c.to_dict() for c in items]}
    atomic_write(store_path(chantier_dir), json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def locked_store(chantier_dir: Path) -> AbstractContextManager[None]:
    """Verrou exclusif du magasin du chantier (lecture-modification-écriture)."""
    return locked(store_path(chantier_dir))
