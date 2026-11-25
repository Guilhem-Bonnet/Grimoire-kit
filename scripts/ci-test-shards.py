#!/usr/bin/env python3
"""Répartit les fichiers de la suite SDK ou E2E entre plusieurs jobs de CI.

Répartition par fichier, comme ``--dist loadfile`` : les tests d'un même fichier
restent ensemble. La taille du fichier sert d'estimation de sa durée ; le résultat
ne dépend que de l'arbre, donc chaque job calcule la même répartition.

Usage : ``scripts/ci-test-shards.py [--suite e2e] --shard 1 --of 4``

``--docs-subset`` (sans ``--shard``) liste les seuls fichiers de test qui citent un
document du dépôt : c'est ce qu'une PR purement documentaire exécute à la place
de la suite (``scripts/ci-scope.py``). ``--suite framework`` vise ``tests/test_*.py``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNIT_PATTERNS = ("test_*.py", "*_test.py")
EXTRA_FILES = ("tests/test_agentic_standard.py",)
# Exclu du job framework de ci-validate.yml (``--ignore``), donc du sous-ensemble.
FRAMEWORK_IGNORED = ("tests/test_background_tasks.py",)
# Un fichier de test qui cite l'un de ces noms lit (ou peut lire) un document :
# il doit tourner quand seule la documentation change. Large exprès (``docs``
# attrape aussi ``docstring``) : un test de trop coûte des secondes, un test
# oublié laisse main rouge.
DOC_MARKERS = (
    "docs",
    "README",
    "CHANGELOG",
    "changelog.d",
    "CONTRIBUTING",
    "ARCHITECTURE",
    "CODE_OF_CONDUCT",
    "SECURITY.md",
    "PULL_REQUEST_TEMPLATE",
    "ISSUE_TEMPLATE",
)


def test_files(root: Path, suite: str = "sdk") -> list[Path]:
    if suite == "e2e":
        return sorted((root / "tests" / "e2e").glob("test_*.py"))
    if suite == "framework":
        return sorted(
            p for p in (root / "tests").glob("test_*.py") if p.relative_to(root).as_posix() not in FRAMEWORK_IGNORED
        )
    found = {path for pattern in UNIT_PATTERNS for path in (root / "tests" / "unit").rglob(pattern)}
    found.update(root / name for name in EXTRA_FILES)
    return sorted(found)


def cites_a_document(path: Path) -> bool:
    text = path.read_text(encoding="utf-8", errors="replace")
    return any(marker in text for marker in DOC_MARKERS)


def docs_subset(root: Path, suite: str = "sdk") -> list[str]:
    """Fichiers de la suite qui citent un document (voir :data:`DOC_MARKERS`)."""
    return [p.relative_to(root).as_posix() for p in test_files(root, suite) if cites_a_document(p)]


def shards(root: Path, count: int, suite: str = "sdk", *, docs_only: bool = False) -> list[list[str]]:
    if count < 1:
        raise ValueError("il faut au moins un job")
    buckets: list[list[str]] = [[] for _ in range(count)]
    loads = [0] * count
    files = [p for p in test_files(root, suite) if not docs_only or cites_a_document(p)]
    for path in sorted(files, key=lambda p: (-p.stat().st_size, p.as_posix())):
        lightest = loads.index(min(loads))
        buckets[lightest].append(path.relative_to(root).as_posix())
        loads[lightest] += path.stat().st_size
    return [sorted(bucket) for bucket in buckets]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("sdk", "e2e", "framework"), default="sdk")
    parser.add_argument("--docs-subset", action="store_true", help="seuls les tests qui citent un document")
    parser.add_argument("--shard", type=int)
    parser.add_argument("--of", type=int)
    args = parser.parse_args()
    if args.shard is None and args.of is None and args.docs_subset:
        print("\n".join(docs_subset(ROOT, args.suite)))
        return 0
    if args.shard is None or args.of is None:
        parser.error("--shard et --of vont ensemble (seul --docs-subset s'en passe)")
    if not 1 <= args.shard <= args.of:
        parser.error("--shard doit être compris entre 1 et --of")
    print("\n".join(shards(ROOT, args.of, args.suite, docs_only=args.docs_subset)[args.shard - 1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
