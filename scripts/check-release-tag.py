#!/usr/bin/env python3
"""Refuse un tag de release qui ne peut pas partir en publication.

Le workflow ``publish.yml`` est l'unique voie de release (D-01). Ce garde en
est la première porte : avant de construire quoi que ce soit, le tag doit

* avoir la forme d'une version (``vX.Y.Z`` stable, ``vX.Y.Z-<suffixe>`` pré-version) ;
* dire la même chose que ``version.txt`` — sinon le paquet publié porterait un
  numéro que le dépôt ne reconnaît pas ;
* pointer un commit atteignable depuis ``origin/main`` — un tag posé sur une
  branche de travail publierait du code que la revue n'a jamais vu. Si la
  référence ``origin/main`` n'existe pas dans le dépôt cloné (clone peu
  profond), le garde refuse : non vérifié n'est pas vérifié.

Il écrit aussi, pour les jobs suivants, ``version`` et ``stable`` : seul un tag
stable part sur PyPI, une pré-version ne produit qu'une release GitHub
marquée « pre-release ».

Usage::

    scripts/check-release-tag.py --tag v1.2.3 [--main-ref origin/main] [--github-output "$GITHUB_OUTPUT"]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

STABLE = re.compile(r"^v\d+\.\d+\.\d+$")
PRERELEASE = re.compile(r"^v\d+\.\d+\.\d+-[0-9A-Za-z][0-9A-Za-z.]*$")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", check=False, timeout=60
    )


def problems(repo: Path, tag: str, main_ref: str) -> list[str]:
    """Les raisons pour lesquelles ``tag`` ne peut pas être publié (vide = publiable)."""
    out: list[str] = []
    if not (STABLE.match(tag) or PRERELEASE.match(tag)):
        out.append(f"le tag « {tag} » n'a pas la forme vX.Y.Z ou vX.Y.Z-<suffixe>")

    version_file = repo / "version.txt"
    if not version_file.is_file():
        out.append("version.txt est absent : le tag ne peut être confronté à rien")
    else:
        declared = "v" + version_file.read_text(encoding="utf-8").strip()
        if tag != declared:
            out.append(
                f"tag {tag} mais version.txt annonce {declared} — "
                "le paquet publié porterait un numéro que le dépôt ne reconnaît pas"
            )

    if _git(repo, "rev-parse", "--verify", "--quiet", f"{main_ref}^{{commit}}").returncode != 0:
        out.append(
            f"{main_ref} est introuvable : impossible de vérifier que le tag est sur main "
            "(clone peu profond ? utiliser fetch-depth: 0)"
        )
    else:
        # `tag^{commit}` : un tag annoté se déréférence vers son commit.
        target = _git(repo, "rev-parse", "--verify", "--quiet", f"{tag}^{{commit}}")
        if target.returncode != 0:
            out.append(f"le tag {tag} n'existe pas dans ce dépôt")
        elif _git(repo, "merge-base", "--is-ancestor", target.stdout.strip(), main_ref).returncode != 0:
            out.append(
                f"le tag {tag} pointe un commit qui n'est pas sur {main_ref} — "
                "une release ne part que de main"
            )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag", required=True)
    parser.add_argument("--main-ref", default="origin/main")
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--github-output", type=Path, default=None)
    args = parser.parse_args(argv)

    found = problems(args.repo, args.tag, args.main_ref)
    if found:
        print(f"::error::le tag {args.tag} ne peut pas être publié", file=sys.stderr)
        for problem in found:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    stable = bool(STABLE.match(args.tag))
    if args.github_output is not None:
        with args.github_output.open("a", encoding="utf-8") as fh:
            fh.write(f"version={args.tag[1:]}\nstable={'true' if stable else 'false'}\n")
    print(f"{args.tag} confirmé : conforme à version.txt, sur {args.main_ref}, {'stable' if stable else 'pré-version'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
