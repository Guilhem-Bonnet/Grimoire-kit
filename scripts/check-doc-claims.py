#!/usr/bin/env python3
"""Garde de vitrine — aucun chiffre sans source mécanique (POS-10).

Un compteur de vitrine (« 5 990+ tests », un badge ``tests-5990+``) mesure la
taille, pas la valeur, et vieillit en silence : celui du README était écrit à la
main, ne bougeait plus, et ne disait rien de ce qu'il comptait. Une affirmation
chiffrée de la vitrine porte donc sa source, ou n'y figure pas.

Périmètre : ``README.md``, ``README.fr.md``, ``docs/index.md``. Trois formes de
chiffre y sont des affirmations :

- un nombre suivi de ``%``, ``×``, ``x`` ou ``fois``, ou précédé de ``×`` ;
- un nombre suivi de ``tests`` (le compteur de vanité par excellence) ;
- un badge shields.io statique dont le message est un nombre (hors versions
  de langage : ``python-3.12+`` est une exigence, pas une mesure).

Chacune doit tenir, sur sa ligne, une ancre ::

    <!-- source: chemin/du/fichier ; recompute: commande qui le recalcule -->

``source`` est un fichier du dépôt **qui doit exister** : l'ancre ne peut pas
pourrir en pointant vers un fichier supprimé. ``recompute`` est facultatif
mais recommandé. Le garde ne vérifie pas que le chiffre égale le contenu de la
source (il ne sait pas lire une commande arbitraire) : il garantit qu'une
source nommée existe et qu'un relecteur sait où regarder.

Usage::

    python scripts/check-doc-claims.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
SCOPE = ("README.md", "README.fr.md", "docs/index.md")
VERSION_LABELS = frozenset({"python", "node", "rust", "version"})

_ANCHOR = re.compile(r"<!--\s*source:\s*(?P<ref>[^;>]*?)\s*(?:;\s*recompute:\s*(?P<cmd>[^>]*?))?\s*-->")
_COMMENT = re.compile(r"<!--.*?-->")
_BADGE = re.compile(r"img\.shields\.io/badge/(?P<spec>[^\"'?)\s]+)")
_TAG = re.compile(r"<[^>]*>")
_LINK = re.compile(r"!?\[(?P<text>[^\]]*)\]\([^)]*\)")
_CODE_SPAN = re.compile(r"`[^`\n]*`")
_FENCE = re.compile(r"^\s*(```|~~~)")
_CLAIM = re.compile(
    r"(?<![\w./#-])\d(?:[\d\u00a0\u202f .,]*\d)?\+?\s?(?:%|×|x(?!\w)|fois\b|tests?\b)"
    r"|×\s?\d(?:[\d.,]*\d)?"
)


def _badge_claim(spec: str) -> str | None:
    parts = re.split(r"(?<!-)-(?!-)", unquote(spec))
    if len(parts) < 2:
        return None
    label, message = parts[0].lower(), parts[1]
    if label in VERSION_LABELS or not re.search(r"\d", message):
        return None
    return f"badge statique `{label}-{message}`"


def claims_in_line(line: str) -> list[str]:
    """Les affirmations chiffrées d'une ligne, ancres et balisage mis à part."""
    found = [claim for match in _BADGE.finditer(line) if (claim := _badge_claim(match.group("spec")))]
    text = _COMMENT.sub(" ", line)
    text = _LINK.sub(lambda m: m.group("text"), text)
    text = _CODE_SPAN.sub(" ", text)
    text = _TAG.sub(" ", text)
    found.extend(m.group(0).strip() for m in _CLAIM.finditer(text))
    return found


def problems(root: Path = ROOT, scope: tuple[str, ...] = SCOPE) -> list[str]:
    found: list[str] = []
    for name in scope:
        path = root / name
        if not path.is_file():
            found.append(f"{name} : fichier du périmètre absent, garde non vérifiée")
            continue
        in_fence = False
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if _FENCE.match(line):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            claims = claims_in_line(line)
            if not claims:
                continue
            anchor = _ANCHOR.search(line)
            if anchor is None:
                found.append(f"{name}:{number} : « {claims[0]} » sans ancre `<!-- source: fichier -->`")
                continue
            ref = anchor.group("ref").split("#", 1)[0].strip()
            if not ref or not (root / ref).exists():
                found.append(f"{name}:{number} : ancre vers `{ref or '(vide)'}` qui n'existe pas dans le dépôt")
    return found


def main() -> int:
    found = problems()
    if found:
        print(f"{len(found)} chiffre(s) de vitrine sans source vérifiable :", file=sys.stderr)
        for line in found:
            print(f"  - {line}", file=sys.stderr)
        print(
            "\nAjouter `<!-- source: fichier ; recompute: commande -->` sur la ligne, ou retirer le chiffre.",
            file=sys.stderr,
        )
        return 1
    print(f"ok — {len(SCOPE)} fichiers de vitrine, chaque chiffre porte une source existante")
    return 0


if __name__ == "__main__":
    sys.exit(main())
