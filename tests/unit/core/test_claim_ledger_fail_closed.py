"""W1-05 : le vérificateur de claims échoue fermé en profil governed.

Une ligne malformée ou un registre vide est une erreur (pas un warning) en profil
governed ou production, selon la proposition W1-05 de « Dépasser AI-DLC ».
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.core.agentic_standard import setup_standard_profile, verify_standard_profile


def _ids(result, prefix: str) -> set[str]:
    return {c.id for c in result.checks if c.id.startswith(prefix)}


def test_empty_claim_ledger_is_warning_in_starter(tmp_path: Path) -> None:
    """Un registre vide est un avertissement en profil non-gouverné."""
    setup_standard_profile(tmp_path, profile_id="starter", project_name="Demo")
    result = verify_standard_profile(tmp_path)
    found = [c for c in result.checks if c.id == "claims.empty"]
    assert found and found[0].severity == "warning"


def test_empty_claim_ledger_is_error_in_governed(tmp_path: Path) -> None:
    """Un registre vide est une erreur en profil governed.

    (W1-05 requirement: registre vide en governed donne une erreur)
    """
    setup_standard_profile(tmp_path, profile_id="governed", project_name="Demo")
    result = verify_standard_profile(tmp_path)
    found = [c for c in result.checks if c.id == "claims.empty"]
    assert found and found[0].severity == "error", f"Expected error, got {found[0].severity if found else 'not found'}"


def test_empty_claim_ledger_is_error_in_production(tmp_path: Path) -> None:
    """Un registre vide est une erreur en profil production."""
    setup_standard_profile(tmp_path, profile_id="production", project_name="Demo")
    result = verify_standard_profile(tmp_path)
    found = [c for c in result.checks if c.id == "claims.empty"]
    assert found and found[0].severity == "error", f"Expected error, got {found[0].severity if found else 'not found'}"


def test_malformed_row_is_warning_in_starter(tmp_path: Path) -> None:
    """Une ligne malformée est un avertissement en profil non-gouverné."""
    setup_standard_profile(tmp_path, profile_id="starter", project_name="Demo")
    ledger = tmp_path / "_grimoire-output/evidence/bootstrap/claim-ledger.md"
    text = ledger.read_text(encoding="utf-8").replace(
        "| CL-001 |  | fait |  | hypothèse | faible | vérifier |",
        "| CL-002 | claim | fait |",  # Malformed: only 3 cells instead of 7
    )
    ledger.write_text(text, encoding="utf-8")
    result = verify_standard_profile(tmp_path)
    found = [c for c in result.checks if c.id == "claims.row_invalid"]
    assert found and found[0].severity == "warning"


def test_malformed_row_is_error_in_governed(tmp_path: Path) -> None:
    """Une ligne malformée est une erreur en profil governed.

    (W1-05 requirement: ligne malformée en governed donne une erreur)
    """
    setup_standard_profile(tmp_path, profile_id="governed", project_name="Demo")
    ledger = tmp_path / "_grimoire-output/evidence/bootstrap/claim-ledger.md"
    text = ledger.read_text(encoding="utf-8").replace(
        "| CL-001 |  | fait |  | hypothèse | faible | vérifier |",
        "| CL-002 | claim | fait |",  # Malformed: only 3 cells instead of 7
    )
    ledger.write_text(text, encoding="utf-8")
    result = verify_standard_profile(tmp_path)
    found = [c for c in result.checks if c.id == "claims.row_invalid"]
    assert found and found[0].severity == "error", f"Expected error, got {found[0].severity if found else 'not found'}"


def test_malformed_row_is_error_in_production(tmp_path: Path) -> None:
    """Une ligne malformée est une erreur en profil production."""
    setup_standard_profile(tmp_path, profile_id="production", project_name="Demo")
    ledger = tmp_path / "_grimoire-output/evidence/bootstrap/claim-ledger.md"
    text = ledger.read_text(encoding="utf-8").replace(
        "| CL-001 |  | fait |  | hypothèse | faible | vérifier |",
        "| CL-002 | claim | fait |",  # Malformed: only 3 cells instead of 7
    )
    ledger.write_text(text, encoding="utf-8")
    result = verify_standard_profile(tmp_path)
    found = [c for c in result.checks if c.id == "claims.row_invalid"]
    assert found and found[0].severity == "error", f"Expected error, got {found[0].severity if found else 'not found'}"


# --- Revue W1-05 : exception V0 figée, sévérité pendant in_progress ---------

LEDGER = "_grimoire-output/evidence/bootstrap/claim-ledger.md"
TEMPLATE_ROW = "| CL-001 |  | fait |  | hypothèse | faible | vérifier |"


def _classify_bootstrap(root: Path, klass: str) -> None:
    import yaml

    board = root / "_grimoire/standard/task-board.yaml"
    data = yaml.safe_load(board.read_text(encoding="utf-8"))
    for task in data["tasks"]:
        if task.get("task_id") == "bootstrap":
            task["verifiability"]["class"] = klass
    board.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def _review_claims(root: Path) -> dict[str, str]:
    from grimoire.core.agentic_standard import check_evidence_gates

    result = check_evidence_gates(root, task_id="bootstrap", target_state="review")
    return {c.id: c.severity for c in result.checks if c.id.startswith("claims.")}


def test_v0_task_outside_governed_keeps_the_documented_claims_exemption(tmp_path: Path) -> None:
    """Lot I (#582), documenté : une tâche V0 hors governed n'a pas de `claims.empty` à la revue."""
    setup_standard_profile(tmp_path, profile_id="production", project_name="Demo")
    _classify_bootstrap(tmp_path, "V0")
    (tmp_path / LEDGER).write_text("", encoding="utf-8")
    assert "claims.empty" not in _review_claims(tmp_path)


def test_v0_exemption_never_applies_to_governed(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="governed", project_name="Demo")
    _classify_bootstrap(tmp_path, "V0")
    (tmp_path / LEDGER).write_text("", encoding="utf-8")
    assert _review_claims(tmp_path).get("claims.empty") == "error"


def test_non_v0_task_in_production_keeps_claims_empty_as_an_error(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="production", project_name="Demo")
    _classify_bootstrap(tmp_path, "V2")
    (tmp_path / LEDGER).write_text("", encoding="utf-8")
    assert _review_claims(tmp_path).get("claims.empty") == "error"


def _in_progress_claims(root: Path) -> dict[str, str]:
    from grimoire.core.agentic_standard import check_evidence_gates

    result = check_evidence_gates(root, task_id="bootstrap", target_state="in_progress")
    return {c.id: c.severity for c in result.checks if c.id.startswith("claims.")}


@pytest.mark.parametrize(
    ("row", "check_id"),
    [
        ("| CL-001 | a | fait |  | à vérifier | faible | vérifier |", "claims.status_invalid"),
        ("| CL-001 | a | fait |  | hypothèse | faible | peut-être |", "claims.decision_invalid"),
        ("| CL-001 | a | fait |", "claims.row_invalid"),
    ],
)
def test_half_written_row_is_a_warning_while_in_progress(tmp_path: Path, row: str, check_id: str) -> None:
    """Pendant le travail (Stop, SubagentStop, PreCompact), une ligne en cours d'écriture ne bloque pas."""
    setup_standard_profile(tmp_path, profile_id="governed", project_name="Demo")
    ledger = tmp_path / LEDGER
    ledger.write_text(ledger.read_text(encoding="utf-8").replace(TEMPLATE_ROW, row), encoding="utf-8")
    assert _in_progress_claims(tmp_path).get(check_id) == "warning"


@pytest.mark.parametrize(
    ("row", "check_id"),
    [
        ("| CL-001 | a | fait |  | à vérifier | faible | vérifier |", "claims.status_invalid"),
        ("| CL-001 | a | fait |  | hypothèse | faible | peut-être |", "claims.decision_invalid"),
        ("| CL-001 | a | fait |", "claims.row_invalid"),
    ],
)
def test_same_row_is_an_error_at_review_in_governed(tmp_path: Path, row: str, check_id: str) -> None:
    setup_standard_profile(tmp_path, profile_id="governed", project_name="Demo")
    ledger = tmp_path / LEDGER
    ledger.write_text(ledger.read_text(encoding="utf-8").replace(TEMPLATE_ROW, row), encoding="utf-8")
    assert _review_claims(tmp_path).get(check_id) == "error"


@pytest.mark.parametrize(
    ("row", "check_id"),
    [
        ("| CL-001 | a | fait |  | prouvé | haute | vérifier |", "claims.proved_without_evidence"),
        ("| CL-001 | a | fait |  | hypothèse | faible | utiliser |", "claims.used_unproved"),
    ],
)
def test_contradictions_stay_errors_while_in_progress(tmp_path: Path, row: str, check_id: str) -> None:
    """Les deux contradictions annoncées par la docstring de `rows_only` restent bloquantes."""
    setup_standard_profile(tmp_path, profile_id="governed", project_name="Demo")
    ledger = tmp_path / LEDGER
    ledger.write_text(ledger.read_text(encoding="utf-8").replace(TEMPLATE_ROW, row), encoding="utf-8")
    assert _in_progress_claims(tmp_path).get(check_id) == "error"
