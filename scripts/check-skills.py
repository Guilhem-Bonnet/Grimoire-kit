#!/usr/bin/env python3
"""Garde des skills livrés : format de la spécification Agent Skills + un ``evals.json`` par skill (R30).

Deux contrôles, sur chaque skill que le kit livre (``framework/hosts/skills/*.md``,
``archetypes/*/skills/*.md``, ``extensions/*/artifacts/skills/*/SKILL.md``) :

1. le format, par ``grimoire.hosts.skill_spec`` (le même contrôle que ``doctor`` et
   que les émetteurs) : un skill sans frontmatter n'a pas de ``description``, il échoue ;
2. ``evals/skills/<slug>/evals.json`` existe, est du JSON et porte au moins un cas
   ``{"id", "prompt", "expected_output"}`` ; son ``skill_name`` est le slug.

Un ``evals.json`` minimal ne mesure pas le skill : il fixe, par écrit, ce qu'on attend
qu'il fasse, pour que le banc ou une relecture puissent le comparer. Il n'est pas
exécuté ici.

Usage :
    python scripts/check-skills.py                     # tous les skills livrés
    python scripts/check-skills.py chemin/skill.md ... # ces fichiers seulement
    python scripts/check-skills.py --root DIR          # un autre clone du kit
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from grimoire.hosts.skill_spec import validate_skill_file  # noqa: E402

CASE_KEYS = ("id", "prompt", "expected_output")


def shipped_skills(root: Path) -> list[Path]:
    files = sorted((root / "framework" / "hosts" / "skills").glob("*.md"))
    files += sorted((root / "archetypes").glob("*/skills/*.md"))
    files += sorted((root / "extensions").glob("*/artifacts/skills/*/SKILL.md"))
    return files


def slug_of(path: Path) -> str:
    return path.parent.name if path.name == "SKILL.md" else path.stem


def eval_problems(root: Path, slug: str) -> list[str]:
    """Ce qui manque ou est faux dans ``evals/skills/<slug>/evals.json``."""
    target = root / "evals" / "skills" / slug / "evals.json"
    if not target.is_file():
        return [f"evals.json absent ({target.relative_to(root).as_posix()})"]
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"evals.json illisible ({exc.__class__.__name__})"]
    if not isinstance(data, dict):
        return ["evals.json : un objet est attendu"]
    problems: list[str] = []
    if data.get("skill_name") != slug:
        problems.append(f"evals.json : skill_name « {data.get('skill_name')} » différent du skill « {slug} »")
    cases = data.get("evals")
    if not isinstance(cases, list) or not cases:
        problems.append("evals.json : au moins un cas est requis dans « evals »")
        return problems
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            problems.append(f"evals.json : cas {index} n'est pas un objet")
            continue
        missing = [k for k in CASE_KEYS if not str(case.get(k, "")).strip()]
        if missing:
            problems.append(f"evals.json : cas {index} sans {', '.join(missing)}")
    return problems


def check(root: Path, files: list[Path]) -> dict[str, list[str]]:
    failures: dict[str, list[str]] = {}
    for path in files:
        found = [str(v) for v in validate_skill_file(path)] + eval_problems(root, slug_of(path))
        if found:
            try:
                label = path.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                label = path.as_posix()
            failures[label] = found
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", type=Path, help="skills à contrôler (défaut : tous ceux que le kit livre)")
    parser.add_argument("--root", type=Path, default=ROOT, help="racine du clone du kit")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    files = list(args.paths) or shipped_skills(root)
    if not files:
        print("aucun skill trouvé : garde non évaluée", file=sys.stderr)
        return 1
    failures = check(root, files)
    for label, problems in failures.items():
        for problem in problems:
            print(f"{label} : {problem}")
    if failures:
        print(f"{len(failures)} skill(s) en défaut sur {len(files)}")
        return 1
    print(f"{len(files)} skills conformes, chacun avec un evals.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
