#!/usr/bin/env python3
"""Génère web/workspace/icons.svg (sprite de <symbol>) depuis docs/assets/icons/*.svg.

Les icônes maison portent des couleurs violettes en dur (bloc <style> avec
classes .s / .f / .fs, et variante prefers-color-scheme). Le cockpit a besoin
qu'elles suivent la couleur du texte : le script normalise tout vers
`currentColor` (trait 1,5, arrondis, remplissage nul sauf classes .f/.fs) et
retire les <style>, dont les classes globales fuiraient hors du sprite.

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
OUT = ROOT / "web" / "workspace" / "icons.svg"

_STROKE = 'stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" fill="none"'
_STYLE_RE = re.compile(r"<style>.*?</style>", re.DOTALL)
_CLASS_RE = re.compile(r'\sclass="([^"]*)"')
_SVG_RE = re.compile(r"<svg\b[^>]*>(.*)</svg>", re.DOTALL)


def _inner(markup: str) -> str:
    """Corps normalisé d'une icône : plus de style, plus de couleur littérale."""
    match = _SVG_RE.search(_STYLE_RE.sub("", markup))
    if not match:
        raise ValueError("pas de <svg>")
    body = match.group(1)

    def swap(m: re.Match[str]) -> str:
        classes = m.group(1).split()
        if "s" in classes:
            return " " + _STROKE
        if "f" in classes or "fs" in classes:
            return ' fill="currentColor"'
        return ""

    body = _CLASS_RE.sub(swap, body)
    # Un élément `.f` peut déjà porter un style="…stroke:none" : l'attribut
    # `style` prime sur `stroke` hérité, donc rien d'autre à faire.
    body = re.sub(r"\n\s*\n+", "\n", body).strip()
    return re.sub(r"(?m)^", "    ", body)


def build() -> str:
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg">',
        "  <!-- Généré par scripts/build-icon-sprite.py depuis docs/assets/icons — ne pas éditer. -->",
    ]
    for path in sorted(SRC.glob("*.svg")):
        parts.append(f'  <symbol id="{path.stem}" viewBox="0 0 24 24" fill="none">')
        parts.append(_inner(path.read_text(encoding="utf-8")))
        parts.append("  </symbol>")
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
