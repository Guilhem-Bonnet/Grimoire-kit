"""La traçabilité vers la norme est une donnée vérifiée, pas une affirmation.

Chaque artefact du profile-map a une ligne ; chaque profil a un niveau ; les
identifiants ont la forme de la norme ; le fichier parle de la même révision du
standard que le profile-map. Une entrée qui ne cite rien doit dire pourquoi.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.agentic_standard import DIMENSION_CHECK_PREFIXES, load_profile_map
from grimoire.core.standard_traceability import (
    CONTROL_ID,
    LEVELS,
    REQUIREMENT_ID,
    declared_artifact_types,
    load_traceability,
    matrix_for,
)

runner = CliRunner()


def test_every_declared_artifact_is_traced_and_nothing_else() -> None:
    assert set(load_traceability()["artifacts"]) == declared_artifact_types()


def test_every_profile_has_a_level_of_the_norm() -> None:
    profiles = {p["id"] for p in load_profile_map()["profiles"]}
    levels = load_traceability()["levels"]
    assert set(levels) == profiles
    assert set(levels.values()) <= set(LEVELS)
    for profile in load_profile_map()["profiles"]:
        assert profile.get("level") == levels[profile["id"]], profile["id"]


def test_identifiers_have_the_shape_of_the_norm() -> None:
    data = load_traceability()
    for name, entry in data["artifacts"].items():
        for req in entry.get("requirements") or []:
            assert REQUIREMENT_ID.match(req), f"{name}: {req}"
        for ctrl in entry.get("controls") or []:
            assert CONTROL_ID.match(ctrl), f"{name}: {ctrl}"
    for level, gaps in data["gaps"].items():
        assert level in LEVELS
        for gap in gaps or []:
            assert REQUIREMENT_ID.match(gap["id"]), gap


def test_an_entry_without_a_link_says_why() -> None:
    for name, entry in load_traceability()["artifacts"].items():
        if not entry.get("requirements"):
            assert entry.get("reason"), f"{name} ne cite rien et ne dit pas pourquoi"
        else:
            assert entry.get("evidence"), f"{name} cite une exigence sans justification"


def test_the_file_traces_the_pinned_revision() -> None:
    pinned = load_profile_map()["metadata"]["upstream_standard"]["commit"]
    assert load_traceability()["metadata"]["upstream_commit"] == pinned


def test_every_check_prefix_the_kit_scores_has_a_verifier_entry() -> None:
    prefixes = {v["prefix"] for v in load_traceability()["verifiers"].values()}
    for dimension, dim_prefixes in DIMENSION_CHECK_PREFIXES.items():
        for prefix in dim_prefixes:
            root = prefix.split(".")[0] + "."
            assert root in prefixes, f"{dimension}: {prefix} n'est rattaché à aucun contrôle"


def test_gaps_accumulate_with_the_level() -> None:
    assert len(matrix_for("starter").gaps) < len(matrix_for("governed").gaps)
    assert {g["id"] for g in matrix_for("starter").gaps} <= {g["id"] for g in matrix_for("governed").gaps}
    # AG-QUA-002 (claim ledger) est comblé depuis que le kit livre l'artefact ;
    # le dossier d'acceptation client, lui, reste un trou dès N1.
    assert "AG-QUA-003" in {g["id"] for g in matrix_for("starter").gaps}
    assert "AG-QUA-002" not in {g["id"] for g in matrix_for("governed").gaps}


def test_cli_renders_the_matrix_as_json() -> None:
    result = runner.invoke(app, ["-o", "json", "standard", "traceability", "--profile", "governed"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["level"] == "N4"
    required = [a for a in payload["artifacts"] if a["required"]]
    assert required and any(a["requirements"] for a in required)
