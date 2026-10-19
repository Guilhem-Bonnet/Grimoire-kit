#!/usr/bin/env python3
"""Garde de documentation — chaque fichier de ``docs/`` déclare son public.

``docs/`` mélangeait des ADR, des plans datés, des références et des guides : un
lecteur ne savait pas si un fichier décrivait l'état courant ou une décision
passée. Chaque ``docs/**/*.md`` porte donc un front-matter ::

    ---
    audience: utilisateur
    ---

``utilisateur``
    celui qui installe et utilise le kit ;
``contributeur``
    celui qui modifie le kit (protocoles internes, bancs, développement) ;
``référence``
    la description exacte et à jour d'une surface (CLI, config, API, hôtes) ;
``décision``
    un texte daté qui consigne un choix, un plan, un constat ou une veille.
    Il décrit un instant : les commandes qu'il cite peuvent avoir changé, et
    ``check-doc-commands.py`` ne les juge pas.

Le garde refuse un fichier sans front-matter, sans ``audience`` ou avec une
valeur hors de ces quatre-là. Il lit les fichiers, il n'en infère rien : un
fichier non classé est un fichier que personne n'a décidé de classer.

Usage::

    python scripts/check-doc-audience.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
AUDIENCES = ("utilisateur", "contributeur", "référence", "décision")

_FRONT_MATTER = re.compile(r"\A---[ \t]*\r?\n(?P<body>.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_AUDIENCE_LINE = re.compile(r"(?m)^audience:[ \t]*(?P<value>[^\r\n#]*?)[ \t]*(?:#.*)?$")


def audience_of(text: str) -> tuple[str | None, str | None]:
    """``(audience, problème)`` d'un document : exactement l'un des deux est ``None``."""
    match = _FRONT_MATTER.match(text)
    if not match:
        return None, "pas de front-matter (--- ... ---) en tête de fichier"
    line = _AUDIENCE_LINE.search(match.group("body"))
    if not line:
        return None, "front-matter sans clé `audience`"
    value = line.group("value").strip().strip("'\"")
    if value not in AUDIENCES:
        return None, f"audience `{value}` hors de {{{', '.join(AUDIENCES)}}}"
    return value, None


def problems(docs: Path = DOCS) -> list[str]:
    found: list[str] = []
    files = sorted(docs.rglob("*.md"))
    if not files:
        return [f"aucun fichier .md sous {docs} : garde sans objet, donc non vérifiée"]
    for path in files:
        _, problem = audience_of(path.read_text(encoding="utf-8"))
        if problem:
            found.append(f"{path.relative_to(docs.parent).as_posix()} : {problem}")
    return found


def main() -> int:
    found = problems()
    if found:
        print(f"{len(found)} fichier(s) de docs/ sans audience valide :", file=sys.stderr)
        for line in found:
            print(f"  - {line}", file=sys.stderr)
        print(f"\nAjouter `audience: <{'|'.join(AUDIENCES)}>` au front-matter.", file=sys.stderr)
        return 1
    print(f"ok — {len(list(DOCS.rglob('*.md')))} fichiers de docs/ classés par audience")
    return 0


if __name__ == "__main__":
    sys.exit(main())
