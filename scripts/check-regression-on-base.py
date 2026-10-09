#!/usr/bin/env python3
"""Une PR `fix:` doit apporter un test qui échoue sur son propre point de départ.

Le critère « test rouge visible sur un commit antérieur » ne tient pas ici :
le squash efface l'historique, et l'auto-merge tire avant les correctifs
demandés. Ce garde rejoue donc la preuve au lieu de la chercher dans
l'historique : il prend les tests ajoutés ou modifiés par la PR, les exécute
contre le code de `base_sha`, et exige au moins un échec.

Échappatoire : l'étiquette `no-regression-test` ET, dans la description de la
PR, une ligne `no-regression-test: <justification>`. L'étiquette seule ne
suffit pas. Le recours est listé dans le résumé de la CI, pour le rapport de
barrière.

Usage (CI)::

    PR_TITLE=... PR_BODY=... PR_LABELS='["fix"]' \\
        scripts/check-regression-on-base.py --base-sha A --head-sha B
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ESCAPE_LABEL = "no-regression-test"
_FIX_TITLE = re.compile(r"^fix(\([^)]*\))?!?:", re.IGNORECASE)
_JUSTIFICATION = re.compile(rf"^\s*{ESCAPE_LABEL}\s*:\s*(\S.*)$", re.IGNORECASE | re.MULTILINE)
_MIN_JUSTIFICATION = 10
#: Codes pytest : 0 tout passe, 5 rien collecté. Tout autre code est un échec
#: (1 test rouge, 2 erreur de collecte : ImportError d'un symbole que le
#: correctif introduit, c'est bien un test qui ne passe pas sur la base).
_NOT_FAILING = (0, 5)


@dataclass
class Verdict:
    ok: bool
    status: str  # skipped | escaped | red-on-base | green-on-base | no-tests | bad-escape
    message: str
    tests: list[str] = field(default_factory=list)
    justification: str = ""


def is_fix_title(title: str) -> bool:
    return bool(_FIX_TITLE.match(title.strip()))


def escape_justification(labels: list[str], body: str) -> tuple[bool, str]:
    """(étiquette posée, justification valable ou chaîne vide)."""
    labelled = ESCAPE_LABEL in labels
    match = _JUSTIFICATION.search(body or "")
    text = match.group(1).strip() if match else ""
    return labelled, text if len(text) >= _MIN_JUSTIFICATION else ""


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} : {done.stderr.strip()}")
    return done.stdout


def changed_test_files(repo: Path, base: str, head: str) -> tuple[list[str], list[str]]:
    """(tests `test_*.py` ajoutés/modifiés, autres fichiers sous tests/ touchés)."""
    out = _git(repo, "diff", "--name-only", "--diff-filter=AM", f"{base}...{head}", "--", "tests")
    files = [line for line in out.splitlines() if line.endswith(".py")]
    tests = [f for f in files if Path(f).name.startswith("test_")]
    return tests, [f for f in files if f not in tests]


def run_tests_on_base(repo: Path, base: str, head: str, tests: list[str], support: list[str]) -> int:
    """Exécute `tests` (version `head`) contre le code de `base`. Renvoie le code pytest."""
    tmp = Path(tempfile.mkdtemp(prefix="regression-on-base-", dir=os.environ.get("RUNNER_TEMP")))
    tree = tmp / "base"
    _git(repo, "worktree", "add", "--detach", str(tree), base)
    try:
        for rel in [*tests, *support]:
            target = tree / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_git(repo, "show", f"{head}:{rel}"), encoding="utf-8")
        env = {**os.environ, "PYTHONPATH": str(tree / "src")}
        done = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=line", *tests],
            cwd=tree,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        print(done.stdout[-4000:])
        return done.returncode
    finally:
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "remove", "--force", str(tree)],
            capture_output=True,
            check=False,
        )
        shutil.rmtree(tmp, ignore_errors=True)


def evaluate(repo: Path, base: str, head: str, title: str, body: str, labels: list[str]) -> Verdict:
    if not is_fix_title(title):
        return Verdict(True, "skipped", "PR non `fix:` : rejeu sans objet.")
    labelled, why = escape_justification(labels, body)
    if labelled and why:
        return Verdict(True, "escaped", f"Échappatoire `{ESCAPE_LABEL}` : {why}", justification=why)
    if labelled:
        return Verdict(
            False,
            "bad-escape",
            f"L'étiquette `{ESCAPE_LABEL}` exige une ligne `{ESCAPE_LABEL}: <justification>` "
            f"(au moins {_MIN_JUSTIFICATION} caractères) dans la description de la PR.",
        )
    tests, support = changed_test_files(repo, base, head)
    if not tests:
        return Verdict(
            False,
            "no-tests",
            "PR `fix:` sans test ajouté ou modifié. Ajoutez le test de régression, "
            f"ou posez `{ESCAPE_LABEL}` avec une justification.",
        )
    code = run_tests_on_base(repo, base, head, tests, support)
    if code in _NOT_FAILING:
        return Verdict(
            False,
            "green-on-base",
            f"Les tests de la PR passent déjà sur la base (pytest code {code}) : ils ne prouvent pas le correctif.",
            tests,
        )
    return Verdict(True, "red-on-base", f"Au moins un test échoue sur la base (pytest code {code}).", tests)


def render_summary(v: Verdict) -> str:
    lines = [f"### Rejeu du test de régression sur la base : {'OK' if v.ok else 'ÉCHEC'}", "", v.message]
    if v.status == "escaped":
        lines += ["", f"Recours à l'échappatoire `{ESCAPE_LABEL}` (à compter dans le rapport de barrière)."]
    if v.tests:
        lines += ["", "Tests rejoués :", *[f"- `{t}`" for t in v.tests]]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--repo", default=".")
    args = parser.parse_args()
    verdict = evaluate(
        Path(args.repo),
        args.base_sha,
        args.head_sha,
        os.environ.get("PR_TITLE", ""),
        os.environ.get("PR_BODY", ""),
        json.loads(os.environ.get("PR_LABELS", "[]")),
    )
    summary = render_summary(verdict)
    print(summary)
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(summary)
    return 0 if verdict.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
