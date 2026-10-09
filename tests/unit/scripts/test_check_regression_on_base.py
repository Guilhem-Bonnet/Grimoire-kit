"""Le garde « rejeu sur la base » refuse-t-il vraiment ?

Chaque test construit un vrai dépôt git (base buggée, tête corrigée) et vérifie
que le garde refuse un `fix:` dont les tests passent déjà sur la base, ainsi
qu'un `fix:` sans test ; le cas nominal sert de contre-épreuve.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "check_regression_on_base",
    Path(__file__).resolve().parents[3] / "scripts" / "check-regression-on-base.py",
)
assert _SPEC is not None and _SPEC.loader is not None
guard = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = guard
_SPEC.loader.exec_module(guard)

FIX = "fix(core): corrige le calcul"


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return done.stdout.strip()


def _commit(repo: Path, files: dict[str, str]) -> str:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(repo, "add", *files)
    _git(repo, "commit", "-q", "-m", "c")
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "r"
    root.mkdir()
    _git(root, "init", "-q")
    base = _commit(root, {"src/calc.py": "def add(a, b):\n    return a - b\n"})
    return root, base


FIXED = "def add(a, b):\n    return a + b\n"
RED_TEST = "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"
GREEN_TEST = "from calc import add\n\ndef test_other():\n    assert add(1, 1) is not None\n"


def _run(root: Path, base: str, head: str, title: str = FIX, body: str = "", labels: list[str] | None = None):
    return guard.evaluate(root, base, head, title, body, labels or [])


def test_fix_whose_test_passes_on_base_is_refused(repo):
    root, base = repo
    head = _commit(root, {"src/calc.py": FIXED, "tests/test_calc.py": GREEN_TEST})
    verdict = _run(root, base, head)
    assert not verdict.ok and verdict.status == "green-on-base"


def test_fix_whose_test_fails_on_base_is_accepted(repo):
    root, base = repo
    head = _commit(root, {"src/calc.py": FIXED, "tests/test_calc.py": RED_TEST})
    verdict = _run(root, base, head)
    assert verdict.ok and verdict.status == "red-on-base"
    assert verdict.tests == ["tests/test_calc.py"]


def test_fix_without_test_is_refused(repo):
    root, base = repo
    head = _commit(root, {"src/calc.py": FIXED})
    assert _run(root, base, head).status == "no-tests"


def test_label_without_justification_is_refused(repo):
    root, base = repo
    head = _commit(root, {"src/calc.py": FIXED})
    verdict = _run(root, base, head, labels=["no-regression-test"])
    assert not verdict.ok and verdict.status == "bad-escape"


def test_label_with_justification_passes_and_is_listed(repo):
    root, base = repo
    head = _commit(root, {"src/calc.py": FIXED})
    body = "Contexte\n\nno-regression-test: correctif de documentation généré, non testable\n"
    verdict = _run(root, base, head, body=body, labels=["no-regression-test"])
    assert verdict.ok and verdict.status == "escaped"
    assert "non testable" in guard.render_summary(verdict)
    assert "échappatoire" in guard.render_summary(verdict).lower()


def test_justification_without_label_does_not_escape(repo):
    root, base = repo
    head = _commit(root, {"src/calc.py": FIXED})
    body = "no-regression-test: une justification assez longue\n"
    assert _run(root, base, head, body=body).status == "no-tests"


def test_non_fix_pr_is_skipped(repo):
    root, base = repo
    head = _commit(root, {"src/calc.py": FIXED})
    assert _run(root, base, head, title="feat(core): autre chose").status == "skipped"


@pytest.mark.parametrize(
    "title,expected",
    [
        ("fix: x", True),
        ("fix(a): x", True),
        ("fix!: x", True),
        ("Fix(a)!: x", True),
        ("feat: fix x", False),
        ("fixup: x", False),
        ("docs: fix", False),
    ],
)
def test_title_detection(title, expected):
    assert guard.is_fix_title(title) is expected


# ── Revue D-14 : fixtures, tête verte, rouge attribuable, main(), workflow ──

GOOD = "def add(a, b):\n    return a + b\n"


@pytest.fixture
def good_repo(tmp_path: Path) -> tuple[Path, str]:
    """Base dont le code est déjà correct : tout rouge vient du garde ou du test."""
    root = tmp_path / "g"
    root.mkdir()
    _git(root, "init", "-q")
    return root, _commit(root, {"src/calc.py": GOOD})


def test_json_fixture_is_copied_so_a_green_test_stays_green(good_repo):
    root, base = good_repo
    test = (
        "import json, pathlib\nfrom calc import add\n\n"
        "def test_case():\n"
        "    data = json.loads((pathlib.Path(__file__).parent / 'fixtures' / 'case.json').read_text())\n"
        "    assert add(*data) == 3\n"
    )
    head = _commit(root, {"tests/fixtures/case.json": "[1, 2]", "tests/test_case.py": test})
    verdict = _run(root, base, head)
    assert verdict.status == "green-on-base" and not verdict.ok


def test_binary_fixture_is_copied_byte_for_byte(good_repo):
    root, base = good_repo
    blob = bytes(range(256))
    (root / "tests").mkdir()
    (root / "tests" / "blob.bin").write_bytes(blob)
    test = (
        "import pathlib\n\n"
        "def test_blob():\n"
        "    assert (pathlib.Path(__file__).parent / 'blob.bin').read_bytes() == bytes(range(256))\n"
    )
    head = _commit(root, {"tests/test_blob.py": test})
    _git(root, "add", "tests/blob.bin")
    _git(root, "commit", "-q", "--amend", "--no-edit")
    head = _git(root, "rev-parse", "HEAD")
    assert _run(root, base, head).status == "green-on-base"


def test_always_failing_test_is_refused_as_red_on_head(good_repo):
    root, base = good_repo
    head = _commit(root, {"tests/test_x.py": "def test_x():\n    assert False\n"})
    verdict = _run(root, base, head)
    assert not verdict.ok and verdict.status == "red-on-head"


def test_syntax_error_in_test_is_refused(good_repo):
    root, base = good_repo
    head = _commit(root, {"tests/test_x.py": "def test_x(:\n"})
    verdict = _run(root, base, head)
    assert not verdict.ok and verdict.status == "red-on-head"


def test_support_conftest_failing_on_base_is_inconclusive(good_repo):
    root, base = good_repo
    head = _commit(
        root,
        {
            "src/calc.py": GOOD + "HELPER = 1\n",
            "tests/conftest.py": "from calc import HELPER\n",
            "tests/test_calc.py": "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        },
    )
    verdict = _run(root, base, head)
    assert not verdict.ok and verdict.status == "inconclusive"


def test_real_regression_still_accepted_with_green_head(repo):
    root, base = repo
    head = _commit(root, {"src/calc.py": FIXED, "tests/test_calc.py": RED_TEST})
    verdict = _run(root, base, head)
    assert verdict.ok and verdict.status == "red-on-base"


def _main(monkeypatch, root: Path, base: str, head: str, env: dict[str, str], summary: Path):
    monkeypatch.setattr(sys, "argv", ["g", "--repo", str(root), "--base-sha", base, "--head-sha", head])
    for key in ("PR_TITLE", "PR_BODY", "PR_LABELS"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    return guard.main()


def test_main_exit_code_is_one_for_fix_without_test(repo, monkeypatch, tmp_path):
    root, base = repo
    head = _commit(root, {"src/calc.py": FIXED})
    summary = tmp_path / "summary.md"
    code = _main(monkeypatch, root, base, head, {"PR_TITLE": FIX, "PR_LABELS": "[]"}, summary)
    assert code == 1
    assert "ÉCHEC" in summary.read_text(encoding="utf-8")


def test_main_exit_code_zero_with_escape_and_summary_lists_it(repo, monkeypatch, tmp_path):
    root, base = repo
    head = _commit(root, {"src/calc.py": FIXED})
    summary = tmp_path / "summary.md"
    env = {
        "PR_TITLE": FIX,
        "PR_BODY": "no-regression-test: correctif de documentation non testable",
        "PR_LABELS": json.dumps(["no-regression-test"]),
    }
    assert _main(monkeypatch, root, base, head, env, summary) == 0
    assert "Recours à l'échappatoire `no-regression-test`" in summary.read_text(encoding="utf-8")


def test_main_label_lookalike_is_not_the_escape_label(repo, monkeypatch, tmp_path):
    root, base = repo
    head = _commit(root, {"src/calc.py": FIXED})
    env = {
        "PR_TITLE": FIX,
        "PR_BODY": "no-regression-test: correctif de documentation non testable",
        "PR_LABELS": json.dumps(["no-regression-test-x"]),
    }
    assert _main(monkeypatch, root, base, head, env, tmp_path / "s.md") == 1


def test_workflow_runs_the_guard_from_the_base_sha():
    text = (Path(__file__).resolve().parents[3] / ".github" / "workflows" / "regression-on-base.yml").read_text(
        encoding="utf-8"
    )
    assert "BASE_SHA: ${{ github.event.pull_request.base.sha }}" in text
    assert 'git show "$BASE_SHA:scripts/check-regression-on-base.py"' in text
    assert "--verify-sha" in text and "MERGE_SHA: ${{ github.sha }}" in text
    assert "python scripts/check-regression-on-base.py" not in text


# ── Revue D-14, 2e tour : renommages, amorçage, conftest, branche en retard ──


def test_fixture_moved_with_git_mv_is_still_copied(good_repo):
    """D14-T1-1 : une fixture renommée ressortait en `R` et n'était pas recopiée."""
    root, base = good_repo
    fixture = "[1, 2]\n" * 20
    base = _commit(root, {"tests/fixtures/case.json": fixture})
    test = (
        "import json, pathlib\nfrom calc import add\n\n"
        "def test_case():\n"
        "    data = json.loads((pathlib.Path(__file__).parent / 'data' / 'case.json').read_text().splitlines()[0])\n"
        "    assert add(*data) == 3\n"
    )
    (root / "tests" / "data").mkdir()
    _git(root, "mv", "tests/fixtures/case.json", "tests/data/case.json")
    (root / "tests" / "test_case.py").write_text(test, encoding="utf-8")
    _git(root, "add", "tests/test_case.py")
    _git(root, "commit", "-q", "-m", "c")
    head = _git(root, "rev-parse", "HEAD")
    verdict = _run(root, base, head)
    assert verdict.status == "green-on-base", verdict.message


def test_renamed_and_edited_test_is_replayed(repo):
    """D14-T1-2 : un test renommé puis modifié ressortait en `R` et était ignoré."""
    root, _ = repo
    old = "from calc import add\n\ndef test_old():\n    assert add(1, 1) is not None\n    # " + "x" * 200 + "\n"
    base = _commit(root, {"tests/test_old.py": old})
    _git(root, "mv", "tests/test_old.py", "tests/test_new.py")
    new = old.replace("is not None", "== 2")
    (root / "tests" / "test_new.py").write_text(new, encoding="utf-8")
    (root / "src" / "calc.py").write_text(FIXED, encoding="utf-8")
    _git(root, "add", "tests/test_new.py", "src/calc.py")
    _git(root, "commit", "-q", "-m", "c")
    head = _git(root, "rev-parse", "HEAD")
    verdict = _run(root, base, head)
    assert verdict.ok and verdict.status == "red-on-base", verdict.message
    assert verdict.tests == ["tests/test_new.py"]


def _workflow_run_script() -> str:
    import yaml

    path = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "regression-on-base.yml"
    steps = yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"]["replay"]["steps"]
    return next(s["run"] for s in steps if s.get("name", "").startswith("Replay"))


def _run_workflow_step(
    root: Path,
    tmp_path: Path,
    *,
    base: str,
    head: str,
    title: str,
    merge: str | None = None,
    use_current_python: bool = False,
    script: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Exécute le `run` du pas Replay sous `bash -e`, comme le fait Actions."""
    summary = tmp_path / "summary.md"
    runner = tmp_path / "runner"
    runner.mkdir(exist_ok=True)
    env = {
        "PATH": __import__("os").environ["PATH"],
        "RUNNER_TEMP": str(runner),
        "GITHUB_STEP_SUMMARY": str(summary),
        "BASE_SHA": base,
        "HEAD_SHA": head,
        "MERGE_SHA": merge or head,
        "PR_TITLE": title,
        "PR_BODY": "",
        "PR_LABELS": "[]",
    }
    run = script if script is not None else _workflow_run_script()
    if use_current_python:
        run = run.replace("python ", f"{sys.executable} ")
    done = subprocess.run(
        ["bash", "-e", "-c", run],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return done, summary


def test_workflow_step_bootstraps_when_the_pr_itself_adds_the_guard(good_repo, tmp_path):
    """D14-T1-3 : sous `bash -e`, `git show base:guard` échouait avant tout verdict
    pour la PR qui introduit le garde. D14-T2-2 : l'amorçage n'est admis que dans ce cas."""
    root, base = good_repo  # base sans scripts/check-regression-on-base.py
    head = _commit(root, {"scripts/check-regression-on-base.py": "raise SystemExit(7)\n"})
    done, summary = _run_workflow_step(root, tmp_path, base=base, head=head, title="ci: autre chose")
    assert done.returncode == 0, done.stderr
    assert "amorçage" in summary.read_text(encoding="utf-8")


def test_workflow_step_fails_when_the_guard_vanished_from_base(good_repo, tmp_path):
    """D14-T2-2 : base valide mais sans garde, que la PR n'ajoute pas (renommé ou
    supprimé sur main) : l'ancien pas sortait en 0 comme pour l'amorçage."""
    root, base = good_repo
    head = _commit(root, {"src/other.py": "X = 1\n"})
    done, summary = _run_workflow_step(root, tmp_path, base=base, head=head, title="fix: x")
    assert done.returncode != 0
    assert "amorçage" not in (summary.read_text(encoding="utf-8") if summary.exists() else "")


def test_workflow_step_fails_when_the_base_object_is_missing(good_repo, tmp_path):
    """D14-T2-2 : un `base.sha` absent du dépôt ne doit pas passer pour un amorçage."""
    root, _ = good_repo
    head = _commit(root, {"scripts/check-regression-on-base.py": "raise SystemExit(7)\n"})
    missing = "0" * 39 + "1"
    done, summary = _run_workflow_step(root, tmp_path, base=missing, head=head, title="fix: x")
    assert done.returncode != 0
    assert "amorçage" not in (summary.read_text(encoding="utf-8") if summary.exists() else "")


def test_workflow_step_still_runs_the_guard_from_base_when_present(good_repo, tmp_path):
    """Le garde exécuté est celui de `base.sha`, pas celui de la tête (D14-T2-3 :
    la tête porte un garde différent, qui sortirait en 0)."""
    root, _ = good_repo
    base = _commit(root, {"scripts/check-regression-on-base.py": "raise SystemExit(7)\n"})
    head = _commit(
        root,
        {"scripts/check-regression-on-base.py": "raise SystemExit(0)\n", "src/other.py": "X = 1\n"},
    )
    done, _ = _run_workflow_step(root, tmp_path, base=base, head=head, title="fix: x", use_current_python=True)
    assert done.returncode == 7, done.stderr


def test_workflow_step_run_from_the_checkout_would_be_caught(good_repo, tmp_path):
    """D14-T2-3 : contre-épreuve par mutation, le pas qui lirait le garde de la
    tête (`scripts/...` du checkout) ne rend plus 7 et le test ci-dessus le voit."""
    root, _ = good_repo
    base = _commit(root, {"scripts/check-regression-on-base.py": "raise SystemExit(7)\n"})
    head = _commit(root, {"scripts/check-regression-on-base.py": "raise SystemExit(0)\n"})
    mutant = _workflow_run_script().replace(
        'python "$RUNNER_TEMP/guard.py"', "python scripts/check-regression-on-base.py"
    )
    assert mutant != _workflow_run_script()
    done, _ = _run_workflow_step(
        root, tmp_path, base=base, head=head, title="fix: x", use_current_python=True, script=mutant
    )
    assert done.returncode == 0


def test_conftest_fixture_error_at_setup_counts_as_red_on_base(good_repo):
    """D14-T1-4 : limite documentée. Une fixture de conftest en erreur au setup est
    attribuée au testcase, donc rouge ; seul un conftest qui échoue à l'import
    donne `inconclusive`."""
    root, base = good_repo
    head = _commit(
        root,
        {
            "src/calc.py": GOOD + "HELPER = 1\n",
            "tests/conftest.py": (
                "import pytest\n\n@pytest.fixture(autouse=True)\n"
                "def _h():\n    from calc import HELPER\n    return HELPER\n"
            ),
            "tests/test_calc.py": "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        },
    )
    verdict = _run(root, base, head)
    assert verdict.status == "red-on-base"


def _stale_repo(tmp_path: Path) -> tuple[Path, str, str, str]:
    """Branche en retard et vraie fusion `git merge --no-ff` propre.

    main change l'API de `f` et met son test à jour ; la branche ne touche que
    `test_other`, loin de `test_f`. (root, base, head, merge).
    """
    root = tmp_path / "s"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    src = "def f(a):\n    return a\n\n\ndef add(a, b):\n    return a + b\n"
    test_f = (
        "from calc import f\n\n\ndef test_f():\n    assert f(1) == 1\n\n\n"
        "# separateur\n# separateur\n\n\ndef test_other():\n    assert 1 == 1\n"
    )
    fork = _commit(root, {"src/calc.py": src, "tests/test_f.py": test_f})
    base = _commit(
        root,
        {
            "src/calc.py": src.replace("def f(a):", "def f(a, b):"),
            "tests/test_f.py": test_f.replace("f(1) == 1", "f(1, 2) == 1"),
        },
    )
    _git(root, "checkout", "-q", "-b", "pr", fork)
    head = _commit(root, {"tests/test_f.py": test_f.replace("assert 1 == 1", "assert 2 == 2")})
    _git(root, "checkout", "-q", "main")
    _git(root, "merge", "-q", "--no-ff", "-m", "merge", "pr")  # propre, comme refs/pull/N/merge
    return root, base, head, _git(root, "rev-parse", "HEAD")


def test_stale_branch_tests_are_taken_from_the_merge_commit(tmp_path: Path):
    """D14-T2-1 : les tests rejoués sur la base viennent du commit de fusion. Pris à
    la tête, `test_f` (ancienne API) échouait sur la base : faux `red-on-base`."""
    root, base, head, merge = _stale_repo(tmp_path)
    verdict = guard.evaluate(root, base, head, FIX, "", [], verify=merge)
    assert not verdict.ok and verdict.status == "green-on-base", verdict.message


def test_stale_branch_head_alone_is_a_false_positive_without_verify(tmp_path: Path):
    """Caractérisation : sans `--verify-sha` le garde juge sur `head.sha` seul et
    se laisse tromper par la dérive ; d'où le commit de fusion dans le workflow."""
    root, base, head, _ = _stale_repo(tmp_path)
    assert _run(root, base, head).status == "red-on-base"


def test_stale_branch_real_regression_is_still_accepted(tmp_path: Path):
    """Contre-épreuve de D14-T2-1 : un vrai correctif d'une branche en retard passe."""
    root = tmp_path / "t"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    buggy = "def f(a):\n    return a\n\n\ndef add(a, b):\n    return a - b\n"
    fork = _commit(root, {"src/calc.py": buggy})
    base = _commit(root, {"src/calc.py": buggy.replace("def f(a):", "def f(a, b):")})
    _git(root, "checkout", "-q", "-b", "pr", fork)
    head = _commit(
        root,
        {
            "src/calc.py": buggy.replace("a - b", "a + b"),
            "tests/test_add.py": "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        },
    )
    _git(root, "checkout", "-q", "main")
    _git(root, "merge", "-q", "--no-ff", "-m", "merge", "pr")
    merge = _git(root, "rev-parse", "HEAD")
    verdict = guard.evaluate(root, base, head, FIX, "", [], verify=merge)
    assert verdict.ok and verdict.status == "red-on-base", verdict.message
