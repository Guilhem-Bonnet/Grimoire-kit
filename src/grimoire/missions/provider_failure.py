"""La fin d'un journal de session dit-elle une erreur *transitoire* du fournisseur ? (#1158).

Un worker dont le dernier tour s'arrête sur ``API Error: 500`` (ou 429, ou ``overloaded``)
ne travaille plus : ``claude agents`` ne le dit plus ``busy``, donc son bail n'est plus
renouvelé et la récolte le gare vingt minutes plus tard — alors que l'erreur est passagère
et que la conversation est intacte.

Ce module ne juge qu'une chose : le **dernier tour** du journal de session. Il lit la queue du
fichier (jamais sa totalité) et rend le texte de l'erreur quand ce dernier tour a fini sur une
erreur transitoire ; ``""`` sinon. Une erreur définitive (400, 401, 403…) n'est pas rendue :
reprendre la même conversation n'y changerait rien.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

__all__ = ["TRANSIENT_API_ERROR", "transient_api_error"]

#: Assez pour les derniers tours d'une session ; jamais le journal entier (il peut peser des centaines de Mo).
_TAIL_BYTES = 64 * 1024

#: Une erreur d'API passagère : 5xx, 429, ou une plateforme saturée (529 ``overloaded``).
#: Les codes sont lus collés à un mot d'erreur ou de statut — la sous-chaîne nue ``500``
#: traîne dans des identifiants et des durées (même garde que ``providers.quota``).
TRANSIENT_API_ERROR = re.compile(
    r"api\s*error\s*:\s*(?:429|5\d\d)\b"
    r"|(?:error|status|http|code)\W{0,6}(?:429|5\d\d)\b"
    r"|\b(?:429|5\d\d)\b\W{0,3}(?:error|overloaded|too many)"
    r"|\boverloaded\b",
    re.IGNORECASE,
)

#: Lignes d'hôte qui ne sont pas un tour de conversation : elles ne décident de rien.
_METADATA_TYPES = frozenset({"system", "summary", "file-history-snapshot", "queue-operation", "progress"})


def _text_of(entry: dict[str, object]) -> str:
    """Le texte d'une entrée ``assistant`` : son contenu, qu'il soit une chaîne ou des blocs."""
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(block.get("text", "")) for block in content if isinstance(block, dict) and block.get("type") == "text"
        ).strip()
    return ""


def transient_api_error(path: str | os.PathLike[str] | None) -> str:
    """Le texte de l'erreur transitoire sur laquelle le journal se termine ; ``""`` sinon.

    Seule la dernière entrée de conversation compte, et seulement si l'hôte l'a marquée
    ``isApiErrorMessage`` : un tour qui a repris après l'erreur la lave, et un tour ordinaire qui
    cite un code d'erreur n'en est pas une. Un journal absent, illisible, vide ou en cours d'écriture rend ``""`` (échec fermé :
    rien n'est repris sur une lecture qu'on ne comprend pas).
    """
    if not path:
        return ""
    try:
        with Path(path).open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - _TAIL_BYTES))
            tail = handle.read()
    except OSError:
        return ""
    for chunk in reversed(tail.split(b"\n")):
        if not chunk.strip():
            continue
        try:
            entry = json.loads(chunk.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue  # première ligne d'une queue : coupée au milieu, elle ne prouve rien
        if not isinstance(entry, dict) or entry.get("isSidechain") is True:
            continue
        if entry.get("type") in _METADATA_TYPES:
            continue
        if entry.get("isApiErrorMessage") is not True:
            # Un tour ordinaire (réponse du modèle, résultat d'outil, message humain) n'est pas une erreur, même
            # s'il cite « 500 » : un worker qui résume « corrigé la gestion des HTTP 500 » a fini, il ne doit pas
            # être repris. L'hôte marque ses vraies erreurs d'API (vérifié sur le journal réel du 2026-11-07).
            return ""
        text = _text_of(entry)
        return text if text and TRANSIENT_API_ERROR.search(text) else ""
    return ""
