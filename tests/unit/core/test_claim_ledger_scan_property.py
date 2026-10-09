"""W1-05 (revue) : propriété de comptage du scanner du claim-ledger, par génération.

Complète le produit cartésien de ``test_claim_ledger_scan_invariants.py`` :
``hypothesis`` (dépendance de dev) explore des lignes de tableau arbitraires.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("hypothesis")

from hypothesis import given
from hypothesis import strategies as st

from grimoire.core.standard_checks.base import StandardProfile, StandardVerificationResult
from grimoire.core.standard_checks.claim_ledger_verify import scan_claim_rows, verify_claim_ledger

_HEADER = "| ID | A | T | P | S | C | D |\n|---|---|---|---|---|---|---|\n"
_STATUSES = {"prouvé", "hypothèse", "contredit", "réfuté"}
_DECISIONS = {"utiliser", "vérifier", "rejeter", "écarter"}

_CELL = st.text(alphabet="abcXYZ0129 _é", max_size=8).map(str.strip)
_ID = st.one_of(
    st.from_regex(r"CL-[0-9]{1,4}", fullmatch=True),
    st.from_regex(r"[Cc][Ll][- ]?[0-9]{0,4}", fullmatch=True),
    st.sampled_from(["", "C-003", "XX-1"]),
)
_VOCAB = st.sampled_from([*sorted(_STATUSES | _DECISIONS), "bidule", ""])
_ROW = st.builds(
    lambda lead, first, rest, trail: f"{lead}| " + " | ".join([first, *rest]) + trail,
    st.sampled_from(["", " ", "  "]),
    _ID,
    st.lists(st.one_of(_CELL, _VOCAB), max_size=8),
    st.sampled_from(["", " |", "|"]),
)
_HEADING = st.sampled_from(["## Claims", "## Claims (registre)", "## Claims — v2", "##   Claims"])


def _cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


def _conforming(cells: list[str]) -> bool:
    return (
        len(cells) >= 7
        and re.fullmatch(r"CL-\d{3,}", cells[0]) is not None
        and cells[4].lower() in _STATUSES
        and cells[6].lower() in _DECISIONS
        and not (cells[4].lower() == "prouvé" and not cells[3])
    )


@given(heading=_HEADING, rows=st.lists(_ROW, min_size=1, max_size=6))
def test_candidate_count_is_evaluated_plus_unevaluated(heading: str, rows: list[str]) -> None:
    scan = scan_claim_rows(f"{heading}\n\n{_HEADER}" + "\n".join(rows) + "\n")
    assert scan.candidate_count == len(scan.evaluated) + scan.unevaluated_count
    # Toute ligne de tableau de la section est candidate, hors ligne d'en-tête (« id »).
    expected = sum(1 for r in rows if _cells(r)[0].lower() != "id")
    assert scan.candidate_count == expected


@given(heading=_HEADING, rows=st.lists(_ROW, min_size=1, max_size=6))
def test_a_nonconforming_row_is_always_an_error_in_governed(heading: str, rows: list[str], tmp_path_factory) -> None:
    root = tmp_path_factory.mktemp("prop")
    ledger = root / "_grimoire-output/evidence/t1/claim-ledger.md"
    ledger.parent.mkdir(parents=True)
    text = f"{heading}\n\n{_HEADER}" + "\n".join(rows) + "\n"
    ledger.write_text(text, encoding="utf-8")
    profile = StandardProfile(
        id="governed", display_name="governed", required_artifacts=(), mapped_capabilities=(), minimum_evidence=()
    )
    result = StandardVerificationResult(profile="governed", project_root=root)
    verify_claim_ledger(root, profile, "t1", result)
    candidates = [r for r in rows if _cells(r)[0].lower() != "id"]
    if candidates and not all(_conforming(_cells(r)) for r in candidates):
        assert any(c.severity == "error" for c in result.checks), text
    elif not candidates:
        assert any(c.id == "claims.empty" and c.severity == "error" for c in result.checks), text
