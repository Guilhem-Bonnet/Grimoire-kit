#!/usr/bin/env python3
"""Génère web/workspace/icons.svg (sprite de <symbol>) depuis docs/assets/icons/*.svg
et docs/assets/cockpit-icons/*.svg.

Les deux dossiers suivent la même grammaire (direction « Douce » : grille 24,
trait 1,75, `stroke="currentColor"` porté par la racine <svg>). Le script
retire le bloc <style> clair/sombre des icônes maison (il sert au rendu
`<img>` dans le manuel, mais ses règles globales fuiraient hors du sprite) et
reporte les attributs de la racine sur le <symbol> : le sprite suit la couleur
du texte, donc le thème.

Les anciens noms encore cités par le cockpit (`ALIASES`) reçoivent un symbole
dupliqué depuis leur remplaçant de la famille Douce, le temps que le cockpit
adopte les nouveaux noms.

Usage :
    python scripts/build-icon-sprite.py            # écrit le sprite
    python scripts/build-icon-sprite.py --check    # échoue si le sprite est périmé (CI)
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "assets" / "icons"
SRC_COCKPIT = ROOT / "docs" / "assets" / "cockpit-icons"
OUT = ROOT / "web" / "workspace" / "icons.svg"

#: Ancien nom cité par web/workspace → icône de la famille Douce qui le remplace
#: (table de remplacement de la charte, docs/assets/brand/README-brand.md).
ALIASES: dict[str, str] = {
    "brain": "book",
    "chart": "board",
    "clipboard": "doc",
    "cognition": "chip",
    "folder-tree": "layers",
    "grimoire": "chevron",
    "microscope": "search",
    "monitor": "gauge",
    "network": "graph",
    "plug": "link",
    "rocket": "play",
    "server": "container",
    "team": "fleet",
    "workflow": "route",
}

_STYLE_RE = re.compile(r"<style>.*?</style>", re.DOTALL)
_SVG_OPEN_RE = re.compile(r"<svg\b([^>]*)>(.*)</svg>", re.DOTALL)
_DROP_ATTR_RE = re.compile(r'\s+(?:xmlns(?::\w+)?|width|height)="[^"]*"')


def _symbol(markup: str) -> tuple[str, str]:
    """Attributs de la racine (sans xmlns ni taille) et corps, sans <style>."""
    match = _SVG_OPEN_RE.search(_STYLE_RE.sub("", markup))
    if not match:
        raise ValueError("pas de <svg>")
    attrs = _DROP_ATTR_RE.sub("", match.group(1)).strip()
    return attrs, match.group(2).strip()


def build() -> str:
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg">',
        "  <!-- Généré par scripts/build-icon-sprite.py depuis docs/assets/icons — ne pas éditer. -->",
    ]
    family: dict[str, tuple[str, str]] = {}
    for path in sorted(SRC.glob("*.svg")):
        family[path.stem] = _symbol(path.read_text(encoding="utf-8"))
        attrs, body = family[path.stem]
        parts.append(f'  <symbol id="{path.stem}" {attrs}>{body}</symbol>')
    parts.append("  <!-- Anciens noms du cockpit, dupliqués depuis leur remplaçant Douce. -->")
    for old, new in sorted(ALIASES.items()):
        if new not in family:
            raise SystemExit(f"alias {old} → {new} : icône absente de {SRC.relative_to(ROOT)}")
        attrs, body = family[new]
        parts.append(f'  <symbol id="{old}" {attrs}>{body}</symbol>')
    parts.append("  <!-- Chrome du cockpit (docs/assets/cockpit-icons), copié tel quel. -->")
    for path in sorted(SRC_COCKPIT.glob("*.svg")):
        attrs, body = _symbol(path.read_text(encoding="utf-8"))
        parts.append(f'  <symbol id="{path.stem}" {attrs}>{body}</symbol>')
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="échoue si le sprite n'est pas à jour")
    args = parser.parse_args()
    expected = build()
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != expected:
            print(f"{OUT.relative_to(ROOT)} périmé : lancer python scripts/build-icon-sprite.py", file=sys.stderr)
            return 1
        return 0
    OUT.write_text(expected, encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)} : {expected.count('<symbol')} symboles")
    return 0


if __name__ == "__main__":
    sys.exit(main())
