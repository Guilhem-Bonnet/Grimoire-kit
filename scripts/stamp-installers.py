#!/usr/bin/env python3
"""Estampille la version épinglée dans install.sh et install.ps1.

Les deux installeurs portent `PINNED_VERSION=""` / `$PinnedVersion = ""` dans le
dépôt. Le script publié avec une release en est une copie où cette valeur est la
version du tag : sans argument, il installe exactement ce que la release livre.

Usage:
  python3 scripts/stamp-installers.py --version 3.71.0 --out dist-installers/
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# (fichier, motif de la ligne vide, gabarit estampillé)
TARGETS = (
    ("install.sh", re.compile(r'^PINNED_VERSION=""$', re.MULTILINE), 'PINNED_VERSION="{v}"'),
    ("install.ps1", re.compile(r'^\$PinnedVersion = ""$', re.MULTILINE), '$PinnedVersion = "{v}"'),
)
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+([.-][0-9A-Za-z.]+)?$")


def stamp(text: str, pattern: re.Pattern[str], template: str, version: str) -> str:
    """Remplace la ligne vide ; échoue si elle est absente ou déjà estampillée."""
    if len(pattern.findall(text)) != 1:
        raise ValueError("la ligne de version épinglée vide est introuvable ou en double")
    return pattern.sub(template.format(v=version), text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--root", type=Path, default=ROOT)
    args = ap.parse_args(argv)
    if not VERSION_RE.match(args.version):
        print(f"version invalide : {args.version!r}", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    for name, pattern, template in TARGETS:
        text = (args.root / name).read_text(encoding="utf-8")
        try:
            stamped = stamp(text, pattern, template, args.version)
        except ValueError as exc:
            print(f"{name} : {exc}", file=sys.stderr)
            return 1
        # newline="" : ni CRLF ni LF n'est imposé au fichier publié.
        with open(args.out / name, "w", encoding="utf-8", newline="") as fh:
            fh.write(stamped)
        print(f"{name} → {args.out / name} ({args.version})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
