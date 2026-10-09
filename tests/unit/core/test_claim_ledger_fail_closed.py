"""W1-05 : le vérificateur de claims échoue fermé en profil governed.

Une ligne malformée ou un registre vide est une erreur (pas un warning) en profil
governed ou production, selon la proposition W1-05 de « Dépasser AI-DLC ».
"""

from __future__ import annotations

from pathlib import Path

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
