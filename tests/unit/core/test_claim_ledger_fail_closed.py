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


# --- Revue W1-05 (2e tour) : la forme ne masque pas le fond ------------------

SUMMARY_EMPTY = "| Affirmations bloquantes non prouvées |  |"


def _write_row(root: Path, row: str) -> None:
    ledger = root / LEDGER
    text = ledger.read_text(encoding="utf-8").replace(SUMMARY_EMPTY, "| Affirmations bloquantes non prouvées | 0 |")
    ledger.write_text(text.replace(TEMPLATE_ROW, row), encoding="utf-8")


def _verify_claims(root: Path) -> dict[str, set[str]]:
    result = verify_standard_profile(root)
    found: dict[str, set[str]] = {}
    for c in result.checks:
        if c.id.startswith("claims."):
            found.setdefault(c.id, set()).add(c.severity)
    return found


@pytest.mark.parametrize("claim_id", ["CL-2", "CL-01", "cl-002"])
@pytest.mark.parametrize("profile", ["starter", "orchestrated"])
def test_badly_numbered_proved_row_without_evidence_is_an_error_at_review(
    tmp_path: Path, claim_id: str, profile: str
) -> None:
    """Un identifiant hors `CL-NNN` ne dispense pas du fond : « prouvé » sans preuve reste une erreur."""
    setup_standard_profile(tmp_path, profile_id=profile, project_name="Demo")
    _write_row(tmp_path, f"| {claim_id} | a | fait |  | prouvé | haute | vérifier |")
    review = _review_claims(tmp_path)
    assert review.get("claims.proved_without_evidence") == "error"
    assert review.get("claims.row_invalid") is not None
    assert "error" in _verify_claims(tmp_path).get("claims.proved_without_evidence", set())


@pytest.mark.parametrize("claim_id", ["CL-2", "CL-01", "cl-002"])
@pytest.mark.parametrize(
    ("row_tail", "check_id", "severity"),
    [
        ("| a | fait |  | prouvé | haute | vérifier |", "claims.proved_without_evidence", "error"),
        ("| a | fait |  | hypothèse | faible | utiliser |", "claims.used_unproved", "error"),
    ],
)
def test_badly_numbered_contradiction_blocks_in_progress_in_governed(
    tmp_path: Path, claim_id: str, row_tail: str, check_id: str, severity: str
) -> None:
    setup_standard_profile(tmp_path, profile_id="governed", project_name="Demo")
    _write_row(tmp_path, f"| {claim_id} {row_tail}")
    claims = _in_progress_claims(tmp_path)
    assert claims.get(check_id) == severity
    assert claims.get("claims.row_invalid") == "warning"
    assert _review_claims(tmp_path).get(check_id) == severity


@pytest.mark.parametrize("claim_id", ["CL-2", "CL-01", "cl-002"])
@pytest.mark.parametrize("profile", ["starter", "orchestrated"])
def test_badly_numbered_used_unproved_row_is_a_warning_outside_strict_profiles(
    tmp_path: Path, claim_id: str, profile: str
) -> None:
    setup_standard_profile(tmp_path, profile_id=profile, project_name="Demo")
    _write_row(tmp_path, f"| {claim_id} | a | fait |  | hypothèse | faible | utiliser |")
    assert _review_claims(tmp_path).get("claims.used_unproved") == "warning"


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ("| CL-001 |  | fait |  | hypothèse | moyenne | vérifier |", "claims.row_invalid"),
        ("| CL-001 |  | fait |  | hypothèse | faible | rejeter |", "claims.row_invalid"),
        ("| CL-001 |  |  |  |  |  |  |", "claims.row_invalid"),
        ("| CL-001 |  | fait |  | Hypothèse | faible | Vérifier |", "claims.empty"),
    ],
)
@pytest.mark.parametrize("profile", ["governed", "production"])
def test_row_without_claim_text_is_never_a_silent_pass_in_strict_profiles(
    tmp_path: Path, row: str, expected: str, profile: str
) -> None:
    """Une ligne sans affirmation n'est pas une affirmation : erreur à la revue, jamais « tout va bien »."""
    setup_standard_profile(tmp_path, profile_id=profile, project_name="Demo")
    _write_row(tmp_path, row)
    assert _review_claims(tmp_path).get(expected) == "error"
    assert "error" in _verify_claims(tmp_path).get(expected, set())


def test_row_without_claim_text_is_not_counted_as_evaluated() -> None:
    from grimoire.core.standard_checks.claim_ledger_verify import scan_claim_rows

    scan = scan_claim_rows(
        "## Claims\n\n| ID | A | T | P | S | C | D |\n|---|---|---|---|---|---|---|\n"
        "| CL-001 |  | fait |  | hypothèse | moyenne | vérifier |\n"
        "| CL-002 | a | fait | src | prouvé | haute | utiliser |\n"
    )
    assert len(scan.evaluated) == 1
    assert scan.unevaluated_count == 1


def _empty_ledger_verify(root: Path, profile: str, klass: str) -> dict[str, set[str]]:
    setup_standard_profile(root, profile_id=profile, project_name="Demo")
    _classify_bootstrap(root, klass)
    (root / LEDGER).write_text("", encoding="utf-8")
    return _verify_claims(root)


def test_verify_applies_the_same_v0_exemption_as_the_gate(tmp_path: Path) -> None:
    """`standard verify` et le gate rendent le même constat : tâche V0 hors governed, pas de `claims.empty`."""
    assert "claims.empty" not in _empty_ledger_verify(tmp_path, "production", "V0")
    assert "claims.empty" not in _review_claims(tmp_path)


def test_verify_keeps_claims_empty_for_non_v0_or_governed(tmp_path: Path) -> None:
    assert _empty_ledger_verify(tmp_path / "a", "production", "V2").get("claims.empty") == {"error"}
    assert _empty_ledger_verify(tmp_path / "b", "governed", "V0").get("claims.empty") == {"error"}


# --- Revue W1-05 (tour 3) : vocabulaire émis, barre échappée, lignes courtes --


@pytest.mark.parametrize("profile", ["governed", "production"])
@pytest.mark.parametrize(
    "row",
    [
        # valeur prescrite par le skill grimoire-evidence : « marquée non vérifié »
        "| CL-001 | Le cache est invalidé au redémarrage | fait |  | non vérifié | faible | vérifier |",
        "| CL-001 | a | fait | src/x.py:3 | prouvé (par lecture) | élevée | utiliser |",
        "| CL-001 | a | fait | src/x.py:3 | prouvé par construction. | élevée | utiliser |",
        "| CL-001 | a | fait |  | hypothèse | faible | vérifier (relecture humaine). |",
        "| CL-001 | a | fait |  | Non vérifié ; à relire | faible | vérifier (utilisateur) |",
    ],
)
def test_values_with_annotations_and_the_skill_wording_are_accepted(tmp_path: Path, row: str, profile: str) -> None:
    """Le vocabulaire fermé tolère l'annotation (parenthèse, `;`, point final) et le « non vérifié » du skill."""
    setup_standard_profile(tmp_path, profile_id=profile, project_name="Demo")
    _write_row(tmp_path, row)
    review = _review_claims(tmp_path)
    assert "claims.status_invalid" not in review
    assert "claims.decision_invalid" not in review
    verify = _verify_claims(tmp_path)
    assert "claims.status_invalid" not in verify
    assert "claims.decision_invalid" not in verify


def test_non_verifie_counts_as_unproved_not_as_proved(tmp_path: Path) -> None:
    """« non vérifié » est un synonyme d'hypothèse : « utiliser » dessus reste une contradiction."""
    setup_standard_profile(tmp_path, profile_id="governed", project_name="Demo")
    _write_row(tmp_path, "| CL-001 | a | fait |  | non vérifié | faible | utiliser |")
    assert _review_claims(tmp_path).get("claims.used_unproved") == "error"


@pytest.mark.parametrize(
    "row",
    [
        "| CL-001 | a | fait |  | non prouvé | faible | vérifier |",
        "| CL-001 | a | fait |  | à vérifier | faible | vérifier |",
        "| CL-001 | a | fait |  | prouvéeee | faible | vérifier |",
        "| CL-001 | a | fait |  | hypothèse | faible | vérifierx |",
    ],
)
def test_normalisation_does_not_open_the_vocabulary(tmp_path: Path, row: str) -> None:
    setup_standard_profile(tmp_path, profile_id="governed", project_name="Demo")
    _write_row(tmp_path, row)
    review = _review_claims(tmp_path)
    assert "error" in {review.get("claims.status_invalid"), review.get("claims.decision_invalid")}


def test_escaped_pipe_in_a_cell_is_not_a_column_separator(tmp_path: Path) -> None:
    """GFM : `\\|` reste dans la cellule. Une ligne valide ne lève aucun constat de forme."""
    setup_standard_profile(tmp_path, profile_id="governed", project_name="Demo")
    _write_row(tmp_path, "| CL-002 | `a \\| b` est vrai | fait | src/x | prouvé | élevée | utiliser |")
    review = _review_claims(tmp_path)
    assert not {"claims.status_invalid", "claims.decision_invalid", "claims.row_invalid"} & set(review)
    assert not {"claims.status_invalid", "claims.decision_invalid", "claims.row_invalid"} & set(
        _verify_claims(tmp_path)
    )


def test_escaped_pipe_proved_without_evidence_is_still_an_error(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="starter", project_name="Demo")
    _write_row(tmp_path, "| CL-002 | a \\| b | fait |  | prouvé | haute | utiliser |")
    assert _review_claims(tmp_path).get("claims.proved_without_evidence") == "error"


def test_escaped_pipe_at_the_end_of_the_last_cell_is_kept() -> None:
    from grimoire.core.standard_checks.claim_ledger_verify import _cells

    assert _cells("| CL-002 | a \\| b | fait |  | prouvé | haute | x \\| |") == [
        "CL-002",
        "a | b",
        "fait",
        "",
        "prouvé",
        "haute",
        "x |",
    ]


@pytest.mark.parametrize("profile", ["starter", "orchestrated", "governed"])
@pytest.mark.parametrize(
    "row",
    [
        "| CL-002 | a | fait |  | prouvé | haute |",
        "| CL-002 | a | fait |  | prouvé |",
    ],
)
def test_short_proved_row_without_evidence_is_an_error_at_review(tmp_path: Path, row: str, profile: str) -> None:
    """Le fond se contrôle aussi pour une ligne de 5 ou 6 cellules, signalée `row_invalid`."""
    setup_standard_profile(tmp_path, profile_id=profile, project_name="Demo")
    _write_row(tmp_path, row)
    review = _review_claims(tmp_path)
    assert review.get("claims.proved_without_evidence") == "error"
    assert review.get("claims.row_invalid") is not None
    assert "error" in _verify_claims(tmp_path).get("claims.proved_without_evidence", set())


def test_short_row_with_a_proof_does_not_raise_a_false_contradiction(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="starter", project_name="Demo")
    _write_row(tmp_path, "| CL-002 | a | fait | src/x | prouvé | haute |")
    assert "claims.proved_without_evidence" not in _review_claims(tmp_path)


def test_rows_under_five_cells_stay_form_only() -> None:
    from grimoire.core.standard_checks.claim_ledger_verify import scan_claim_rows

    scan = scan_claim_rows("## Claims\n\n| CL-002 | a | fait | prouvé |\n")
    assert scan.unevaluated_count == 1
    assert scan.unevaluated_cells == []


# --- Revue W1-05 (4e tour) : ni la forme du tableau ni la section ne cachent une ligne ---

_BAD_PROVED = "CL-002 | b | fait |  | prouvé | élevée | utiliser"
_SECOND_SECTION = "\n## Claims supplémentaires\n\n| ID | A | T | P | S | C | D |\n|---|---|---|---|---|---|---|\n"


def _append_second_claims_section(root: Path, row: str) -> None:
    ledger = root / LEDGER
    ledger.write_text(ledger.read_text(encoding="utf-8") + _SECOND_SECTION + row + "\n", encoding="utf-8")


@pytest.mark.parametrize(
    "row",
    [
        _BAD_PROVED,
        f"{_BAD_PROVED} |",
        f"  {_BAD_PROVED}",
        f"> | {_BAD_PROVED} |",
        f"> {_BAD_PROVED}",
    ],
    ids=["no-outer-pipes", "trailing-pipe-only", "indented", "blockquote", "blockquote-no-pipes"],
)
@pytest.mark.parametrize("profile", ["starter", "orchestrated", "governed"])
def test_row_without_leading_pipe_is_evaluated(tmp_path: Path, profile: str, row: str) -> None:
    """GFM autorise un tableau sans barres extérieures : la ligne est évaluée, pas ignorée."""
    setup_standard_profile(tmp_path, profile_id=profile, project_name="Demo")
    _write_row(tmp_path, row)
    review = _review_claims(tmp_path)
    assert review.get("claims.proved_without_evidence") == "error"
    assert "claims.empty" not in review
    assert "error" in _verify_claims(tmp_path).get("claims.proved_without_evidence", set())


@pytest.mark.parametrize("profile", ["starter", "orchestrated", "governed"])
def test_row_of_a_second_claims_section_is_evaluated(tmp_path: Path, profile: str) -> None:
    setup_standard_profile(tmp_path, profile_id=profile, project_name="Demo")
    _write_row(tmp_path, "| CL-001 | a | fait | src/x | prouvé | haute | utiliser |")
    _append_second_claims_section(tmp_path, "| C3 | b | fait |  | prouvé | élevée | utiliser |")
    review = _review_claims(tmp_path)
    assert review.get("claims.proved_without_evidence") == "error"
    assert review.get("claims.row_invalid") is not None
    expected = {"error"} if profile == "governed" else {"warning"}
    assert _verify_claims(tmp_path).get("claims.row_invalid") == expected


@pytest.mark.parametrize("profile", ["starter", "orchestrated", "governed"])
def test_only_row_without_leading_pipe_is_not_an_empty_ledger(tmp_path: Path, profile: str) -> None:
    """Seule ligne, sans barre initiale : le diagnostic n'est pas « le registre est vierge »."""
    setup_standard_profile(tmp_path, profile_id=profile, project_name="Demo")
    _write_row(tmp_path, _BAD_PROVED)
    assert "claims.empty" not in _review_claims(tmp_path)
    assert "claims.empty" not in _verify_claims(tmp_path)


def test_scan_covers_every_claims_section_and_pipeless_rows() -> None:
    from grimoire.core.standard_checks.claim_ledger_verify import scan_claim_rows

    text = (
        "## Claims\n\n| ID | A | T | P | S | C | D |\n|---|---|---|---|---|---|---|\n"
        "| CL-001 | a | fait | src | prouvé | haute | utiliser |\n"
        "ID | A | T | P | S | C | D\n---|---|---|---|---|---|---\n"
        "CL-002 | b | fait |  | prouvé | haute | utiliser\n\n"
        "## Autre\n\n| x | y |\n|---|---|\n| 1 | 2 |\n\n"
        "## Claims (suite)\n\nC3 | b | fait |  | prouvé | haute | utiliser\n"
        "Du texte sans barre.\n"
    )
    scan = scan_claim_rows(text)
    assert scan.candidate_count == 3
    assert len(scan.evaluated) == 2
    assert scan.unevaluated_count == 1


def test_pipeless_cl_row_is_a_candidate_outside_any_claims_section() -> None:
    from grimoire.core.standard_checks.claim_ledger_verify import scan_claim_rows

    scan = scan_claim_rows("# Ledger\n\nCL-002 | b | fait |  | prouvé | haute | utiliser\n")
    assert scan.candidate_count == 1
    assert scan.evaluated and scan.evaluated[0][1][0] == "CL-002"


def test_prose_without_a_pipe_is_not_a_candidate() -> None:
    from grimoire.core.standard_checks.claim_ledger_verify import scan_claim_rows

    text = "## Claims\n\nCL-002 est discuté ici.\nTypes : `a \\| b`.\n\n```\nnote\n```\n"
    assert scan_claim_rows(text).candidate_count == 0
