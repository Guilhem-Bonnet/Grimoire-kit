"""Deux artefacts que la norme rend obligatoires et que le kit ne livrait pas.

`claim-ledger` (AG-QUA-002, dès N1) et `runtime-surface-registry` (AG-TOL-007,
AG-RET-006, dès N4). Un projet neuf les reçoit ; un registre vierge est un
avertissement, pas une erreur — il attend d'être rempli ; ce qui est une
erreur, c'est une affirmation dite prouvée sans preuve, ou une surface sans
owner en profil gouverné.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.core.agentic_standard import setup_standard_profile, verify_standard_profile


def _ids(result, prefix: str) -> set[str]:
    return {c.id for c in result.checks if c.id.startswith(prefix)}


def test_starter_ships_a_claim_ledger_and_not_the_registry(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="starter", project_name="Demo")
    assert (tmp_path / "_grimoire-output/evidence/bootstrap/claim-ledger.md").is_file()
    assert not (tmp_path / "_grimoire/standard/runtime-surface-registry.yaml").exists()


def test_governed_ships_both_and_still_verifies(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="governed", project_name="Demo")
    assert (tmp_path / "_grimoire/standard/runtime-surface-registry.yaml").is_file()
    result = verify_standard_profile(tmp_path)
    assert "claims.empty" in _ids(result, "claims.")
    assert "surfaces.no_control_surface" in _ids(result, "surfaces.")
    assert not [c for c in result.checks if c.id.startswith(("claims.", "surfaces.")) and c.severity == "error"]


def test_a_claim_marked_proved_without_evidence_is_an_error(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="starter", project_name="Demo")
    ledger = tmp_path / "_grimoire-output/evidence/bootstrap/claim-ledger.md"
    text = ledger.read_text(encoding="utf-8").replace(
        "| CL-001 |  | fait |  | hypothèse | faible | vérifier |",
        "| CL-001 | Les tests passent | résultat |  | prouvé | élevée | utiliser |",
    )
    ledger.write_text(text, encoding="utf-8")
    result = verify_standard_profile(tmp_path)
    assert any(c.id == "claims.proved_without_evidence" and c.severity == "error" for c in result.checks)


def test_using_an_unproved_claim_is_an_error_only_when_governed(tmp_path: Path) -> None:
    for profile, severity in (("starter", "warning"), ("governed", "error")):
        root = tmp_path / profile
        setup_standard_profile(root, profile_id=profile, project_name="Demo")
        ledger = root / "_grimoire-output/evidence/bootstrap/claim-ledger.md"
        ledger.write_text(ledger.read_text(encoding="utf-8").replace(
            "| CL-001 |  | fait |  | hypothèse | faible | vérifier |",
            "| CL-001 | L'API accepte le JSON | hypothèse | — | hypothèse | moyenne | utiliser |",
        ), encoding="utf-8")
        result = verify_standard_profile(root)
        found = [c for c in result.checks if c.id == "claims.used_unproved"]
        assert found and found[0].severity == severity, (profile, found)


def test_a_control_surface_without_owner_is_an_error_when_governed(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="governed", project_name="Demo")
    registry = tmp_path / "_grimoire/standard/runtime-surface-registry.yaml"
    text = registry.read_text(encoding="utf-8").replace(
        "control_surfaces: []",
        'control_surfaces:\n  - id: CTRL-001\n    surface: ".claude/settings.json"\n    type: hook\n    mode: enforced\n    risk: moyen\n    status: active\n',
    )
    registry.write_text(text, encoding="utf-8")
    result = verify_standard_profile(tmp_path)
    assert any(c.id == "surfaces.control_owner_missing" and c.severity == "error" for c in result.checks)
    assert "surfaces.no_control_surface" not in _ids(result, "surfaces.")
