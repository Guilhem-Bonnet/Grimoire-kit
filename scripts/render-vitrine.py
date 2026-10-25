#!/usr/bin/env python3
"""Rend la vitrine : chaque chiffre vient de ``meta.json``, jamais de la page.

``web/index.html`` ne contient aucun nombre saisi. Un chiffre y est un élément
vide qui désigne sa source ::

    <span data-source="version" data-format=" ({})"></span>
    <span data-source="counts.patterns"></span>

``data-source`` est un chemin pointé dans ``meta.json`` (produit par
``scripts/gen-site-data.py --meta-only``). ``data-format`` est un gabarit
facultatif où ``{}`` reçoit la valeur : il porte la ponctuation, pour qu'elle
disparaisse avec le chiffre.

Si la valeur manque (clé absente, ``null``, ``unknown``), l'élément reste vide :
la page n'affiche pas de chiffre plutôt qu'un chiffre faux.

Usage::

    python scripts/render-vitrine.py --meta _site/data/meta.json \\
        --html web/index.html --out _site/index.html
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

_ELEMENT = re.compile(
    r"<(?P<tag>[a-zA-Z][\w-]*)(?P<attrs>[^>]*?\bdata-source=\"(?P<key>[^\"]+)\"[^>]*)>"
    r"(?P<body>.*?)</(?P=tag)>",
    re.DOTALL,
)
_FORMAT = re.compile(r"\bdata-format=\"([^\"]*)\"")


def lookup(meta: dict, key: str) -> str | None:
    """Valeur affichable de ``key`` (chemin pointé), ``None`` si non calculée."""
    node: object = meta
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    if node is None or isinstance(node, (dict, list, bool)) or node == "unknown":
        return None
    return str(node)


def sources(page: str) -> list[str]:
    """Clés ``data-source`` citées par la page."""
    return [m.group("key") for m in _ELEMENT.finditer(page)]


def render(page: str, meta: dict) -> str:
    def fill(m: re.Match[str]) -> str:
        value = lookup(meta, m.group("key"))
        fmt = _FORMAT.search(m.group("attrs"))
        text = ""
        if value is not None:
            text = html.escape(html.unescape(fmt.group(1)).replace("{}", value) if fmt else value)
        return f"<{m.group('tag')}{m.group('attrs')}>{text}</{m.group('tag')}>"

    return _ELEMENT.sub(fill, page)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--meta", type=Path, required=True)
    ap.add_argument("--html", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    meta = json.loads(args.meta.read_text(encoding="utf-8"))
    page = args.html.read_text(encoding="utf-8")
    missing = [k for k in sources(page) if lookup(meta, k) is None]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(page, meta), encoding="utf-8")
    for key in missing:
        print(f"[WARN] {key} : non calculée, aucun chiffre affiché")
    print(f"[OK] {args.out} — {len(sources(page))} chiffre(s) rendu(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
