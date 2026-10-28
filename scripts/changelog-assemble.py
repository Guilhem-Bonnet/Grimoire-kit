#!/usr/bin/env python3
"""Assemble les fragments ``changelog.d/*.md`` sous la section de la version publiée.

Une PR n'écrit plus en tête de ``CHANGELOG.md`` : toutes les PR ouvertes y
ajoutaient leurs lignes au même endroit, et chaque fusion mettait les autres
en conflit. Elle dépose un fichier ``changelog.d/<slug>.md`` (une ou plusieurs
puces, le format des entrées de ``[Unreleased]``). Deux PR ne touchent jamais
le même fichier : elles fusionnent l'une après l'autre sans conflit.

À la release, ce script range les fragments sous ``## [X.Y.Z] - <date>``
(après les entrées déjà présentes sous ``[Unreleased]``, qui y sont reprises
elles aussi) puis supprime les fichiers. ``README.md`` et les fichiers cachés
de ``changelog.d/`` ne sont pas des fragments.

Usage::

    scripts/changelog-assemble.py 3.72.0 [--date 2026-10-28] [--dry-run]
"""

from __future__ import annotations

import argparse
import datetime
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FRAGMENT_DIR = "changelog.d"
UNRELEASED = re.compile(r"(?m)^## \[Unreleased\][^\n]*\n")
NEXT_SECTION = re.compile(r"(?m)^## \[")


def fragments(repo: Path) -> list[Path]:
    """Les fragments de ``changelog.d/``, dans l'ordre alphabétique (donc stable)."""
    directory = repo / FRAGMENT_DIR
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.md") if p.is_file() and p.name.lower() != "readme.md" and not p.name.startswith("."))


def assemble(repo: Path, version: str, date: str) -> tuple[str, list[Path]]:
    """Le CHANGELOG après assemblage, et les fragments consommés. N'écrit rien."""
    text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    start = UNRELEASED.search(text)
    if start is None:
        raise ValueError("CHANGELOG.md n'a pas de section [Unreleased]")
    following = NEXT_SECTION.search(text, start.end())
    end = following.start() if following else len(text)
    entries = [text[start.end():end].strip("\n")]
    used = fragments(repo)
    for path in used:
        body = path.read_text(encoding="utf-8").strip("\n")
        if not any(line.startswith("- ") for line in body.splitlines()):
            raise ValueError(f"{FRAGMENT_DIR}/{path.name} ne contient aucune puce « - … »")
        entries.append(body)
    merged = "\n".join(e for e in entries if e.strip())
    if not merged.strip():
        raise ValueError("rien à assembler : ni fragment, ni entrée sous [Unreleased]")
    section = f"## [Unreleased]\n\n## [{version}] - {date}\n\n{merged}\n\n"
    return text[:start.start()] + section + text[end:], used


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version", help="sans le préfixe v (ex. 3.72.0)")
    parser.add_argument("--date", default=datetime.date.today().isoformat())
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        text, used = assemble(args.repo, args.version.removeprefix("v"), args.date)
    except ValueError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    if args.dry_run:
        print(f"{len(used)} fragment(s) seraient assemblés sous [{args.version}]")
        return 0
    (args.repo / "CHANGELOG.md").write_text(text, encoding="utf-8")
    for path in used:
        path.unlink()
    print(f"{len(used)} fragment(s) assemblés sous [{args.version}] et retirés")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
