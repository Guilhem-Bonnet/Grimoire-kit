#!/usr/bin/env python3
"""Dire si une poussée est bien arrivée sur une PR — et qui d'autre y écrit.

Après un `git push` sur une branche de PR, l'objet PR n'est pas la source
fiable. Mesuré sur ce dépôt le 2026-08-28 : à `t+0`, `GET /pulls/{n}` renvoie
encore l'ancien `head.sha` pendant que `GET /git/ref/heads/{branche}` a déjà le
nouveau. La convergence prend moins de vingt secondes — mais dans cet
intervalle `gh pr view` affiche une tête périmée et un `mergeable` calculé sur
l'ancien contenu, souvent `CONFLICTING` alors que `git merge-tree` ne trouve
aucun conflit.

Le piège coûteux n'est pas ce décalage, c'est ce qu'on en conclut. Quatre PR ont
été fermées et rouvertes ici pour un « objet PR cassé » qui n'existait pas : la
branche allait bien, une autre session poussait dessus. Fermer une PR ne répare
rien et lui fait perdre son historique de revue.

Ce script répond à la seule question utile : la branche porte-t-elle ce que je
crois y avoir mis, et suis-je seul à écrire dessus ?

Usage::

    scripts/pr-head-check.py 213
    scripts/pr-head-check.py 213 --expect 04848769   # le SHA que vous avez poussé
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

#: Sortie non nulle : la branche ne porte pas ce qui était attendu.
EXIT_MISMATCH = 1
#: Sortie non nulle : quelqu'un d'autre a poussé depuis.
EXIT_CONCURRENT = 2


def _gh(*args: str, allow_missing: bool = False) -> str:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        if allow_missing and "HTTP 404" in result.stderr:
            return ""
        print(f"gh {' '.join(args)} : {result.stderr.strip()}", file=sys.stderr)
        raise SystemExit(EXIT_MISMATCH)
    return result.stdout.strip()


def _short(sha: str) -> str:
    return sha[:8] if sha else "—"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pr", type=int, help="numéro de la PR")
    parser.add_argument(
        "--expect",
        default="",
        help="le SHA que vous venez de pousser ; sans lui, le HEAD local sert de référence",
    )
    args = parser.parse_args()

    meta = json.loads(_gh("pr", "view", str(args.pr), "--json", "headRefName,headRefOid,mergeable"))
    branch = meta["headRefName"]
    repo = _gh("repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner")

    # La référence de branche : ce que git a réellement enregistré.
    raw_ref = _gh("api", f"repos/{repo}/git/ref/heads/{branch}", allow_missing=True)
    if not raw_ref:
        # Branche supprimée : la PR est fusionnée ou fermée, et le nettoyage
        # automatique est passé. Il n'y a plus rien à comparer.
        print(f"PR #{args.pr} — branche {branch} supprimée côté distant")
        print(f"  objet PR             {_short(meta['headRefOid'])}")
        print("\nRIEN À VÉRIFIER — la branche n'existe plus ; la PR est close ou fusionnée.")
        return 0
    ref = json.loads(raw_ref)["object"]["sha"]
    # L'objet PR : ce que l'API dérive de la branche, avec un décalage possible.
    pull = json.loads(_gh("api", f"repos/{repo}/pulls/{args.pr}"))["head"]["sha"]

    # Le HEAD local ne sert de référence que s'il a un rapport avec la branche
    # de la PR. Sinon on comparerait deux choses sans lien et on annoncerait un
    # désaccord là où l'utilisateur est simplement ailleurs dans son dépôt.
    expected = args.expect or _local_head_if_on(branch)

    print(f"PR #{args.pr} — branche {branch}")
    if expected:
        print(f"  attendu (local)      {_short(expected)}")
    print(f"  référence de branche {_short(ref)}   ← source fiable")
    print(f"  objet PR             {_short(pull)}   (mergeable: {meta['mergeable']})")

    if expected and not ref.startswith(expected[:7]) and not expected.startswith(ref[:7]):
        if _is_ancestor(expected, ref):
            print(
                f"\nÉCRIVAIN CONCURRENT — la branche porte {_short(ref)}, un descendant de "
                f"votre {_short(expected)}.\nQuelqu'un d'autre a poussé depuis. Récupérez son "
                "travail (`git fetch` puis `git merge`) au lieu de forcer :\nune poussée forcée "
                "écraserait son commit, et la politique d'outils du kit la refuse."
            )
            return EXIT_CONCURRENT
        print(
            f"\nDÉSACCORD — la branche porte {_short(ref)}, pas votre {_short(expected)}, "
            "et l'un n'est pas l'ancêtre de l'autre.\nVotre poussée n'est pas arrivée, ou "
            "elle a visé une autre branche."
        )
        return EXIT_MISMATCH

    if ref != pull:
        print(
            "\nDÉCALAGE DE PROPAGATION — la branche est à jour, l'objet PR pas encore.\n"
            "Attendez une vingtaine de secondes. Ne fermez pas la PR : il n'y a rien de cassé."
        )
        return 0

    if expected:
        print("\nSYNCHRONISÉ — la branche, l'objet PR et votre HEAD concordent.")
    else:
        print(
            "\nSYNCHRONISÉ — la branche et l'objet PR concordent.\n"
            "Aucune référence locale : lancez depuis la branche, ou passez --expect."
        )
    return 0


def _local_head_if_on(branch: str) -> str:
    """HEAD local, seulement si l'on est effectivement sur la branche de la PR.

    Le critère « partage une histoire » serait trop large : dans ce dépôt tout
    partage `main`, et l'outil annoncerait un désaccord à quiconque le lance
    depuis ailleurs. Être sur la branche est la seule condition qui rend la
    comparaison signifiante.
    """
    current = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, check=False
    ).stdout.strip()
    if current != branch:
        return ""
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    ).stdout.strip()


def _is_ancestor(maybe_ancestor: str, descendant: str) -> bool:
    return (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", maybe_ancestor, descendant],
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
