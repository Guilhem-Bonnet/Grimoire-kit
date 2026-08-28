#!/usr/bin/env python3
"""Vérifie que le CHANGELOG décrit la version que le dépôt s'apprête à publier.

Troisième garde-fou de release, après la correspondance tag ↔ ``version.txt``
et la couverture du catalogue de digests. Il couvre l'angle mort constaté sur
la 3.34.0 : ``version.txt`` annonçait une version dont aucune section ne
parlait, la section la plus récente décrivait une 3.33.0 jamais taguée, et onze
entrées publiées dans le tag restaient rangées sous « à venir ».

Rien de tout cela ne casse un test ni un import. Ça ne se voit qu'à la lecture
de la note de version, c'est-à-dire trop tard.

Deux propriétés, pas une :

``[Unreleased]`` est vide
    Une entrée qui y reste au moment du tag décrit un changement qui part
    pourtant dans l'artefact. Elle sera annoncée « à venir » dans une version
    où elle est déjà là.

la section la plus récente porte le numéro de ``version.txt``
    Sinon la note publiée décrit une autre version que celle qu'on publie.

Usage::

    scripts/check-changelog-release.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHANGELOG = REPO / "CHANGELOG.md"
VERSION_FILE = REPO / "version.txt"

#: Un titre de section versionnée : ``## [3.34.0] - 2026-08-28``.
SECTION = re.compile(r"(?m)^## \[(?P<version>[^\]]+)\]")


def main() -> int:
    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    text = CHANGELOG.read_text(encoding="utf-8")

    sections = [(m.group("version"), m.start()) for m in SECTION.finditer(text)]
    if not sections:
        print("::error::aucune section versionnée dans CHANGELOG.md", file=sys.stderr)
        return 1

    problems: list[str] = []

    unreleased = [(name, pos) for name, pos in sections if name.lower() == "unreleased"]
    if unreleased:
        _, start = unreleased[0]
        following = [p for _, p in sections if p > start]
        body = text[start:following[0]] if following else text[start:]
        pending = [ln for ln in body.splitlines() if ln.startswith("- ")]
        if pending:
            problems.append(
                f"[Unreleased] porte {len(pending)} entrée(s) : elles partiraient dans "
                f"{version} en étant annoncées « à venir ».\n"
                + "\n".join(f"      {ln[:88]}" for ln in pending[:5])
                + (f"\n      … et {len(pending) - 5} autre(s)" if len(pending) > 5 else "")
            )

    released = [name for name, _ in sections if name.lower() != "unreleased"]
    if not released:
        problems.append("aucune section publiée dans CHANGELOG.md")
    elif released[0] != version:
        problems.append(
            f"la section la plus récente est [{released[0]}], mais version.txt annonce "
            f"{version} — la note publiée décrirait une autre version."
        )

    if problems:
        print(f"::error::CHANGELOG.md ne décrit pas la version {version}", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "\nClore la section : renommer [Unreleased] en "
            f"## [{version}] - <date>, et en ouvrir une nouvelle vide au-dessus.",
            file=sys.stderr,
        )
        return 1

    print(f"CHANGELOG à jour — [{version}] est la section la plus récente, [Unreleased] est vide")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
