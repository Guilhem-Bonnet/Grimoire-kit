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
        scripts/check-regression-on-base.py --base-sha A --head-sha B [--verify-sha M]

Limites assumées : les tests rejoués et la tête vérifiée viennent de
`--verify-sha` (commit de fusion en CI), pas de `--head-sha` seul ; un rouge sur la base reste attribué aux
fichiers de la PR sans prouver la cause (par exemple une fixture de conftest
en erreur au setup compte comme rouge).
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
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

ESCAPE_LABEL = "no-regression-test"
_FIX_TITLE = re.compile(r"^fix(\([^)]*\))?!?:", re.IGNORECASE)
_JUSTIFICATION = re.compile(rf"^\s*{ESCAPE_LABEL}\s*:\s*(\S.*)$", re.IGNORECASE | re.MULTILINE)
_MIN_JUSTIFICATION = 10
#: Codes pytest : 0 tout passe, 5 rien collecté : la base ne prouve rien.
#: 1 : au moins un test en échec. 2 (collecte, interruption) : rouge seulement
#: si un testcase des fichiers de la PR est en échec ou en erreur (JUnit) ;
#: sinon `inconclusive`. 3 et 4 (erreur interne, usage) : toujours `inconclusive`.
_NOT_FAILING = (0, 5)
_RED_CODES = (1, 2)


@dataclass
class Verdict:
    ok: bool
    status: str  # skipped | escaped | red-on-base | green-on-base | red-on-head | inconclusive | no-tests | bad-escape
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
    """(tests `test_*.py` ajoutés/modifiés, autres fichiers d'appui sous tests/ touchés).

    L'appui couvre tout type de fichier (fixtures JSON, binaires, conftest) :
    les écarter ferait échouer le test sur la base pour une raison qui n'est
    pas le code testé. `--no-renames` : un fichier déplacé (fixture, test)
    ressort en `A` à sa destination au lieu d'un `R` que `AM` écarte.
    """
    out = _git(repo, "diff", "--name-only", "--no-renames", "--diff-filter=AM", f"{base}...{head}", "--", "tests")
    files = [line for line in out.splitlines() if line]
    return _split_tests(files)


def _split_tests(files: list[str]) -> tuple[list[str], list[str]]:
    tests = [f for f in files if f.endswith(".py") and Path(f).name.startswith("test_")]
    return tests, [f for f in files if f not in tests]


def _renamed_on_base(repo: Path, base: str, head: str) -> dict[str, str]:
    """Renommages faits par la base depuis le point de fourche : ancien -> nouveau chemin."""
    fork = _git(repo, "merge-base", base, head).strip()
    fields = _git(repo, "diff", "--name-status", "--find-renames", "-z", fork, base).split("\0")
    moved: dict[str, str] = {}
    i = 0
    while i < len(fields) and fields[i]:
        status = fields[i]
        if status.startswith(("R", "C")) and i + 2 < len(fields):
            if status.startswith("R"):
                moved[fields[i + 1]] = fields[i + 2]
            i += 3
        else:
            i += 2
    return moved


def follow_to_verify(repo: Path, base: str, head: str, verify: str, files: list[str]) -> list[str]:
    """Chemins de la tête (`base...head`) tels qu'ils existent dans le commit `verify`.

    Une fusion git propre applique la modification de la PR au fichier que la
    base a renommé : `tests/test_calc.py` de la tête devient `tests/test_arith.py`
    dans le commit de fusion. Un chemin absent de `verify` et sans renommage connu
    (supprimé par la base) est écarté.
    """
    if verify == head or not files:
        return files
    present = set(_git(repo, "ls-tree", "-r", "--name-only", verify, "--", "tests").splitlines())
    missing = [f for f in files if f not in present]
    moved = _renamed_on_base(repo, base, head) if missing else {}
    followed: list[str] = []
    for f in files:
        target = f if f in present else moved.get(f, "")
        if target in present and target not in followed:
            followed.append(target)
    return followed


def _module_id(rel: str) -> str:
    """`tests/a/test_x.py` -> `tests.a.test_x`, forme que pytest met dans le JUnit."""
    return rel.removesuffix(".py").replace("/", ".")


def _failing_modules(junit: Path, tests: list[str]) -> set[str]:
    """Fichiers de `tests` qui portent un testcase en échec ou en erreur."""
    try:
        root = ET.parse(junit).getroot()  # noqa: S314 - produit par notre propre pytest
    except (OSError, ET.ParseError):
        return set()
    hit: set[str] = set()
    for case in root.iter("testcase"):
        if case.find("failure") is None and case.find("error") is None:
            continue
        cid = f"{case.get('classname')}.{case.get('name')}" if case.get("classname") else case.get("name", "")
        for rel in tests:
            mod = _module_id(rel)
            if cid == mod or cid.startswith(mod + "."):
                hit.add(rel)
    return hit


def _add_tree(repo: Path, tree: Path, rev: str) -> None:
    _git(repo, "worktree", "add", "--detach", str(tree), rev)


def _pytest(tree: Path, tests: list[str], junit: Path) -> int:
    env = {**os.environ, "PYTHONPATH": str(tree / "src")}
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--tb=line",
            f"--junitxml={junit}",
            *tests,
        ],
        cwd=tree,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    print(done.stdout[-4000:])
    return done.returncode


@dataclass
class Replay:
    base_code: int
    base_failing: set[str]
    head_code: int | None  # None : non exécuté (la base n'a pas prouvé de rouge)


def run_replay(
    repo: Path, base: str, head: str, tests: list[str], support: list[str], verify: str | None = None
) -> Replay:
    """Tests de `verify` contre le code de `base`, puis (si rouge) contre `verify`.

    `verify` vaut `head` par défaut ; en CI c'est le commit de fusion que
    `actions/checkout` extrait. Les fichiers de test sont pris sur ce même
    commit (la liste vient de `base...head`) : une branche en retard sur main
    n'est ainsi ni validée contre un code que main a déjà changé, ni refusée
    ou acceptée à cause d'un test qui n'est plus celui qui sera fusionné.
    """
    tmp = Path(tempfile.mkdtemp(prefix="regression-on-base-", dir=os.environ.get("RUNNER_TEMP")))
    trees: list[Path] = []
    try:
        btree = tmp / "base"
        _add_tree(repo, btree, base)
        trees.append(btree)
        # Checkout git : binaire exact, fixtures non corrompues. Les fichiers
        # viennent de `verify` (le commit de fusion en CI), pas de `head` : sur
        # une branche en retard, le test fusionné peut différer de celui de la
        # tête (main l'a mis à jour) et ne doit pas échouer sur la base par dérive.
        _git(btree, "checkout", verify or head, "--", *tests, *support)
        code = _pytest(btree, tests, tmp / "base.xml")
        failing = _failing_modules(tmp / "base.xml", tests)
        head_code: int | None = None
        if code in _RED_CODES and failing:
            htree = tmp / "head"
            _add_tree(repo, htree, verify or head)
            trees.append(htree)
            head_code = _pytest(htree, tests, tmp / "head.xml")
        return Replay(code, failing, head_code)
    finally:
        for tree in trees:
            subprocess.run(
                ["git", "-C", str(repo), "worktree", "remove", "--force", str(tree)],
                capture_output=True,
                check=False,
            )
        shutil.rmtree(tmp, ignore_errors=True)


def _inconclusive_git(exc: RuntimeError, tests: list[str] | None = None) -> Verdict:
    """Une erreur git ne sort jamais en trace : le résumé du job doit être écrit."""
    return Verdict(
        False,
        "inconclusive",
        f"Rejeu impossible, git a échoué (fichier renommé ou supprimé sur la base, objet absent ?) : {exc}",
        tests or [],
    )


def evaluate(
    repo: Path,
    base: str,
    head: str,
    title: str,
    body: str,
    labels: list[str],
    verify: str | None = None,
) -> Verdict:
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
    try:
        tests, support = changed_test_files(repo, base, head)
        if verify and verify != head:
            tests, support = _split_tests(follow_to_verify(repo, base, head, verify, [*tests, *support]))
    except RuntimeError as exc:
        return _inconclusive_git(exc)
    if not tests:
        return Verdict(
            False,
            "no-tests",
            "PR `fix:` sans test ajouté ou modifié. Ajoutez le test de régression, "
            f"ou posez `{ESCAPE_LABEL}` avec une justification.",
        )
    try:
        replay = run_replay(repo, base, head, tests, support, verify)
    except RuntimeError as exc:
        return _inconclusive_git(exc, tests)
    code = replay.base_code
    if code in _NOT_FAILING:
        return Verdict(
            False,
            "green-on-base",
            f"Les tests de la PR passent déjà sur la base (pytest code {code}) : ils ne prouvent pas le correctif.",
            tests,
        )
    if replay.head_code is None:
        return Verdict(
            False,
            "inconclusive",
            f"Rouge sur la base sans preuve exploitable (pytest code {code}) : aucun test de la PR "
            "n'est en échec ou en erreur (conftest, usage, interruption). Le rouge ne vient pas du code testé.",
            tests,
        )
    if replay.head_code != 0:
        return Verdict(
            False,
            "red-on-head",
            f"Les tests de la PR échouent aussi sur sa tête{' (commit de fusion)' if verify else ''} "
            f"(pytest code {replay.head_code}) : "
            "un test cassé n'est pas une preuve de régression.",
            tests,
        )
    return Verdict(
        True,
        "red-on-base",
        f"Au moins un test échoue sur la base (pytest code {code}) et la tête est verte.",
        tests,
    )


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
    parser.add_argument(
        "--verify-sha",
        default=None,
        help="commit sur lequel les tests doivent être verts (commit de fusion en CI) ; défaut : --head-sha",
    )
    parser.add_argument("--repo", default=".")
    args = parser.parse_args()
    verdict = evaluate(
        Path(args.repo),
        args.base_sha,
        args.head_sha,
        os.environ.get("PR_TITLE", ""),
        os.environ.get("PR_BODY", ""),
        json.loads(os.environ.get("PR_LABELS", "[]")),
        verify=args.verify_sha,
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
