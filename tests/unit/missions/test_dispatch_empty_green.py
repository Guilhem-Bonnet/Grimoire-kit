"""Vert vide : un exécuteur de tests qui rend 0 sans test exécuté est « unrunnable » (W1-04).

Constat : ``_classify_unrunnable`` ne reconnaissait que le code de sortie 5 de
pytest ; vitest, go, cargo, unittest et mocha rendent 0 quand rien n'est
collecté, et le check passait vert. Les exécuteurs sont simulés par de vraies
commandes shell qui impriment la sortie réelle de l'outil puis sortent en 0.
"""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest

from grimoire.missions.dispatch import _classify_unrunnable, _run_checks


def _check(tmp_path: Path, printed: str, *, stream: str = "stdout"):
    """Un vrai ``--check`` qui imprime *printed* sur *stream* et sort en 0."""
    redirect = " >&2" if stream == "stderr" else ""
    cmd = f"printf %s {shlex.quote(printed)}{redirect}"
    (result,) = _run_checks((cmd,), project_root=tmp_path)
    return result


@pytest.mark.parametrize(
    ("label", "printed", "stream"),
    [
        ("vitest", " RUN  v1.6.0 /app\n\nNo test files found, exiting with code 1\n", "stdout"),
        ("jest", "No tests found, exiting with code 0\n", "stdout"),
        ("go", "?   \texample.com/app\t[no test files]\n", "stdout"),
        (
            "cargo",
            "running 0 tests\n\ntest result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out\n",
            "stdout",
        ),
        ("unittest", "\n----------------------------------------------------------------------\nRan 0 tests in 0.000s\n\nOK\n", "stderr"),
        ("mocha", "\n\n  0 passing (1ms)\n\n", "stdout"),
        ("pytest", "============ no tests ran in 0.01s ============\n", "stdout"),
    ],
)
def test_un_vert_vide_est_inexecutable_meme_a_code_de_sortie_zero(
    tmp_path: Path, label: str, printed: str, stream: str
) -> None:
    result = _check(tmp_path, printed, stream=stream)

    assert result.exit_code == 0
    assert result.unrunnable_reason is not None, label
    assert result.ok is False


@pytest.mark.parametrize(
    ("label", "printed"),
    [
        (
            "go mixte",
            "?   \texample.com/app/cmd\t[no test files]\nok  \texample.com/app/pkg\t0.004s\n",
        ),
        (
            "cargo avec doc-tests vides",
            "test result: ok. 12 passed; 0 failed; 0 ignored\n\ntest result: ok. 0 passed; 0 failed; 0 ignored\n",
        ),
        ("mocha", "\n  4 passing (8ms)\n"),
        ("unittest", "Ran 5 tests in 0.001s\n\nOK\n"),
        ("vitest", " Test Files  2 passed (2)\n      Tests  7 passed (7)\n"),
        ("pytest", "5 passed in 0.12s\n"),
    ],
)
def test_une_execution_reelle_reste_verte(tmp_path: Path, label: str, printed: str) -> None:
    result = _check(tmp_path, printed)

    assert result.unrunnable_reason is None, label
    assert result.ok is True


def test_une_commande_sans_executeur_reconnu_garde_son_comportement(tmp_path: Path) -> None:
    ok = _check(tmp_path, "tout va bien\n")
    assert ok.ok is True and ok.unrunnable_reason is None
    assert _classify_unrunnable("make check", 0, "tout va bien", "") is None
    assert _classify_unrunnable("make check", 1, "boom", "") is None


def test_un_echec_reel_d_un_executeur_n_est_pas_un_vert_vide() -> None:
    """Des tests ont tourné et l'un échoue : c'est un rouge, pas un inexécutable."""
    out = "\n  3 passing (5ms)\n  1 failing\n"
    assert _classify_unrunnable("npx mocha", 1, out, "") is None
    cargo = "test result: FAILED. 0 passed; 2 failed; 0 ignored\n"
    assert _classify_unrunnable("cargo test", 101, cargo, "") is None
