"""W1-05 (revue) : aucune ligne du claim-ledger n'est ignorée en silence.

Invariant : toute ligne candidate est soit évaluée, soit comptée non évaluée et
signalée ; en governed, une ligne non conforme produit au moins une erreur.
"""

from __future__ import annotations

import ast
import itertools
from pathlib import Path

import pytest

from grimoire.core.agentic_standard import setup_standard_profile, verify_standard_profile

LEDGER = "_grimoire-output/evidence/bootstrap/claim-ledger.md"
TEMPLATE_ROW = "| CL-001 |  | fait |  | hypothèse | faible | vérifier |"
GOOD = "| CL-001 | a | fait | src/x | prouvé | haute | utiliser |"
SUMMARY_EMPTY = "| Affirmations bloquantes non prouvées |  |"


def _project(tmp_path: Path, profile: str, row: str | None) -> None:
    setup_standard_profile(tmp_path, profile_id=profile, project_name="Demo")
    ledger = tmp_path / LEDGER
    text = ledger.read_text(encoding="utf-8").replace(SUMMARY_EMPTY, "| Affirmations bloquantes non prouvées | 0 |")
    text = text.replace(TEMPLATE_ROW, GOOD if row is None else f"{GOOD}\n{row}")
    ledger.write_text(text, encoding="utf-8")


def _errors(tmp_path: Path) -> set[str]:
    result = verify_standard_profile(tmp_path)
    return {c.id for c in result.checks if c.id.startswith("claims.") and c.severity == "error"}


BAD_ROWS = [
    "| cl-002 | a | fait |",
    "|CL-002|a|fait|",
    "  | CL-002 | a | fait |",
    "| CL-2 | a | fait | src | prouvé | haute | utiliser |",
    "| CL-002 | a | fait | src | bidule | haute | vérifier |",
    "| CL-002 | a | fait | src | prouvé | haute | peut-être |",
    "| CL-002 | a | fait |  | prouvé | haute | vérifier |",
]


@pytest.mark.parametrize("profile", ["governed", "production"])
@pytest.mark.parametrize("row", BAD_ROWS)
def test_every_nonconforming_row_is_an_error_in_strict_profiles(tmp_path: Path, profile: str, row: str) -> None:
    _project(tmp_path, profile, row)
    assert _errors(tmp_path), row


def test_good_row_alone_has_no_claim_error(tmp_path: Path) -> None:
    _project(tmp_path, "governed", None)
    assert not _errors(tmp_path)


@pytest.mark.parametrize("profile", ["governed", "production"])
@pytest.mark.parametrize("mode", ["emptied", "deleted"])
def test_emptied_or_deleted_ledger_is_an_error(tmp_path: Path, profile: str, mode: str) -> None:
    setup_standard_profile(tmp_path, profile_id=profile, project_name="Demo")
    ledger = tmp_path / LEDGER
    if mode == "emptied":
        ledger.write_text("", encoding="utf-8")
    else:
        ledger.unlink()
    assert "claims.empty" in _errors(tmp_path)


def test_emptied_ledger_blocks_review_gate_in_governed(tmp_path: Path) -> None:
    from grimoire.core.agentic_standard import check_evidence_gates

    setup_standard_profile(tmp_path, profile_id="governed", project_name="Demo")
    (tmp_path / LEDGER).write_text("", encoding="utf-8")
    result = check_evidence_gates(tmp_path, task_id="bootstrap", target_state="review")
    assert any(c.id.startswith("claims.") and c.severity == "error" for c in result.checks) or any(
        "claim" in c.id and c.severity == "error" for c in result.checks
    )


def test_scan_counts_every_candidate_either_evaluated_or_unevaluated() -> None:
    from grimoire.core.standard_checks.claim_ledger_verify import scan_claim_rows

    cells_variants = ["CL-001", "cl-002", " CL-003 ", "CL-4", "XX-005"]
    tails = ["", "| a | fait", "| a | fait | s | prouvé | h | utiliser", "| a | fait | s | pruvé | h | x"]
    for cell, tail, lead, trail in itertools.product(cells_variants, tails, ["", "  "], ["", " |"]):
        line = f"{lead}| {cell} {tail}{trail}"
        text = f"## Claims\n\n| ID | A |\n|---|---|\n{line}\n\n## Suite\n"
        scan = scan_claim_rows(text)
        expected = 1 if line.strip().startswith("|") and line.strip().strip("|").strip() else 0
        assert scan.candidate_count == expected, line
        assert scan.candidate_count == len(scan.evaluated) + scan.unevaluated_count


def test_summary_check_exposes_unevaluated_count(tmp_path: Path) -> None:
    _project(tmp_path, "governed", "| CL-002 | a | fait |")
    result = verify_standard_profile(tmp_path)
    summary = [c for c in result.checks if c.id == "claims.summary"]
    assert summary and "unevaluated_count=1" in summary[0].message


def test_no_silent_swallow_in_standard_checks() -> None:
    """Aucun `except ...: pass` dans standard_checks sans justification explicite.

    Seul est toléré un handler dont la ligne porte `noqa: S110 —` suivi de la raison.
    """
    import grimoire.core.standard_checks as pkg

    offenders: list[str] = []
    for path in Path(pkg.__file__).parent.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        src_lines = source.splitlines()
        for node in ast.walk(ast.parse(source)):
            if (
                isinstance(node, ast.ExceptHandler)
                and all(isinstance(s, ast.Pass) for s in node.body)
                and "noqa: S110 —" not in src_lines[node.lineno - 1]
            ):
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, offenders
