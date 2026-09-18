"""``no_tests_collected`` : distinguer « rien collecté » d'un rouge réel (issue #582 lot I)."""

from __future__ import annotations

from grimoire.core.standard_checks.no_tests_collected import (
    classify_no_tests_collected,
    has_no_test_justification,
)


def test_pytest_exit_5_is_nothing_collected() -> None:
    assert classify_no_tests_collected(command="pytest", exit_code=5, output_excerpt="collected 0 items\nno tests ran in 0.01s\n")


def test_pytest_real_failure_is_not_nothing_collected() -> None:
    """Rouge-avant : un test rouge (exit 1, un AssertionError) reste un rouge, jamais reclassé."""
    assert not classify_no_tests_collected(
        command="pytest", exit_code=1, output_excerpt="FAILED test_x.py::test_a - AssertionError\n1 failed in 0.02s\n"
    )


def test_jest_no_tests_found_is_nothing_collected() -> None:
    """Rouge-avant : `npm test` -> jest, `No tests found`, exit 1 (lot H, 0/15 runs JS)."""
    output = "No tests found, exiting with code 1\ntestMatch: **/?(*.)+(spec|test).[tj]s?(x) - 0 matches\n"
    assert classify_no_tests_collected(command="npm test", exit_code=1, output_excerpt=output)


def test_jest_real_failure_is_not_nothing_collected() -> None:
    output = "FAIL ./affine-cipher.spec.js\n  ✕ encodes a sentence\n1 failed, 0 passed\n"
    assert not classify_no_tests_collected(command="npm test", exit_code=1, output_excerpt=output)


def test_go_test_no_test_files_is_nothing_collected() -> None:
    """Rouge-avant : `go test ./...` sans fichier `_test.go` (Go récent, 'no test files')."""
    assert classify_no_tests_collected(command="go test ./...", exit_code=0, output_excerpt="? demo [no test files]\n")


def test_go_test_no_test_files_historical_wording() -> None:
    assert classify_no_tests_collected(command="go test ./...", exit_code=0, output_excerpt="no test files\n")


def test_go_test_real_failure_is_not_nothing_collected() -> None:
    output = "--- FAIL: TestSum (0.00s)\nFAIL\nFAIL\tdemo\t0.002s\n"
    assert not classify_no_tests_collected(command="go test ./...", exit_code=1, output_excerpt=output)


def test_dotnet_no_test_available_is_nothing_collected() -> None:
    assert classify_no_tests_collected(command="dotnet test", exit_code=0, output_excerpt="No test is available in demo.dll\n")


def test_mocha_zero_passing_without_failing_is_nothing_collected() -> None:
    assert classify_no_tests_collected(command="npx mocha", exit_code=0, output_excerpt="  0 passing (2ms)\n")


def test_mocha_zero_passing_with_failing_is_not_nothing_collected() -> None:
    """0 passing à côté de N failing = des tests existent et ont échoué au chargement, pas une suite absente."""
    output = "  0 passing (2ms)\n  1 failing\n\n  1) demo\n     TypeError: x is not a function\n"
    assert not classify_no_tests_collected(command="npx mocha", exit_code=1, output_excerpt=output)


def test_cargo_test_zero_on_every_target_is_nothing_collected() -> None:
    output = "running 0 tests\n\ntest result: ok. 0 passed; 0 failed\n\nrunning 0 tests\n\ntest result: ok. 0 passed; 0 failed\n"
    assert classify_no_tests_collected(command="cargo test", exit_code=0, output_excerpt=output)


def test_cargo_test_mixed_targets_is_not_nothing_collected() -> None:
    """Une cible vide à côté d'une cible qui a de vrais tests reste une suite réelle."""
    output = "running 0 tests\n\ntest result: ok. 0 passed\n\nrunning 3 tests\ntest a ... ok\ntest result: ok. 3 passed\n"
    assert not classify_no_tests_collected(command="cargo test", exit_code=0, output_excerpt=output)


def test_has_no_test_justification_recognises_the_documented_line() -> None:
    text = "## Vérifications\n\nsans test : tests cachés par le harnais, jugés par le validateur.\n"
    assert has_no_test_justification(text)


def test_has_no_test_justification_is_case_insensitive() -> None:
    assert has_no_test_justification("Sans Test: raison quelconque")


def test_has_no_test_justification_requires_a_reason() -> None:
    """« sans test : » sans rien derrière n'est pas une justification — un gabarit vide ne doit pas suffire."""
    assert not has_no_test_justification("sans test :\n")


def test_has_no_test_justification_absent_by_default() -> None:
    assert not has_no_test_justification("# Agentic Acceptance Record\n\n- Task id: demo\n")
