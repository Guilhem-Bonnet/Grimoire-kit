"""CI : suite complète sur trois systèmes, sans ``-x``, e2e sans skip silencieux (D-10).

Avant : Windows ne lançait qu'un sous-ensemble de six cibles, ``-x`` (ligne de
commande et ``addopts``) arrêtait chaque job au premier échec, et le job e2e
laissait passer au vert un harnais entièrement skippé.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import shutil
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from ruamel.yaml import YAML

_ROOT = Path(__file__).resolve().parents[2]
_CI = _ROOT / ".github" / "workflows" / "ci-sdk.yml"
_E2E = _ROOT / "tests" / "e2e"
_STRICT = _E2E / "_strict.py"

pytestmark = pytest.mark.skipif(not _CI.is_file(), reason="dépôt source uniquement")


def _job(name: str) -> dict[str, Any]:
    return YAML(typ="safe").load(_CI.read_text(encoding="utf-8"))["jobs"][name]


def _run_steps(job: dict[str, Any]) -> str:
    return "\n".join(str(s.get("run", "")) for s in job["steps"])


def _pytest_steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in job["steps"] if "pytest" in str(s.get("run", ""))]


def _strict_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("e2e_strict_under_test", _STRICT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_matrix_runs_the_three_systems() -> None:
    oses = set(_job("test")["strategy"]["matrix"]["os"])
    assert {"ubuntu-latest", "windows-latest", "macos-latest"} <= oses


def test_one_command_for_every_system_no_windows_subset() -> None:
    """La commande pytest est la même sur les trois systèmes : aucun sous-ensemble Windows."""
    steps = _pytest_steps(_job("test"))
    assert len(steps) == 1, [s.get("name") for s in steps]
    run = str(steps[0]["run"])
    assert "runner.os" not in run and "Windows" not in run
    assert "tests/unit/" in run
    assert "test_app.py::" not in run


def test_no_fail_fast_flag_in_the_test_job() -> None:
    run = _run_steps(_job("test")).split()
    assert "-x" not in run
    assert not any(tok.startswith("--maxfail") for tok in run)


def test_pytest_addopts_does_not_stop_at_first_failure() -> None:
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    addopts = data["tool"]["pytest"]["ini_options"]["addopts"].split()
    assert "-x" not in addopts
    assert not any(tok.startswith("--maxfail") for tok in addopts)


def test_test_job_uses_xdist() -> None:
    assert "-n auto" in _run_steps(_job("test"))


def test_xdist_distributes_by_file() -> None:
    """``--dist load`` (défaut) éparpille les tests d'un même fichier entre workers.

    Rejoué sur 84ecc61a : sur la suite complète, ``-n 8`` donne 21 échecs contre 10
    en série (état d'un module de portée ``module`` partagé, mémoire transverse
    polluée d'un fichier à l'autre) ; ``-n 8 --dist loadfile`` revient exactement
    aux 10 échecs de la série. Le choix est figé tant que l'ordre n'est pas
    indépendant des tests.
    """
    run = _run_steps(_job("test")).split()
    assert "--dist" in run, "xdist sans --dist : répartition par test, état partagé pollué"
    assert run[run.index("--dist") + 1] == "loadfile"


def test_e2e_job_is_strict() -> None:
    step = next(s for s in _job("e2e")["steps"] if "tests/e2e" in str(s.get("run", "")))
    assert step["env"]["GRIMOIRE_E2E_STRICT"] == "1"


@pytest.mark.parametrize(("value", "expected"), [("1", True), ("true", True), ("YES", True), ("", False), ("0", False)])
def test_strict_mode_reads_the_environment(value: str, expected: bool) -> None:
    assert _strict_module().strict_mode({"GRIMOIRE_E2E_STRICT": value}) is expected
    assert _strict_module().strict_mode({}) is False


def test_a_skip_becomes_a_failure_under_strict_mode(tmp_path: Path) -> None:
    """Vrai pytest dans un répertoire jeté : le même crochet que celui du harnais."""
    (tmp_path / "conftest.py").write_text(
        textwrap.dedent(
            f"""
            import importlib.util
            import pytest
            spec = importlib.util.spec_from_file_location("_strict", r"{_STRICT}")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            @pytest.hookimpl(hookwrapper=True)
            def pytest_runtest_makereport(item, call):
                outcome = yield
                mod.promote_skip(outcome.get_result())
            """
        ),
        encoding="utf-8",
    )
    (tmp_path / "test_sample.py").write_text(
        "import pytest\ndef test_skipped():\n    pytest.skip('chromium absent')\n", encoding="utf-8"
    )

    def run(strict: str) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "GRIMOIRE_E2E_STRICT": strict, "PYTHONUTF8": "1"}  # sortie en UTF-8 sous Windows aussi
        return subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts=", str(tmp_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=tmp_path,
            timeout=120,
            check=False,
        )

    lax = run("0")
    assert lax.returncode == 0 and "1 skipped" in lax.stdout
    strict = run("1")
    assert strict.returncode == 1, strict.stdout
    assert "1 failed" in strict.stdout and "chromium absent" in strict.stdout


# --- Le vrai tests/e2e/conftest.py, pas une copie de son crochet -----------------
#
# Le test ci-dessus prouve ``promote_skip`` derrière un conftest réécrit à la main :
# retirer le branchement de ``tests/e2e/conftest.py`` ne le fait pas rougir. Les
# suivants copient le fichier réel (avec ``_strict.py`` et ``__init__.py``) dans un
# paquet jetable, y posent un faux ``playwright`` et lancent un vrai pytest.

_SAMPLE = "import pytest\n\ndef test_skipped():\n    pytest.skip('chromium absent')\n"


def _e2e_sandbox(tmp_path: Path, *, with_sync_api: bool) -> tuple[Path, Path]:
    """Paquet ``e2e`` jetable portant le conftest réel ; renvoie (racine, faux playwright)."""
    pkg = tmp_path / "e2e"
    pkg.mkdir()
    for name in ("__init__.py", "_strict.py", "conftest.py"):
        shutil.copy2(_E2E / name, pkg / name)
    (pkg / "test_sample.py").write_text(_SAMPLE, encoding="utf-8")
    fake = tmp_path / "fakepw"
    (fake / "playwright").mkdir(parents=True)
    (fake / "playwright" / "__init__.py").write_text("", encoding="utf-8")
    if with_sync_api:
        (fake / "playwright" / "sync_api.py").write_text(
            "Browser = Page = object\n\ndef sync_playwright():\n    raise RuntimeError('jamais appelé')\n",
            encoding="utf-8",
        )
    return pkg, fake


def _run_e2e(tmp_path: Path, fake: Path, strict: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "GRIMOIRE_E2E_STRICT": strict, "PYTHONPATH": str(fake), "PYTHONUTF8": "1"}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts=", "e2e"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=tmp_path,
        timeout=120,
        check=False,
    )


def test_real_e2e_conftest_turns_a_skip_into_a_failure(tmp_path: Path) -> None:
    _, fake = _e2e_sandbox(tmp_path, with_sync_api=True)
    lax = _run_e2e(tmp_path, fake, "0")
    assert lax.returncode == 0 and "1 skipped" in lax.stdout, lax.stdout
    strict = _run_e2e(tmp_path, fake, "1")
    assert strict.returncode == 1, strict.stdout
    assert "1 failed" in strict.stdout and "chromium absent" in strict.stdout
    assert "1 skipped" not in strict.stdout


def test_real_e2e_conftest_fails_without_playwright_in_strict_mode(tmp_path: Path) -> None:
    _, fake = _e2e_sandbox(tmp_path, with_sync_api=False)
    lax = _run_e2e(tmp_path, fake, "0")
    assert "Skipped: playwright absent" in lax.stdout + lax.stderr
    strict = _run_e2e(tmp_path, fake, "1")
    out = strict.stdout + strict.stderr
    assert strict.returncode != 0, out
    assert "No module named 'playwright.sync_api'" in out, out
    assert "Skipped: playwright absent" not in out, out


def test_e2e_conftest_wiring_is_structural() -> None:
    """Garde bon marché : le branchement existe dans le fichier réel, sous la bonne forme."""
    tree = ast.parse((_E2E / "conftest.py").read_text(encoding="utf-8"))
    imported = {a.name for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "_strict" for a in n.names}
    assert {"promote_skip", "strict_mode"} <= imported
    hooks = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "pytest_runtest_makereport"]
    assert len(hooks) == 1 and "promote_skip" in ast.unparse(hooks[0])
    # l'importorskip ne doit exister que derrière strict_mode()
    guarded = [n for n in tree.body if isinstance(n, ast.If) and "strict_mode" in ast.unparse(n.test)]
    assert guarded and "importorskip" in ast.unparse(guarded[0].orelse[0])
    assert "importorskip" not in ast.unparse(guarded[0].body[0])
    top_level_skips = [n for n in tree.body if isinstance(n, ast.Expr) and "importorskip" in ast.unparse(n)]
    assert not top_level_skips


def test_windows_et_macos_passent_la_suite_complete_hors_pr_seulement() -> None:
    """2026-10-27 : chaque réalignement de PR relançait ~45 min de Windows. Sur une PR, Windows et macOS rendent leur
    check requis sans la suite ; sur main, la nuit et à la demande, ils la passent. Ubuntu la passe toujours."""
    workflow = YAML(typ="safe").load(_CI.read_text(encoding="utf-8"))
    triggers = workflow.get("on") or workflow.get(True)
    assert "schedule" in triggers and "workflow_dispatch" in triggers and "push" in triggers
    job = workflow["jobs"]["test"]
    gate = job["env"]["FULL_SUITE"]
    assert "matrix.os == 'ubuntu-latest'" in gate and "github.event_name != 'pull_request'" in gate
    suite = next(s for s in job["steps"] if "pytest tests/unit/" in str(s.get("run", "")))
    assert suite.get("if") == "env.FULL_SUITE == 'true'"
    assert {"windows-latest", "macos-latest"} <= set(job["strategy"]["matrix"]["os"])  # le nom du check requis reste
