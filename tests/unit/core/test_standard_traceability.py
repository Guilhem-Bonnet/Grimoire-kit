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


# Les dix-sept exigences obligatoires que la matrice du 2026-09-03 laissait
# sans artefact, avec le premier niveau où la norme les attend. Chacune doit
# être portée par un artefact *requis* par le profil de ce niveau — sinon le
# trou est revenu, quoi que dise la section gaps.
SEVENTEEN = {
    "AG-QUA-003": "starter", "AG-RET-001": "starter",
    "AG-TOL-001": "controlled", "AG-TOL-003": "controlled", "AG-TOL-005": "controlled",
    "AG-INC-001": "controlled", "AG-RET-003": "controlled", "AG-RET-004": "controlled",
    "AG-RET-005": "controlled",
    "AG-ORC-004": "orchestrated",
    "AG-INC-002": "governed", "AG-INC-003": "governed", "AG-DYN-001": "governed",
    "AG-DYN-003": "governed", "AG-DYN-004": "governed", "AG-AUD-001": "governed",
    "AG-AUD-003": "governed",
}


def test_the_seventeen_former_gaps_are_covered_at_their_level() -> None:
    for requirement, profile in SEVENTEEN.items():
        assert requirement in matrix_for(profile).covered_requirements, (requirement, profile)


def test_no_level_leaves_an_unjustified_gap() -> None:
    for profile in ("starter", "controlled", "orchestrated", "governed", "production"):
        assert matrix_for(profile).gaps == (), profile


def test_cli_renders_the_matrix_as_json() -> None:
    result = runner.invoke(app, ["-o", "json", "standard", "traceability", "--profile", "governed"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["level"] == "N4"
    required = [a for a in payload["artifacts"] if a["required"]]
    assert required and any(a["requirements"] for a in required)
