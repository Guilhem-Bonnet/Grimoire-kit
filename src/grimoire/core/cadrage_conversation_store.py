"""Le fichier de conversation d'un chantier : lecture validée, écriture atomique sous verrou (épique #1477, lot A).

``conversation.json`` vit dans le dossier du chantier, à côté de ``entretien.json`` et ``comments.json``. Comme eux
il est **versionnable** (les verrous ``.conversation.json.lock`` sont déjà couverts par le bloc ``.gitignore`` du kit,
le fichier lui-même reste suivi) : une conversation est une trace de décision, relue avec le cadrage.

Le dossier est fourni par l'appelant, déjà retrouvé dans la liste réelle des chantiers (jamais construit depuis un
nom reçu). Ici : ``lstat`` + ``S_ISREG`` avant toute lecture (lien, FIFO, périphérique refusés), taille bornée, JSON
puis schéma validés (:meth:`Conversation.from_dict`). Un fichier abîmé n'est jamais écrasé : l'appelant le reçoit en
erreur et la personne le corrige ou le retire, comme ``entretien.json``.
"""

from __future__ import annotations

import json
import os
from contextlib import AbstractContextManager
from pathlib import Path

from grimoire.core.cadrage_conversation import MAX_FILE_BYTES, Conversation, ConversationError
from grimoire.core.fsio import atomic_write, locked
from grimoire.core.regular_file import open_regular

__all__ = ["FILENAME", "locked_store", "read", "save", "store_path"]

FILENAME = "conversation.json"


def store_path(chantier_dir: Path) -> Path:
    return chantier_dir / FILENAME


def read(chantier_dir: Path, chantier: str | None = None) -> Conversation | None:
    """La conversation enregistrée ; ``None`` s'il n'y en a pas. Un fichier hors cadre lève :class:`ConversationError`."""
    path = store_path(chantier_dir)
    try:
        with open_regular(path) as handle:
            if os.fstat(handle.fileno()).st_size > MAX_FILE_BYTES:
                raise ConversationError(f"{FILENAME} dépasse {MAX_FILE_BYTES // 1024} Kio : corrige-le ou retire-le")
            raw = handle.read()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ConversationError(f"{FILENAME} illisible ({exc.__class__.__name__}) : corrige-le ou retire-le") from exc
    except ValueError as exc:
        if isinstance(exc, ConversationError):
            raise
        raise ConversationError(f"{FILENAME} : {exc}") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError) as exc:  # JSON invalide, UTF-8 invalide, imbrication excessive
        raise ConversationError(f"{FILENAME} illisible ({exc.__class__.__name__}) : corrige-le ou retire-le") from exc
    return Conversation.from_dict(data, chantier=chantier)


def save(chantier_dir: Path, conv: Conversation) -> None:
    """Réécrit le fichier en entier, de façon atomique. À appeler sous :func:`locked_store`."""
    payload = json.dumps(conv.to_dict(), ensure_ascii=False, indent=2) + "\n"
    if len(payload.encode("utf-8")) > MAX_FILE_BYTES:
        raise ConversationError("conversation trop volumineuse : classe-la et reprends dans une nouvelle")
    atomic_write(store_path(chantier_dir), payload)


def locked_store(chantier_dir: Path) -> AbstractContextManager[None]:
    """Verrou exclusif du fichier de conversation (lecture-modification-écriture)."""
    return locked(store_path(chantier_dir))
