#!/usr/bin/env python3
"""Extrait du CHANGELOG le corps de la section d'une version.

C'est le texte que la release GitHub publie, tel quel : la note de version
est celle que ``check-changelog-release.py`` vient de valider, pas un
``git log`` ni des notes régénérées. Le titre de la section (``## [x.y.z] -
date``) n'en fait pas partie : le nom de la release porte déjà le numéro.

Refuse une version sans section ou à la section vide plutôt que de publier
une note vide, et une section trop longue : ``softprops/action-gh-release``
tronque en silence le corps de la release à 125000 unités UTF-16 (``substring``
JavaScript) sans échouer, ce qui contredirait « tel quel ».

Usage::

    scripts/changelog-section.py 3.62.1 [--output RELEASE_NOTES.md]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SECTION = re.compile(r"(?m)^## \[(?P<version>[^\]]+)\][^\n]*\n")
# softprops/action-gh-release (src/github.ts, truncateReleaseNotes) garde les
# 124999 premières unités UTF-16 du corps ; au-delà, il coupe sans rien dire.
MAX_NOTES_UTF16_UNITS = 124_999


def section_body(text: str, version: str) -> str | None:
    """Le corps de ``## [version]`` jusqu'au titre suivant, sans blancs de bord ; ``None`` si absente."""
    matches = list(SECTION.finditer(text))
    for i, match in enumerate(matches):
        if match.group("version") == version:
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            return text[match.end():end].strip()
    return None


def utf16_units(text: str) -> int:
    """Longueur de ``text`` telle que la compte ``String.prototype.substring`` (un caractère hors BMP vaut 2)."""
    return len(text.encode("utf-16-le", errors="surrogatepass")) // 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version", help="sans le préfixe v (ex. 3.62.1)")
    parser.add_argument("--changelog", type=Path, default=REPO / "CHANGELOG.md")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    body = section_body(args.changelog.read_text(encoding="utf-8"), args.version.removeprefix("v"))
    if not body:
        reason = "n'a pas de section" if body is None else "a une section vide"
        print(f"::error::CHANGELOG.md {reason} pour la version {args.version}", file=sys.stderr)
        return 1
    notes = body + "\n"
    units = utf16_units(notes)
    if units > MAX_NOTES_UTF16_UNITS:
        print(
            f"::error::la section {args.version} de CHANGELOG.md fait {units} unités UTF-16 : "
            f"la release GitHub la tronquerait au-delà de {MAX_NOTES_UTF16_UNITS} sans échouer",
            file=sys.stderr,
        )
        return 1
    if args.output is None:
        sys.stdout.write(notes)
    else:
        args.output.write_text(notes, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
