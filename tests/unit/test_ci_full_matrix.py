"""CI : suite complète sur trois systèmes, sans ``-x``, e2e sans skip silencieux (D-10).

Avant : Windows ne lançait qu'un sous-ensemble de six cibles, ``-x`` (ligne de
commande et ``addopts``) arrêtait chaque job au premier échec, et le job e2e
laissait passer au vert un harnais entièrement skippé.
"""

from __future__ import annotations

import importlib.util
import os
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
_STRICT = _ROOT / "tests" / "e2e" / "_strict.py"

pytestmark = pytest.mark.skipif(not _CI.is_file(), reason="dépôt source uniquement")


def _job(name: str) -> dict[str, Any]:
    return YAML(typ="safe").load(_CI.read_text(encoding="utf-8"))["jobs"][name]


def _run_steps(job: dict[str, Any]) -> str:
    return "\n".join(str(s.get("run", "")) for s in job["steps"])


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
    run = _run_steps(_job("test"))
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
        env = {**os.environ, "GRIMOIRE_E2E_STRICT": strict}
        return subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts=", str(tmp_path)],
            capture_output=True, text=True, encoding="utf-8", env=env, cwd=tmp_path, timeout=120, check=False,
        )

    lax = run("0")
    assert lax.returncode == 0 and "1 skipped" in lax.stdout
    strict = run("1")
    assert strict.returncode == 1, strict.stdout
    assert "1 failed" in strict.stdout and "chromium absent" in strict.stdout
