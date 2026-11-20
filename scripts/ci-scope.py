#!/usr/bin/env python3
"""Décide si une PR paie la CI complète ou seulement sa part « documentation ».

Une PR qui ne touche que de la prose (``docs/``, fragments ``changelog.d/``,
README…) lançait la suite SDK répartie, Neo4j et les tests du framework :
une vingtaine de minutes pour des fichiers qu'aucun de ces jobs n'exécute.

Le refus est la règle : un chemin absent de :data:`DOC_ONLY_PATTERNS`, une
liste de fichiers vide ou un ``git diff`` qui échoue rendent ``full=true``.
Les jobs lourds restent déclarés et sont *sautés* par un ``if:``, jamais
retirés par un filtre ``paths:`` du déclencheur : un check requis par la
protection de ``main`` doit toujours se prononcer (succès ou saut), sinon la
PR attend un rapport qui ne vient jamais.

Usage :
  ``scripts/ci-scope.py --base HEAD^1 --head HEAD``  -> ``full=true|false``
  ``scripts/ci-scope.py --files a.md docs/b.md``    -> idem, sans git
"""

from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys
from collections.abc import Iterable

# Chemins qu'aucun test lourd n'exécute. Les tests qui *lisent* ces fichiers
# (gardes de documentation, références CLI) tournent quand même, via
# ``scripts/ci-test-shards.py --docs-subset``.
DOC_ONLY_PATTERNS: tuple[str, ...] = (
    "docs/*",
    "changelog.d/*",
    "README.md",
    "README.fr.md",
    "CONTRIBUTING.md",
    "ARCHITECTURE.md",
    "CODE_OF_CONDUCT.md",
    "SECURITY.md",
    ".github/ISSUE_TEMPLATE/*",
    ".github/PULL_REQUEST_TEMPLATE.md",
)


def is_doc_only_path(path: str) -> bool:
    """``fnmatch`` : ``*`` traverse les ``/``, donc ``docs/*`` couvre tout l'arbre."""
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in DOC_ONLY_PATTERNS)


def needs_full_ci(paths: Iterable[str]) -> bool:
    """Vrai sauf si la liste est non vide et entièrement documentaire."""
    changed = [p.strip() for p in paths if p.strip()]
    return not changed or not all(is_doc_only_path(p) for p in changed)


def changed_files(base: str, head: str) -> list[str] | None:
    """Fichiers modifiés entre deux révisions, ``None`` si git échoue.

    ``--no-renames`` : un renommage détecté n'afficherait que sa destination, et
    déplacer ``src/x.py`` vers ``docs/`` passerait pour une PR documentaire.
    """
    try:
        done = subprocess.run(
            ["git", "diff", "--name-only", "--no-renames", base, head],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"ci-scope : git diff impossible ({exc}), CI complète", file=sys.stderr)
        return None
    return done.stdout.splitlines()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--base", help="révision de départ (avec --head)")
    source.add_argument("--files", nargs="*", help="liste explicite des fichiers modifiés")
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args(argv)

    files = args.files if args.files is not None else changed_files(args.base, args.head)
    full = True if files is None else needs_full_ci(files)
    for name in files or []:
        print(f"  {'doc ' if is_doc_only_path(name) else 'code'}  {name}", file=sys.stderr)
    print(f"full={'true' if full else 'false'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
