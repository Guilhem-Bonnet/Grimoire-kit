#!/usr/bin/env python3
"""Vérifie que toutes les GitHub Actions utilisées dans les workflows sont épinglées par SHA.

Les actions non épinglées (par exemple @v1, @v3, @release/v1) créent des risques de sécurité :
la version peut être réassignée, modifiée, ou supprimée sans préavis.

Seules les actions épinglées par SHA (40 caractères hexadécimaux) sont acceptées.

Exemples d'actions valides :
  - uses: actions/checkout@a83e7850d592aacd9ed61157eb88bafe1c4b8289

Exemples d'actions rejetées :
  - uses: actions/checkout@v1
  - uses: actions/checkout@v4
  - uses: pypa/gh-action-pypi-publish@release/v1

Usage:
  python3 scripts/check-actions-pinned.py              # Vérifie tous les workflows
  python3 scripts/check-actions-pinned.py --print-shas # Affiche également les SHA détectés
"""

import argparse
import re
import sys
from pathlib import Path

def extract_action_ref(uses_line: str) -> str:
    """Extrait la référence d'action (ex: owner/repo@ref) d'une ligne 'uses:'."""
    match = re.search(r"uses:\s*(.+?)(?:\s*#|$)", uses_line)
    if match:
        return match.group(1).strip()
    return ""

def is_pinned_by_sha(action_ref: str) -> bool:
    """Vérifie si une référence d'action est épinglée par SHA (40 hex chars)."""
    # Le format doit être owner/repo@SHA où SHA est 40 caractères hexadécimaux
    match = re.search(r"@([0-9a-f]{40})$", action_ref)
    return match is not None

def check_workflow_file(filepath: Path) -> list[tuple[int, str, str, bool]]:
    """Vérifie un fichier workflow. Retourne une liste de (ligne, action, ref, est_valide)."""
    issues = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except (IOError, OSError) as e:
        print(f"Erreur lecture {filepath}: {e}", file=sys.stderr)
        return issues

    for line_no, line in enumerate(lines, 1):
        if "uses:" in line:
            action_ref = extract_action_ref(line)
            if action_ref:
                is_valid = is_pinned_by_sha(action_ref)
                if not is_valid:
                    issues.append((line_no, action_ref.split("@")[0], action_ref, is_valid))

    return issues

def main():
    parser = argparse.ArgumentParser(
        description="Vérifie que toutes les GitHub Actions sont épinglées par SHA"
    )
    parser.add_argument(
        "--print-shas",
        action="store_true",
        help="Affiche les SHA détectés et validés"
    )
    args = parser.parse_args()

    workflow_dir = Path(".github/workflows")
    if not workflow_dir.exists():
        print(f"Erreur: {workflow_dir} n'existe pas", file=sys.stderr)
        sys.exit(1)

    all_issues = {}
    all_valid_shas = []
    exit_code = 0

    for workflow_file in sorted(workflow_dir.glob("*.yml")):
        issues = check_workflow_file(workflow_file)
        if issues:
            all_issues[workflow_file] = issues
            exit_code = 1

        # Collecter les SHA valides si demandé
        if args.print_shas:
            with open(workflow_file, "r", encoding="utf-8") as f:
                for line in f:
                    if "uses:" in line:
                        action_ref = extract_action_ref(line)
                        if action_ref and is_pinned_by_sha(action_ref):
                            all_valid_shas.append(action_ref)

    # Afficher les résultats
    if all_issues:
        print("Actions non épinglées par SHA détectées :\n", file=sys.stderr)
        for filepath in sorted(all_issues.keys()):
            print(f"{filepath}:", file=sys.stderr)
            for line_no, action_name, action_ref, _ in all_issues[filepath]:
                print(f"  Ligne {line_no}: {action_ref}", file=sys.stderr)
            print()

    if args.print_shas and all_valid_shas:
        print(f"\nActions validées ({len(all_valid_shas)} total):")
        for sha in sorted(set(all_valid_shas)):
            print(f"  {sha}")

    if exit_code == 0:
        print("✓ Toutes les actions sont épinglées par SHA")
    else:
        print(
            "\n✗ Erreur: Des actions ne sont pas épinglées par SHA.",
            file=sys.stderr
        )
        print(
            "Raison: Les actions non épinglées peuvent être modifiées ou supprimées "
            "sans préavis.",
            file=sys.stderr
        )

    sys.exit(exit_code)

if __name__ == "__main__":
    main()
