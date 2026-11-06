#!/usr/bin/env python3
"""Répartit les fichiers de la suite SDK entre plusieurs jobs de CI.

Répartition par fichier, comme ``--dist loadfile`` : les tests d'un même fichier
restent ensemble. La taille du fichier sert d'estimation de sa durée ; le résultat
ne dépend que de l'arbre, donc chaque job calcule la même répartition.

Usage : ``scripts/ci-test-shards.py --shard 1 --of 4``
"""

from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNIT_PATTERNS = ("test_*.py", "*_test.py")
EXTRA_FILES = ("tests/test_agentic_standard.py",)


def test_files(root: Path) -> list[Path]:
    found = {path for pattern in UNIT_PATTERNS for path in (root / "tests" / "unit").rglob(pattern)}
    found.update(root / name for name in EXTRA_FILES)
    return sorted(found)


def shards(root: Path, count: int) -> list[list[str]]:
    if count < 1:
        raise ValueError("il faut au moins un job")
    buckets: list[list[str]] = [[] for _ in range(count)]
    loads = [0] * count
    for path in sorted(test_files(root), key=lambda p: (-p.stat().st_size, p.as_posix())):
        lightest = loads.index(min(loads))
        buckets[lightest].append(path.relative_to(root).as_posix())
        loads[lightest] += path.stat().st_size
    return [sorted(bucket) for bucket in buckets]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard", type=int, required=True)
    parser.add_argument("--of", type=int, required=True)
    args = parser.parse_args()
    if not 1 <= args.shard <= args.of:
        parser.error("--shard doit être compris entre 1 et --of")
    print("\n".join(shards(ROOT, args.of)[args.shard - 1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
