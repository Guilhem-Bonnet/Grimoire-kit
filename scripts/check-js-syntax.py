#!/usr/bin/env python3
"""Garde de syntaxe — chaque fichier JavaScript suivi sous ``web/`` passe ``node --check``.

``web/`` porte le cockpit et la vitrine : du JavaScript sans framework, sans
étape de build, servi tel quel et copié en entier dans la roue. Une accolade
perdue n'y était vue que par la suite e2e, quand elle tournait. Ce garde lit la
liste des fichiers suivis par git (``.js`` et ``.mjs``) et demande à Node de
les analyser un par un, sans rien exécuter.

Un même répertoire mêle scripts classiques et modules ES sous l'extension
``.js`` ; c'est Node qui tranche, par sa détection de la syntaxe de module. Un
Node qui ne sait pas le faire rendrait un faux rouge sur chaque module : le
garde le sonde d'abord et le refuse nommément.

Le garde échoue aussi quand il ne peut pas rendre son verdict : ``node`` ou
``git`` absent, dépôt illisible, aucun fichier JavaScript suivi.

Usage::

    python scripts/check-js-syntax.py                 # racine = dépôt du script
    python scripts/check-js-syntax.py --root <dossier>
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = "web"
SUFFIXES = (".js", ".mjs")
#: Un module ES minimal : ce que la sonde demande à Node de reconnaître dans un `.js`.
_PROBE = "export const sonde = 1;\n"


class GuardError(Exception):
    """Le garde ne peut pas rendre son verdict (outil absent, dépôt illisible...)."""


def tracked_files(root: Path) -> list[str]:
    """Chemins POSIX, relatifs à ``root``, des fichiers JavaScript suivis sous ``web/``."""
    if shutil.which("git") is None:
        raise GuardError("`git` introuvable : la liste des fichiers suivis est illisible")
    listing = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--", WEB],
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    if listing.returncode != 0:
        raise GuardError(f"`git ls-files` a échoué sous {root} : {listing.stderr.strip()}")
    return sorted(name for name in listing.stdout.split("\0") if name.endswith(SUFFIXES))


def _check(node: str, path: Path) -> str | None:
    """Le message de Node si ``path`` ne s'analyse pas, ``None`` sinon."""
    result = subprocess.run(
        [node, "--check", str(path)], capture_output=True, encoding="utf-8", errors="replace", check=False
    )
    if result.returncode == 0:
        return None
    return (result.stderr or result.stdout).strip() or f"node --check a rendu le code {result.returncode}"


def find_node() -> str:
    """Le binaire ``node``, après avoir vérifié qu'il reconnaît un module ES dans un ``.js``."""
    node = shutil.which("node")
    if node is None:
        raise GuardError("`node` introuvable : la syntaxe du JavaScript de web/ n'est pas vérifiée")
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "sonde.js"
        probe.write_text(_PROBE, encoding="utf-8")
        refused = _check(node, probe)
    if refused is not None:
        raise GuardError(
            f"le Node trouvé ({node}) ne reconnaît pas un module ES dans un fichier .js : chaque module de web/ "
            "serait refusé à tort. Utiliser un Node plus récent (celui qu'exige l'ESLint de `make lint-js`)."
        )
    return node


def problems(root: Path = ROOT, files: list[str] | None = None) -> list[str]:
    """Une ligne par fichier refusé ; lève ``GuardError`` si le verdict est impossible."""
    files = tracked_files(root) if files is None else files
    if not files:
        raise GuardError(
            f"aucun fichier {'/'.join(SUFFIXES)} suivi sous {root / WEB} : garde sans objet, donc non vérifiée"
        )
    node = find_node()
    with ThreadPoolExecutor(max_workers=8) as pool:
        verdicts = list(pool.map(lambda name: _check(node, root / name), files))
    return [f"{name} : {refused}" for name, refused in zip(files, verdicts, strict=True) if refused is not None]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Vérifie la syntaxe du JavaScript suivi sous web/ (node --check)")
    parser.add_argument("--root", type=Path, default=ROOT, help="Racine du dépôt (défaut : dépôt du script)")
    args = parser.parse_args(argv)
    try:
        files = tracked_files(args.root)
        found = problems(args.root, files)
    except GuardError as error:
        print(f"garde de syntaxe JavaScript non rendue : {error}", file=sys.stderr)
        return 1
    if found:
        print(f"{len(found)} fichier(s) JavaScript de web/ refusé(s) par `node --check` :", file=sys.stderr)
        for line in found:
            print(f"  - {line}", file=sys.stderr)
        return 1
    print(f"ok — {len(files)} fichiers JavaScript suivis sous web/ passent `node --check`")
    return 0


if __name__ == "__main__":
    sys.exit(main())
