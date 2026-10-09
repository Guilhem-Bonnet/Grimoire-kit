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
    assert "git show" in text and "pull_request.base.sha }}:scripts/check-regression-on-base.py" in text
    assert "python scripts/check-regression-on-base.py" not in text
