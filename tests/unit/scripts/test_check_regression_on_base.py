"""Le garde « rejeu sur la base » refuse-t-il vraiment ?

Chaque test construit un vrai dépôt git (base buggée, tête corrigée) et vérifie
que le garde refuse un `fix:` dont les tests passent déjà sur la base, ainsi
qu'un `fix:` sans test ; le cas nominal sert de contre-épreuve.
"""

from __future__ import annotations

import importlib.util
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
