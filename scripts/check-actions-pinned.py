#!/usr/bin/env python3
"""Vérifie que toutes les GitHub Actions des workflows sont épinglées par SHA.

Les actions non épinglées (par exemple @v1, @v3, @release/v1, @stable) créent des
risques de sécurité : le tag ou la branche peut être réassigné, modifié ou
supprimé sans préavis.

Le garde est hors ligne : il ne résout aucun SHA (la résolution par `gh api` a
servi une seule fois à l'épinglage). Chaque workflow est parsé en YAML ; sont
contrôlées les valeurs des clés `uses` de `jobs.*.steps[*]` et de `jobs.*`
(workflows réutilisables).

Règles :
  - `owner/repo@<40 hex>` exigé, suivi d'un commentaire de version
    (`# v7`, `# v1.2.3`) pour que le SHA reste relisible ; tout tag ou toute
    branche est rejeté ;
  - `./chemin` (action locale du même dépôt) est exempté ;
  - `docker://image@sha256:<64 hex>` est accepté, sans digest il est rejeté.

Le garde échoue aussi quand il ne peut pas vérifier : workflow illisible ou
YAML invalide, entrée `.yml`/`.yaml` qui n'est pas un fichier régulier, ou
aucun workflow trouvé.

Usage:
  python3 scripts/check-actions-pinned.py                  # racine = dépôt du script
  python3 scripts/check-actions-pinned.py --root <dossier> # racine contenant .github/workflows
  python3 scripts/check-actions-pinned.py --print-shas     # affiche aussi les SHA validés
"""

from __future__ import annotations

import argparse
import contextlib
import re
import sys
from collections.abc import Iterator
from pathlib import Path

import yaml  # type: ignore[import-untyped,unused-ignore]

SHA_RE = re.compile(r"@[0-9a-f]{40}$")
DOCKER_DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")
COMMENT_RE = re.compile(r"^\s*#\s*\S")

# Triplet : numéro de ligne, référence, problème.
Finding = tuple[int, str, str]


class WorkflowError(Exception):
    """Le workflow n'a pas pu être vérifié (illisible, YAML invalide...)."""


def is_pinned_by_sha(action_ref: str) -> bool:
    """Vrai si la référence est `owner/repo@<40 hex>` ou un digest docker complet."""
    if action_ref.startswith("docker://"):
        return DOCKER_DIGEST_RE.search(action_ref) is not None
    return SHA_RE.search(action_ref) is not None


def _mapping_get(node: yaml.Node, key: str) -> yaml.Node | None:
    if not isinstance(node, yaml.MappingNode):
        return None
    for k, v in node.value:
        if isinstance(k, yaml.ScalarNode) and k.value == key:
            return v  # type: ignore[no-any-return,unused-ignore]
    return None


def iter_uses_nodes(document: yaml.Node | None) -> Iterator[yaml.Node]:
    """Les valeurs `uses` de `jobs.*` et de `jobs.*.steps[*]`."""
    jobs = _mapping_get(document, "jobs") if document is not None else None
    if not isinstance(jobs, yaml.MappingNode):
        return
    for _name, job in jobs.value:
        uses = _mapping_get(job, "uses")
        if uses is not None:
            yield uses
        steps = _mapping_get(job, "steps")
        if isinstance(steps, yaml.SequenceNode):
            for step in steps.value:
                uses = _mapping_get(step, "uses")
                if uses is not None:
                    yield uses


def check_workflow_file(filepath: Path) -> list[Finding]:
    """Retourne les problèmes d'un workflow ; lève WorkflowError s'il est invérifiable."""
    try:
        text = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise WorkflowError(f"lecture impossible: {e}") from e
    try:
        document = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as e:
        raise WorkflowError(f"YAML invalide: {e}") from e

    lines = text.splitlines()
    findings: list[Finding] = []
    for node in iter_uses_nodes(document):
        line_no = node.start_mark.line + 1
        if not isinstance(node, yaml.ScalarNode):
            findings.append((line_no, "<valeur non scalaire>", "`uses` doit être une chaîne"))
            continue
        ref = str(node.value).strip()
        if ref.startswith("./"):
            continue  # action locale du même dépôt
        if not is_pinned_by_sha(ref):
            findings.append((line_no, ref, "non épinglé par SHA"))
            continue
        end = node.end_mark
        tail = lines[end.line][end.column :] if end.line < len(lines) else ""
        if not COMMENT_RE.match(tail):
            findings.append((line_no, ref, "commentaire de version absent (`# v7`)"))
    return findings


def collect_pinned_refs(filepath: Path) -> list[str]:
    """Références épinglées valides d'un workflow (pour --print-shas)."""
    try:
        document = yaml.compose(filepath.read_text(encoding="utf-8"), Loader=yaml.SafeLoader)
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return []
    return [
        str(n.value).strip()
        for n in iter_uses_nodes(document)
        if isinstance(n, yaml.ScalarNode) and is_pinned_by_sha(str(n.value).strip())
    ]


def _force_utf8_output() -> None:
    """Le garde imprime ✓, ✗ et des accents : une console cp1252 (Windows) ne doit pas le tuer.

    Autonome à dessein (pas d'import du paquet `grimoire`, absent de l'étape lint).
    `errors="replace"` : un caractère non représentable dégrade l'affichage, pas le verdict.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(OSError, ValueError):
                reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    _force_utf8_output()
    parser = argparse.ArgumentParser(description="Vérifie que toutes les GitHub Actions sont épinglées par SHA")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Racine du dépôt contenant .github/workflows (défaut: dépôt du script)",
    )
    parser.add_argument("--print-shas", action="store_true", help="Affiche les SHA détectés et validés")
    args = parser.parse_args()

    workflow_dir: Path = args.root / ".github" / "workflows"
    if not workflow_dir.is_dir():
        print(f"Erreur: {workflow_dir} n'existe pas", file=sys.stderr)
        sys.exit(1)

    entries = sorted({*workflow_dir.glob("*.yml"), *workflow_dir.glob("*.yaml")})
    all_issues: dict[Path, list[Finding]] = {}
    unverified: list[tuple[Path, str]] = []
    valid_refs: list[str] = []
    checked = 0

    for entry in entries:
        if not entry.is_file():
            unverified.append((entry, "n'est pas un fichier régulier"))
            continue
        try:
            issues = check_workflow_file(entry)
        except WorkflowError as e:
            unverified.append((entry, str(e)))
            continue
        checked += 1
        if issues:
            all_issues[entry] = issues
        if args.print_shas:
            valid_refs.extend(collect_pinned_refs(entry))

    if checked == 0 and not unverified:
        unverified.append((workflow_dir, "aucun workflow (*.yml, *.yaml) trouvé"))

    if all_issues:
        print("Actions non conformes détectées :\n", file=sys.stderr)
        for filepath, issues in all_issues.items():
            print(f"{filepath}:", file=sys.stderr)
            for line_no, ref, problem in issues:
                print(f"  Ligne {line_no}: {ref} ({problem})", file=sys.stderr)
            print(file=sys.stderr)

    if unverified:
        print("Workflows non vérifiés :\n", file=sys.stderr)
        for filepath, reason in unverified:
            print(f"  {filepath}: {reason}", file=sys.stderr)
        print(file=sys.stderr)

    if args.print_shas and valid_refs:
        print(f"\nActions validées ({len(valid_refs)} total):")
        for ref in sorted(set(valid_refs)):
            print(f"  {ref}")

    if all_issues or unverified:
        print("\n✗ Erreur: garde d'épinglage en échec.", file=sys.stderr)
        sys.exit(1)
    print("✓ Toutes les actions sont épinglées par SHA")


if __name__ == "__main__":
    main()
