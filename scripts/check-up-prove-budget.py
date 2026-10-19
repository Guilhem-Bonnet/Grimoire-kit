#!/usr/bin/env python3
"""Budget de la preuve d'onboarding : ``grimoire up --prove`` tient sa durée déclarée.

``docs/onboarding.md`` annonce un parcours J1 court. Un texte ne se vérifie
pas ; ce garde le transforme en budget testé. Il lit la sortie JSON de
``grimoire up --prove`` (produite par le job de release sur la roue installée
dans un environnement vierge) et échoue si :

- le rapport est illisible ou n'a pas les trois champs de la preuve ;
- ``hooks_fired`` n'est pas vrai, ou ``first_gate`` n'est pas ``green`` ;
- ``time_to_first_proof_s`` dépasse le budget que ``docs/onboarding.md``
  déclare (``<!-- up-prove-budget-s: N -->``).

Un budget absent du document échoue aussi (code 2) : un seuil qu'on ne trouve
pas ne serait jamais dépassé. La durée mesurée est écrite dans le résumé du job
(``$GITHUB_STEP_SUMMARY``) ; le document ne la recopie pas, il déclare le budget.

Ce que ce garde ne mesure pas : le temps d'installation du paquet, et
les imports du processus avant que la commande ne démarre.

Usage::

    scripts/check-up-prove-budget.py --report prove.json [--doc docs/onboarding.md]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUDGET_MARKER = re.compile(r"<!--\s*up-prove-budget-s:\s*(?P<seconds>\d+(?:\.\d+)?)\s*-->")


def declared_budget(doc_text: str) -> float | None:
    """Le budget (secondes) déclaré dans le document, ``None`` s'il n'y en a pas."""
    match = BUDGET_MARKER.search(doc_text)
    return float(match.group("seconds")) if match else None


def judge(report: object, budget: float) -> list[str]:
    """Les raisons pour lesquelles *report* ne tient pas le budget ; vide si la preuve est bonne."""
    if not isinstance(report, dict):
        return ["rapport inattendu : un objet JSON est attendu"]
    problems: list[str] = []
    if report.get("hooks_fired") is not True:
        problems.append(f"hooks_fired = {report.get('hooks_fired')!r} (attendu true)")
    if report.get("first_gate") != "green":
        problems.append(f"first_gate = {report.get('first_gate')!r} (attendu 'green')")
    duration = report.get("time_to_first_proof_s")
    if isinstance(duration, bool) or not isinstance(duration, int | float):
        problems.append(f"time_to_first_proof_s = {duration!r} (nombre attendu)")
    elif duration > budget:
        problems.append(f"time_to_first_proof_s = {duration} s dépasse le budget de {budget:g} s")
    return problems


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", type=Path, required=True, help="sortie JSON de `grimoire up --prove`")
    parser.add_argument("--doc", type=Path, default=REPO / "docs" / "onboarding.md")
    args = parser.parse_args(argv)

    try:
        budget = declared_budget(args.doc.read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"✗ document de budget illisible ({args.doc}) : {exc}", file=sys.stderr)
        return 2
    if budget is None:
        print(f"✗ aucun budget déclaré dans {args.doc} (<!-- up-prove-budget-s: N -->)", file=sys.stderr)
        return 2
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"✗ rapport illisible ({args.report}) : {exc}", file=sys.stderr)
        return 1

    problems = judge(report, budget)
    measured = report.get("time_to_first_proof_s") if isinstance(report, dict) else None
    summary = f"up --prove : {measured!r} s mesurés, budget {budget:g} s"
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with Path(step_summary).open("a", encoding="utf-8") as handle:
            handle.write(f"### Preuve d'onboarding\n\n{summary} — {'échec' if problems else 'tenu'}\n")
    if problems:
        print(f"✗ {summary}", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"✓ {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
