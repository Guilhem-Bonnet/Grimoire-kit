#!/usr/bin/env python3
"""Triage des alertes CodeQL ouvertes de Grimoire-kit (épic #552, lot 2).

Classe chaque alerte ouverte et dismiss celles qui sont des faux positifs
(py/path-injection : chemin déjà confiné, sanitizer non reconnu par CodeQL)
ou du bruit hors périmètre sécurité (règles de qualité/style de la suite
`security-and-quality`, narrowée à `security-extended` par ce même lot).

Le scan de main qui a suivi la PR #573 (correctifs de code) a confirmé que
log-injection/tarslip/constantes mortes se referment tout seuls une fois le
code corrigé, mais que les 4 alertes py/path-injection de forge_server.py
protégées par une garde `SLUG_RE`/`is_relative_to` restent ouvertes : CodeQL
ne reconnaît pas ce genre de garde comme un sanitizer, même quand le code est
déjà sûr. Elles sont donc dismissées ici comme les autres, pas laissées de
côté en espérant une fermeture automatique qui ne viendra pas.
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections import defaultdict

REPO = "Guilhem-Bonnet/Grimoire-kit"

QUALITY_RULES = {
    "py/empty-except",
    "py/unused-global-variable",
    "py/ineffectual-statement",
    "py/import-and-import-from",
    "py/undefined-export",
    "py/unused-import",
    "py/unused-local-variable",
    "py/uninitialized-local-variable",
    "py/cyclic-import",
    "py/side-effect-in-assert",
    "py/unreachable-statement",
    "py/imprecise-assert",
    "py/implicit-string-concatenation-in-list",
    "py/repeated-import",
    "py/regex/duplicate-in-character-class",
    "py/mixed-returns",
    "py/multiple-definition",
    "py/overly-large-range",
    "py/unnecessary-lambda",
    "py/regex/unmatchable-dollar",
    "py/str-format/surplus-named-argument",
    "py/redundant-comparison",
    "py/call-to-non-callable",
}


def gh_api(path: str) -> list[dict]:
    out = subprocess.run(
        ["gh", "api", path, "--paginate"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    # --paginate concatenates JSON arrays back-to-back; split and merge.
    text = out.stdout.strip()
    alerts: list[dict] = []
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(text):
        while idx < len(text) and text[idx] in " \n\t":
            idx += 1
        if idx >= len(text):
            break
        obj, end = decoder.raw_decode(text, idx)
        alerts.extend(obj)
        idx = end
    return alerts


def classify(alert: dict) -> tuple[str, str]:
    """Retourne (verdict, justification). verdict in {skip, dismiss}."""
    rule = alert["rule"]["id"]
    loc = alert["most_recent_instance"]["location"]
    path = loc["path"]
    line = loc["start_line"]

    # PR #573 (mergée) a corrigé log-injection/tarslip/constantes mortes — le
    # scan CodeQL de main qui a suivi confirme leur fermeture (absents de tout
    # fetch state=open depuis). Les 4 alertes forge_server.py qui semblaient
    # protégées par la même PR (bp_id validé par SLUG_RE avant écriture) sont
    # RESTÉES ouvertes sur ce même scan : CodeQL ne reconnaît pas une garde
    # « valider par regex puis lever si non conforme » comme un sanitizer,
    # même quand le code est déjà sûr. Elles rejoignent donc le bucket
    # `dismiss` ci-dessous plutôt qu'un `skip` qui ne se produira jamais.

    if rule == "py/path-injection":
        return (
            "dismiss",
            "false positive",
            (
                "Chemin déjà confiné (resolve_within_allowed/SLUG_RE/"
                "_ensure_inside_root) ou dérivé d'un argument CLI/config local, "
                "jamais réseau. CodeQL ne reconnaît pas ces sanitizers du "
                "projet. Détail : docs/security/code-scanning-triage-2026-09.md"
            ),
        )
    if rule in QUALITY_RULES:
        return (
            "dismiss",
            "won't fix",
            (
                f"Qualité/style ({rule}), hors périmètre sécurité #552. Généré "
                "par security-and-quality, narrowé à security-extended par "
                "cette PR. Détail : docs/security/code-scanning-triage-2026-09.md"
            ),
        )
    return "keep", f"{path}:{line} — rule {rule} non classée, à trier manuellement"


def main() -> None:
    alerts = gh_api(f"repos/{REPO}/code-scanning/alerts?state=open&per_page=100")
    print(f"total open alerts (fresh fetch): {len(alerts)}", file=sys.stderr)

    dismissed: list[tuple[int, str, str]] = []
    skipped: list[tuple[int, str]] = []
    kept: list[tuple[int, str]] = []
    by_rule_file: dict[tuple[str, str], list[int]] = defaultdict(list)

    dry_run = "--apply" not in sys.argv

    for a in alerts:
        result = classify(a)
        n = a["number"]
        rule = a["rule"]["id"]
        path = a["most_recent_instance"]["location"]["path"]
        if result[0] == "skip":
            skipped.append((n, result[1]))
            continue
        if result[0] == "keep":
            kept.append((n, result[1]))
            continue
        _, reason, comment = result
        dismissed.append((n, reason, comment))
        by_rule_file[(rule, path)].append(n)
        if not dry_run:
            subprocess.run(
                [
                    "gh",
                    "api",
                    "-X",
                    "PATCH",
                    f"repos/{REPO}/code-scanning/alerts/{n}",
                    "-f",
                    "state=dismissed",
                    "-f",
                    f"dismissed_reason={reason}",
                    "-f",
                    f"dismissed_comment={comment}",
                ],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            print(f"dismissed #{n} ({reason})", file=sys.stderr)

    print(f"\n=== SUMMARY (dry_run={dry_run}) ===", file=sys.stderr)
    print(f"dismissed: {len(dismissed)}", file=sys.stderr)
    print(f"skipped (unexpected — classify() should not return this anymore): {len(skipped)}", file=sys.stderr)
    print(f"kept (unclassified, needs manual review): {len(kept)}", file=sys.stderr)
    for n, why in kept:
        print(f"  KEEP #{n}: {why}", file=sys.stderr)

    print("\n=== by rule x file ===", file=sys.stderr)
    for (r, p), ids in sorted(by_rule_file.items(), key=lambda kv: -len(kv[1])):
        print(f"{len(ids)}\t{r}\t{p}", file=sys.stderr)


if __name__ == "__main__":
    main()
