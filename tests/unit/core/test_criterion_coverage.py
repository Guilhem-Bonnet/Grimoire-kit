"""W3-06 (VER-10) : couverture critère -> preuve du dossier d'acceptance.

Avant ce lot, seul un critère « passé » sans preuve était relevé. Un livrable
« accepté » pouvait garder un critère sans preuve, perdre un critère du tableau
de tâches, ou citer une preuve sans critère, sans un seul constat.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.core.agentic_standard import check_evidence_gates, setup_standard_profile, verify_standard_profile

_RECORD = Path("_grimoire-output/evidence/bootstrap/acceptance-record.md")
_BOARD = Path("_grimoire/standard/task-board.yaml")
_TEMPLATE_ROW = "| AC-001 |  |  | à vérifier |"
_PENDING = "| en attente |  |  |  |"
_ACCEPTED = "| accepté | Guilhem | 2026-10-18 | ok |"


def _replace(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _project(tmp_path: Path, profile: str, rows: str, decision: str = _ACCEPTED) -> Path:
    setup_standard_profile(tmp_path, profile_id=profile, project_name="Demo")
    record = tmp_path / _RECORD
    _replace(record, _TEMPLATE_ROW + "\n", rows)
    _replace(record, _PENDING, decision)
    return tmp_path


def _checks(root: Path, check_id: str) -> list[tuple[str, str]]:
    result = verify_standard_profile(root)
    return [(c.severity, c.message) for c in result.checks if c.id == check_id]


@pytest.mark.parametrize("status", ["à vérifier", "partiel"])
def test_an_accepted_deliverable_with_a_criterion_without_proof_is_an_error_when_governed(
    tmp_path: Path, status: str
) -> None:
    root = _project(
        tmp_path / "g",
        "governed",
        f"| AC-001 | Les artefacts sont générés | pytest -q | passé |\n| AC-002 | Le rapport est relu |  | {status} |\n",
    )
    found = _checks(root, "acceptance.uncovered")
    assert [sev for sev, _ in found] == ["error"], found
    assert "AC-002" in found[0][1]


def test_the_same_gap_is_only_a_warning_outside_governed_profiles(tmp_path: Path) -> None:
    root = _project(
        tmp_path / "s",
        "starter",
        "| AC-001 | Les artefacts sont générés | pytest -q | passé |\n| AC-002 | Le rapport est relu |  | à vérifier |\n",
    )
    assert [sev for sev, _ in _checks(root, "acceptance.uncovered")] == ["warning"]


def test_a_pending_criterion_without_proof_is_normal_before_the_decision(tmp_path: Path) -> None:
    root = _project(
        tmp_path / "p",
        "governed",
        "| AC-001 | Les artefacts sont générés | pytest -q | passé |\n| AC-002 | Le rapport est relu |  | à vérifier |\n",
        decision=_PENDING,
    )
    assert _checks(root, "acceptance.uncovered") == []


def test_a_failed_criterion_is_left_to_its_own_check(tmp_path: Path) -> None:
    root = _project(
        tmp_path / "f",
        "governed",
        "| AC-001 | Les artefacts sont générés | pytest -q | passé |\n| AC-002 | Le rapport est relu |  | échoué |\n",
    )
    assert _checks(root, "acceptance.uncovered") == []


def test_a_board_criterion_with_no_row_in_the_record_is_uncovered(tmp_path: Path) -> None:
    root = _project(
        tmp_path / "b",
        "governed",
        "| AC-001 | Standard artifacts are generated and verified. | pytest -q | passé |\n",
        decision=_PENDING,
    )
    board = root / _BOARD
    _replace(
        board,
        "  acceptance_criteria:\n  - Standard artifacts are generated and verified.\n",
        "  acceptance_criteria:\n  - Standard artifacts are generated and verified.\n  - Le rapport est relu.\n",
    )
    found = _checks(root, "acceptance.uncovered")
    assert len(found) == 1, found
    assert "AC-002" in found[0][1]
    # Reconnu par le texte, même sous un autre identifiant.
    _replace(
        root / _RECORD,
        "| AC-001 | Standard artifacts are generated and verified. | pytest -q | passé |\n",
        "| AC-001 | Standard artifacts are generated and verified. | pytest -q | passé |\n"
        "| AC-007 | le  rapport est relu. | revue PR | passé |\n",
    )
    assert _checks(root, "acceptance.uncovered") == []


def test_a_proof_without_a_criterion_is_an_orphan(tmp_path: Path) -> None:
    root = _project(
        tmp_path / "o",
        "governed",
        "| AC-001 | Standard artifacts are generated and verified. | pytest -q | passé |\n"
        "| AC-002 |  | captures/ecran.png | passé |\n",
    )
    found = _checks(root, "evidence.orphan")
    assert [sev for sev, _ in found] == ["warning"], found
    assert "AC-002" in found[0][1]


def test_a_complete_record_raises_nothing(tmp_path: Path) -> None:
    root = _project(
        tmp_path / "c",
        "governed",
        "| AC-001 | Standard artifacts are generated and verified. | pytest -q | passé |\n",
    )
    assert _checks(root, "acceptance.uncovered") == []
    assert _checks(root, "evidence.orphan") == []


def _gate_checks(root: Path, check_id: str, target_state: str) -> list[tuple[str, str]]:
    result = check_evidence_gates(root, task_id="bootstrap", target_state=target_state)
    return [(c.severity, c.message) for c in result.checks if c.id == check_id]


def _board_status(root: Path) -> str:
    import re

    match = re.search(r"^  status: (\S+)$", (root / _BOARD).read_text(encoding="utf-8"), re.MULTILINE)
    assert match, "statut du tableau introuvable"
    return match.group(1)


@pytest.mark.parametrize("target_state", ["accepted", "released"])
def test_the_gate_to_accepted_already_flags_an_uncovered_criterion_while_the_board_is_in_review(
    tmp_path: Path, target_state: str
) -> None:
    """Défaut W3-06-S1-01 : le check ne parlait qu'une fois la tâche déjà acceptée au tableau."""
    root = _project(
        tmp_path / "t",
        "governed",
        "| AC-001 | Les artefacts sont générés | pytest -q | passé |\n| AC-002 | Le rapport est relu |  | à vérifier |\n",
        decision=_PENDING,
    )
    _replace(root / _BOARD, "status: " + _board_status(root), "status: review")
    assert _gate_checks(root, "acceptance.uncovered", "review") == []
    found = _gate_checks(root, "acceptance.uncovered", target_state)
    assert [sev for sev, _ in found] == ["error"], found
    assert "AC-002" in found[0][1]


def test_the_gate_to_accepted_flags_a_board_criterion_missing_from_the_record_as_an_error(tmp_path: Path) -> None:
    root = _project(
        tmp_path / "m",
        "governed",
        "| AC-001 | Standard artifacts are generated and verified. | pytest -q | passé |\n",
        decision=_PENDING,
    )
    _replace(root / _BOARD, "status: " + _board_status(root), "status: review")
    _replace(
        root / _BOARD,
        "  acceptance_criteria:\n  - Standard artifacts are generated and verified.\n",
        "  acceptance_criteria:\n  - Standard artifacts are generated and verified.\n  - Le rapport est relu.\n",
    )
    # Avant l'acceptation : avertissement (la tâche est en cours). Vers `accepted` : erreur.
    assert [sev for sev, _ in _gate_checks(root, "acceptance.uncovered", "review")] == ["warning"]
    assert [sev for sev, _ in _gate_checks(root, "acceptance.uncovered", "accepted")] == ["error"]
