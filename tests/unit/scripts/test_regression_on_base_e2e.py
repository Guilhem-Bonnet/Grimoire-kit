"""Le rejeu sur la base reconnaît-il un test E2E (#1148) ?

Le job « Replay regression test on base » installe `.[dev]` sans navigateur :
rejouer un test sous `tests/e2e/` y produit un faux rouge. Le garde doit donc
rendre « non rejouable ici », sans échec, quand les seuls tests de la PR sont
des tests E2E ; sur une PR mixte, il ne rejoue que les tests hors E2E et le dit.

Chaque test construit un vrai dépôt git (base buggée, tête corrigée), comme
`test_check_regression_on_base.py`.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "check_regression_on_base_e2e",
    Path(__file__).resolve().parents[3] / "scripts" / "check-regression-on-base.py",
)
assert _SPEC is not None and _SPEC.loader is not None
guard = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = guard
_SPEC.loader.exec_module(guard)

FIX = "fix(core): corrige le calcul"
FIXED = "def add(a, b):\n    return a + b\n"
RED_TEST = "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"
#: Échouerait si le garde le rejouait : c'est ce qu'on veut interdire.
E2E_TEST = "def test_e2e():\n    assert False\n"


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
    """Base buggée : un test de régression doit y échouer."""
    root = tmp_path / "r"
    root.mkdir()
    _git(root, "init", "-q")
    base = _commit(root, {"src/calc.py": "def add(a, b):\n    return a - b\n"})
    return root, base


def _run(root: Path, base: str, head: str):
    return guard.evaluate(root, base, head, FIX, "", [])


def test_e2e_path_detection():
    assert guard.is_e2e_test("tests/e2e/test_app.py")
    assert guard.is_e2e_test("tests/e2e/sub/test_app.py")
    assert not guard.is_e2e_test("tests/e2e_helpers/test_app.py")
    assert not guard.is_e2e_test("tests/unit/test_app.py")


def test_only_e2e_tests_is_not_replayable_and_does_not_fail(repo):
    """Le faux rouge de #1148 : le test E2E ne peut pas prouver sa régression ici."""
    root, base = repo
    head = _commit(root, {"src/calc.py": FIXED, "tests/e2e/test_app.py": E2E_TEST})
    verdict = _run(root, base, head)
    assert verdict.ok, verdict.message
    assert verdict.status == "not-replayable"
    assert "tests/e2e/" in verdict.message
    assert verdict.tests == ["tests/e2e/test_app.py"]


def test_not_replayable_summary_says_so(repo):
    root, base = repo
    head = _commit(root, {"src/calc.py": FIXED, "tests/e2e/test_app.py": E2E_TEST})
    summary = guard.render_summary(_run(root, base, head))
    assert "NON REJOUÉ" in summary
    assert "navigateur" in summary
    assert "Tests concernés" in summary
    assert "tests/e2e/test_app.py" in summary


def test_mixed_pr_replays_only_the_unit_test(repo):
    """Sans filtrage, le test E2E rouge ferait conclure `red-on-head`."""
    root, base = repo
    head = _commit(
        root,
        {
            "src/calc.py": FIXED,
            "tests/e2e/test_app.py": E2E_TEST,
            "tests/test_calc.py": RED_TEST,
        },
    )
    verdict = _run(root, base, head)
    assert verdict.ok, verdict.message
    assert verdict.status == "red-on-base"
    assert verdict.tests == ["tests/test_calc.py"]
    assert "E2E non rejoué" in verdict.message
    assert "tests/e2e/test_app.py" in verdict.message


def test_main_exits_zero_when_all_tests_are_e2e(repo, monkeypatch, tmp_path):
    root, base = repo
    head = _commit(root, {"src/calc.py": FIXED, "tests/e2e/test_app.py": E2E_TEST})
    summary = tmp_path / "summary.md"
    monkeypatch.setattr(sys, "argv", ["g", "--repo", str(root), "--base-sha", base, "--head-sha", head])
    monkeypatch.setenv("PR_TITLE", FIX)
    monkeypatch.setenv("PR_BODY", "")
    monkeypatch.setenv("PR_LABELS", "[]")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert guard.main() == 0
    assert "NON REJOUÉ" in summary.read_text(encoding="utf-8")
