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
from grimoire.missions.empty_green import has_execution_proof


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


_VITEST_VIDE = " RUN  v1.6.0 /app\n\nNo test files found, exiting with code 1\n"
_JEST_VIDE = "No tests found, exiting with code 0\n"
_GO_VIDE = "?   \texample.com/app\t[no test files]\n"
_GO_JSON = (
    '{"Action":"output","Package":"ex.com/pkg","Output":"ok  \\tex.com/pkg\\t0.004s\\n"}\n'
    '{"Action":"pass","Package":"ex.com/pkg","Test":"TestA","Elapsed":0}\n'
    '{"Action":"output","Package":"ex.com/cmd","Output":"?   \\tex.com/cmd\\t[no test files]\\n"}\n'
)


@pytest.mark.parametrize(
    ("label", "printed"),
    [
        # W1-04-S1-01 : monorepo ``pnpm -r``, un paquet vide et un paquet testé.
        (
            "vitest monorepo",
            _VITEST_VIDE + "\n Test Files  2 passed (2)\n      Tests  7 passed (7)\n",
        ),
        (
            "jest monorepo",
            _JEST_VIDE + "\nTests:       3 passed, 3 total\nTest Suites: 1 passed, 1 total\n",
        ),
        ("vitest monorepo, un échec", _VITEST_VIDE + "\n      Tests  1 failed | 4 passed (5)\n"),
        # W1-04-S1-02 : go -json, et vide go dans un check composite.
        ("go -json", _GO_JSON),
        ("go vide puis pytest", _GO_VIDE + "50 passed in 1.2s\n"),
        ("go vide puis cargo", _GO_VIDE + "test result: ok. 3 passed; 0 failed; 0 ignored\n"),
        ("go vide puis mocha", _GO_VIDE + "\n  4 passing (8ms)\n"),
        ("go vide puis unittest", _GO_VIDE + "Ran 5 tests in 0.001s\n\nOK\n"),
        # W1-04-S1-03 : un identifiant de test cite le motif.
        (
            "pytest rouge citant le motif",
            "FAILED tests/test_x.py::test_a[vitest- RUN  v1.6.0 /app\\n\\nNo test files found, "
            "exiting with code 1\\n-stdout]\n=== 1 failed, 14 passed in 0.5s ===\n",
        ),
    ],
)
def test_une_preuve_d_execution_de_n_importe_quel_outil_l_emporte_sur_un_vide(
    tmp_path: Path, label: str, printed: str
) -> None:
    result = _check(tmp_path, printed)

    assert result.unrunnable_reason is None, label
    assert result.ok is True


def test_un_vrai_rouge_qui_cite_un_motif_de_vide_reste_un_rouge(tmp_path: Path) -> None:
    """Le motif cité dans un identifiant de test ne transforme pas le rouge en inexécutable."""
    out = (
        "FAILED tests/test_x.py::test_a[vitest- RUN  v1.6.0 /app\\n\\nNo test files found, "
        "exiting with code 1\\n-stdout]\n=== 1 failed, 14 passed in 0.5s ===\n"
    )
    assert _classify_unrunnable("pytest tests/unit/missions", 1, out, "") is None

    (result,) = _run_checks((f"printf %s {shlex.quote(out)}; exit 1",), project_root=tmp_path)
    assert result.exit_code == 1
    assert result.unrunnable_reason is None
    assert result.ok is False


@pytest.mark.parametrize("exit_code", [0, 1])
def test_un_vide_sans_aucune_preuve_reste_inexecutable(exit_code: int) -> None:
    for out in (_VITEST_VIDE, _JEST_VIDE, _GO_VIDE, _GO_JSON.splitlines()[2]):
        assert _classify_unrunnable("npx vitest", exit_code, out, "") is not None, out


# W1-04-S2-01 : un binaire cargo avorté (SIGABRT) n'imprime pas son ``test result:``.
_CARGO_VIDE = "running 0 tests\n\ntest result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out\n"
_CARGO_AVORTE = (
    "     Running unittests src/lib.rs (target/debug/deps/w104-68ff)\n\n"
    + _CARGO_VIDE
    + "\n     Running tests/it.rs (target/debug/deps/it-75c8)\n\n"
    "running 2 tests\ntest a_passes ... ok\ntest b_aborts ... error: test failed, to rerun pass `--test it`\n\n"
    "Caused by:\n  process didn't exit successfully: `it --test-threads=1` (signal: 6, SIGABRT: process abort signal)\n"
)
_CARGO_AVORTE_SEUL = (
    _CARGO_VIDE
    + "\nrunning 1 test\ntest b_aborts ... error: test failed, to rerun pass `--test it`\n"
    "Caused by:\n  process didn't exit successfully: `it` (signal: 6, SIGABRT: process abort signal)\n"
)

# W1-04-S2-02 : go a trouvé des fichiers de test mais aucun test à lancer.
_GO_AUCUN_TEST = "ok  \texample.com/m/b\t0.002s [no tests to run]\n"
_GO_AUCUN_TEST_JSON = '{"Action":"output","Package":"example.com/m/b","Output":"ok  \\texample.com/m/b\\t0.002s [no tests to run]\\n"}\n'
_GO_AUCUN_TEST_V = "testing: warning: no tests to run\nPASS\nok  \texample.com/m/b\t0.002s\n"
_GO_AUCUN_TEST_V_JSON = (
    '{"Action":"output","Package":"ex.com/b","Output":"testing: warning: no tests to run\\n"}\n'
    '{"Action":"output","Package":"ex.com/b","Output":"PASS\\n"}\n'
    '{"Action":"output","Package":"ex.com/b","Output":"ok  \\tex.com/b\\t0.002s\\n"}\n'
    '{"Action":"pass","Package":"ex.com/b","Elapsed":0.002}\n'
)


@pytest.mark.parametrize("exit_code", [101, 134])
@pytest.mark.parametrize("output", [_CARGO_AVORTE, _CARGO_AVORTE_SEUL], ids=["avorte-apres-ok", "avorte-seul"])
def test_un_binaire_cargo_avorte_reste_un_rouge(output: str, exit_code: int) -> None:
    """Des tests ont démarré : l'échec est un rouge ordinaire, pas un vert vide."""
    assert _classify_unrunnable("cargo test --offline", exit_code, output, "") is None


def test_un_binaire_cargo_avorte_via_un_vrai_check_reste_un_rouge(tmp_path: Path) -> None:
    (result,) = _run_checks((f"printf %s {shlex.quote(_CARGO_AVORTE)}; exit 101",), project_root=tmp_path)

    assert result.exit_code == 101
    assert result.unrunnable_reason is None
    assert result.ok is False


@pytest.mark.parametrize(
    "output",
    [
        _GO_AUCUN_TEST,
        _GO_AUCUN_TEST_JSON,
        _GO_AUCUN_TEST_V,
        _GO_AUCUN_TEST_V_JSON,
        _GO_VIDE + _GO_AUCUN_TEST,
    ],
    ids=["texte", "json", "verbose", "verbose-json", "melange-no-test-files"],
)
@pytest.mark.parametrize("exit_code", [0, 1])
def test_go_sans_aucun_test_a_lancer_est_inexecutable(output: str, exit_code: int) -> None:
    assert _classify_unrunnable("go test ./...", exit_code, output, "") is not None


def test_go_sans_aucun_test_a_lancer_via_un_vrai_check_est_inexecutable(tmp_path: Path) -> None:
    result = _check(tmp_path, _GO_VIDE + _GO_AUCUN_TEST)

    assert result.exit_code == 0
    assert result.unrunnable_reason is not None
    assert result.ok is False


@pytest.mark.parametrize(
    "output",
    [
        _GO_AUCUN_TEST + "ok  \texample.com/m/c\t0.010s\n",
        "FAIL\texample.com/m/a [build failed]\n" + _GO_AUCUN_TEST,
        _GO_AUCUN_TEST + "=== RUN   TestA\n--- PASS: TestA (0.00s)\nPASS\nok  \texample.com/m/c\t0.010s\n",
        _GO_AUCUN_TEST_V + "ok  \texample.com/m/c\t0.010s\n",
        _GO_AUCUN_TEST_JSON + '{"Action":"output","Package":"ex.com/c","Output":"ok  \\tex.com/c\\t0.010s\\n"}\n',
        _GO_AUCUN_TEST_V_JSON + '{"Action":"output","Package":"ex.com/c","Output":"ok  \\tex.com/c\\t0.010s\\n"}\n',
    ],
    ids=["texte", "build-failed", "verbose-run", "verbose-sans-test", "json", "verbose-json"],
)
def test_un_paquet_go_reellement_execute_reste_une_preuve(output: str) -> None:
    assert _classify_unrunnable("go test ./...", 0, output, "") is None


# W1-04-S3-01 : turbo et ``pnpm -r`` préfixent chaque ligne de la sortie de l'exécuteur.
_TURBO_GO_VIDE = "cli:test: ?   \texample.com/cli\t[no test files]\n"
_PNPM_VITEST_VIDE = "packages/web test: No test files found, exiting with code 1\n"
_PNPM_MOCHA_VIDE = "packages/web test: No test files found...\n"


@pytest.mark.parametrize(
    ("label", "printed", "exit_code"),
    [
        ("turbo go, vert", _TURBO_GO_VIDE + "api:test: ok  \texample.com/api\t0.004s\n", 0),
        (
            "turbo go, rouge",
            _TURBO_GO_VIDE
            + "api:test: --- FAIL: TestA (0.00s)\napi:test: FAIL\napi:test: FAIL\texample.com/api\t0.004s\n",
            1,
        ),
        ("pnpm mocha, vert", _PNPM_MOCHA_VIDE + "packages/api test:   4 passing (8ms)\n", 0),
        (
            "pnpm mocha, rouge",
            _PNPM_MOCHA_VIDE + "packages/api test:   3 passing (8ms)\npackages/api test:   1 failing\n",
            1,
        ),
        ("pnpm vitest, vert", _PNPM_VITEST_VIDE + "packages/api test:       Tests  7 passed (7)\n", 0),
        ("turbo cargo, vert", _TURBO_GO_VIDE + "api:test: running 2 tests\napi:test: test a ... ok\n", 0),
        ("turbo unittest, vert", _TURBO_GO_VIDE + "api:test: Ran 5 tests in 0.001s\n", 0),
        (
            "turbo go -v, vert",
            "api:test: testing: warning: no tests to run\napi:test: PASS\napi:test: ok  \texample.com/m/b\t0.002s\n"
            "api:test: ok  \texample.com/m/c\t0.010s\n",
            0,
        ),
    ],
)
def test_une_preuve_prefixee_par_un_orchestrateur_de_monorepo_reste_une_preuve(
    tmp_path: Path, label: str, printed: str, exit_code: int
) -> None:
    (result,) = _run_checks((f"printf %s {shlex.quote(printed)}; exit {exit_code}",), project_root=tmp_path)

    assert result.exit_code == exit_code
    assert result.unrunnable_reason is None, label
    assert result.ok is (exit_code == 0)


@pytest.mark.parametrize(
    ("printed", "ligne"),
    [
        ("api:test: ?   \texample.com/api\t[no test files]\n", "go"),
        ("api:test: ok  \texample.com/m/b\t0.002s [no tests to run]\n", "go à lancer"),
        ("packages/api test: Ran 0 tests in 0.000s\n", "unittest"),
        ("packages/api test:   0 passing (1ms)\n", "mocha"),
    ],
)
def test_un_vide_prefixe_sans_preuve_reste_inexecutable(printed: str, ligne: str) -> None:
    assert _classify_unrunnable("turbo run test", 0, printed, "") is not None, ligne


# W1-04-S3-02 : des exécuteurs hors de la liste d'origine (node:test, minitest, rspec...).
_NODE_TEST = "ℹ tests 3\nℹ suites 0\nℹ pass 3\nℹ fail 0\nℹ cancelled 0\n"
_NODE_TEST_ROUGE = "ℹ tests 3\nℹ pass 2\nℹ fail 1\n"
_MINITEST = "Finished in 0.001s\n3 runs, 5 assertions, 0 failures, 0 errors, 0 skips\n"
_MINITEST_ROUGE = "3 runs, 5 assertions, 1 failures, 0 errors, 0 skips\n"


@pytest.mark.parametrize(
    ("label", "printed", "exit_code"),
    [
        ("node:test pnpm, vert", _PNPM_VITEST_VIDE + "".join(f"packages/api test: {x}\n" for x in _NODE_TEST.splitlines()), 0),
        (
            "node:test pnpm, rouge",
            _PNPM_VITEST_VIDE + "".join(f"packages/api test: {x}\n" for x in _NODE_TEST_ROUGE.splitlines()),
            1,
        ),
        ("node:test, vert", _JEST_VIDE + _NODE_TEST, 0),
        ("TAP, rouge", _JEST_VIDE + "# tests 4\n# pass 3\n# fail 1\n", 1),
        ("minitest, vert", _JEST_VIDE + _MINITEST, 0),
        ("minitest, rouge", _JEST_VIDE + _MINITEST_ROUGE, 1),
        ("rspec, vert", _JEST_VIDE + "Finished in 0.01 seconds\n3 examples, 0 failures\n", 0),
        ("rspec, rouge", _JEST_VIDE + "3 examples, 1 failure\n", 1),
        ("phpunit, vert", _JEST_VIDE + "OK (3 tests, 5 assertions)\n", 0),
        ("phpunit, rouge", _JEST_VIDE + "Tests: 3, Assertions: 5, Failures: 1.\n", 1),
        (
            "dotnet, vert",
            _JEST_VIDE + "Passed!  - Failed:     0, Passed:     3, Skipped:     0, Total:     3, Duration: 12 ms\n",
            0,
        ),
        (
            "dotnet, rouge",
            _JEST_VIDE + "Failed!  - Failed:     1, Passed:     2, Skipped:     0, Total:     3, Duration: 12 ms\n",
            1,
        ),
    ],
)
def test_un_executeur_hors_de_la_liste_d_origine_reste_une_preuve(
    tmp_path: Path, label: str, printed: str, exit_code: int
) -> None:
    (result,) = _run_checks((f"printf %s {shlex.quote(printed)}; exit {exit_code}",), project_root=tmp_path)

    assert result.exit_code == exit_code
    assert result.unrunnable_reason is None, label
    assert result.ok is (exit_code == 0)


@pytest.mark.parametrize(
    "printed",
    ["ℹ tests 0\nℹ pass 0\nℹ fail 0\n", "0 runs, 0 assertions, 0 failures, 0 errors, 0 skips\n", "0 examples, 0 failures\n"],
)
def test_un_decompte_nul_hors_liste_n_est_pas_une_preuve(printed: str) -> None:
    assert has_execution_proof(printed) is False
