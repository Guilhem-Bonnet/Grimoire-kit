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


@pytest.mark.parametrize(
    "heading",
    ["## Claims", "## Claims (registre)", "## Claims — projet", "##  claims", "## Claims : état"],
)
def test_any_claims_heading_variant_opens_the_section(heading: str) -> None:
    """Un titre « ## Claims (…) » ne doit pas faire écarter en silence les lignes qui suivent."""
    from grimoire.core.standard_checks.claim_ledger_verify import scan_claim_rows

    text = (
        f"{heading}\n\n| ID | A | T | P | S | C | D |\n|---|---|---|---|---|---|---|\n"
        "| CL-001 | a | fait | src | prouvé | haute | utiliser |\n"
        "| CL 002 | b | fait |  | prouvé | haute | utiliser |\n"
        "| C-003 | b | fait |  | prouvé | haute | utiliser |\n"
    )
    scan = scan_claim_rows(text)
    assert scan.candidate_count == 3, heading
    assert scan.unevaluated_count == 2, heading


def test_a_claims_prefixed_word_is_not_the_claims_section() -> None:
    from grimoire.core.standard_checks.claim_ledger_verify import scan_claim_rows

    text = "## Claimsmanship\n\n| a | b |\n|---|---|\n| x | y |\n"
    assert scan_claim_rows(text).candidate_count == 0


def test_summary_check_exposes_unevaluated_count(tmp_path: Path) -> None:
    _project(tmp_path, "governed", "| CL-002 | a | fait |")
    result = verify_standard_profile(tmp_path)
    summary = [c for c in result.checks if c.id == "claims.summary"]
    assert summary and "unevaluated_count=1" in summary[0].message


_JUSTIFIED = ("noqa: S110 —", "silent-ok —")


def _is_trivial(node: ast.expr | None) -> bool:
    """Valeur sans appel ni nom : ``None``, constante, ou conteneur de telles valeurs."""
    if node is None or isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_is_trivial(e) for e in node.elts)
    if isinstance(node, ast.Dict):
        return all(k is not None and _is_trivial(k) for k in node.keys) and all(_is_trivial(v) for v in node.values)
    return False


def _is_silent(handler: ast.ExceptHandler) -> bool:
    """Handler qui avale l'exception : seulement pass, continue ou return d'une valeur triviale."""
    return all(
        isinstance(s, (ast.Pass, ast.Continue)) or (isinstance(s, ast.Return) and _is_trivial(s.value))
        for s in handler.body
    )


def _silent_handlers(source: str) -> list[tuple[int, bool]]:
    """``(ligne, justifié)`` pour chaque handler silencieux de *source*."""
    lines = source.splitlines()
    return [
        (node.lineno, any(tag in lines[node.lineno - 1] for tag in _JUSTIFIED))
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ExceptHandler) and _is_silent(node)
    ]


@pytest.mark.parametrize(
    "body",
    ["pass", "continue", "return", "return None", "return []", "return ([], False)", "return {}", "return 0"],
)
def test_silent_handler_detector_flags_every_swallow_shape(body: str) -> None:
    source = f"def f():\n    for x in y:\n        try:\n            g()\n        except OSError:\n            {body}\n"
    assert _silent_handlers(source) == [(5, False)]


@pytest.mark.parametrize("body", ["return helper()", "log(err)", "raise", "_add_check(r, 'x', 'error', 'm')"])
def test_silent_handler_detector_ignores_handlers_that_act(body: str) -> None:
    source = f"def f():\n    try:\n        g()\n    except OSError:\n        {body}\n"
    assert _silent_handlers(source) == []


def test_silent_handler_detector_accepts_a_justified_swallow() -> None:
    source = "try:\n    g()\nexcept OSError:  # silent-ok — raison\n    pass\n"
    assert _silent_handlers(source) == [(3, True)]


def test_no_silent_swallow_in_standard_checks() -> None:
    """Aucun handler qui avale l'exception dans standard_checks sans justification explicite.

    Silencieux = corps réduit à ``pass``, ``continue`` ou ``return`` d'une valeur
    triviale. Toléré seulement si la ligne ``except`` porte ``noqa: S110 —`` ou
    ``silent-ok —`` suivi de la raison. Sous-dossiers inclus.
    """
    import grimoire.core.standard_checks as pkg

    offenders: list[str] = []
    for path in sorted(Path(pkg.__file__).parent.rglob("*.py")):
        for lineno, justified in _silent_handlers(path.read_text(encoding="utf-8")):
            if not justified:
                offenders.append(f"{path.name}:{lineno}")
    assert not offenders, offenders
