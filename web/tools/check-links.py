#!/usr/bin/env python3
"""Vérifie que chaque src/href relatif et chaque url() d'une page de web/ pointe vers un fichier existant.

Usage : python web/tools/check-links.py [page.html ...]   (défaut : web/index.html)
Les ancres #... sont vérifiées contre les id de la page cible. Les URL absolues
(http, https, mailto, data) sont ignorées : c'est le travail de la vérification
« aucune requête tierce », faite à part.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

WEB = Path(__file__).resolve().parent.parent
ATTR = re.compile(r"""\b(?:src|href|poster)\s*=\s*["']([^"']*)["']""", re.I)
CSS_URL = re.compile(r"""url\(\s*["']?([^"')]+)["']?\s*\)""", re.I)
ID = re.compile(r"""\bid\s*=\s*["']([^"']+)["']""", re.I)
SKIP = ("http:", "https:", "mailto:", "data:", "javascript:", "//")


def check(page: Path) -> list[str]:
    text = page.read_text(encoding="utf-8")
    refs = ATTR.findall(text) + CSS_URL.findall(text)
    errors: list[str] = []
    for ref in refs:
        if not ref or ref.lower().startswith(SKIP):
            continue
        parts = urlsplit(ref)
        target = page if not parts.path else (page.parent / unquote(parts.path))
        if not target.is_file():
            errors.append(f"{page.name}: {ref} -> fichier absent")
        elif parts.fragment and target.suffix == ".html":
            ids = set(ID.findall(target.read_text(encoding="utf-8")))
            if parts.fragment not in ids:
                errors.append(f"{page.name}: {ref} -> ancre absente")
    print(f"{page.name}: {len(refs)} références lues")
    return errors


def main(argv: list[str]) -> int:
    pages = [Path(a).resolve() for a in argv] or [WEB / "index.html"]
    errors = [e for p in pages for e in check(p)]
    for e in errors:
        print("ERREUR", e)
    print("OK" if not errors else f"{len(errors)} erreur(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
