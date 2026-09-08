"""`prompt_firewall` : requis en `production`, attendu en `governed` (B12).

Le template `prompt-firewall.yaml` existait, la capacité
`prompt-injection-firewall` était catalogue, et aucun profil ne l'exigeait —
pas même `production`. Un pare-feu que personne ne réclame est un verrou
décoratif.

Le rendre obligatoire dès `governed` aurait rendu tout projet consommateur
non conforme du jour au lendemain : d'où l'avertissement à ce palier et
l'exigence dure au suivant (décision 2 du plan d'exécution du 2026-09-08).
"""

from __future__ import annotations

from pathlib import Path

from grimoire.core.agentic_standard import (
    get_profile,
    setup_standard_profile,
    verify_standard_profile,
)

_ARTIFACT = Path("_grimoire/standard/prompt-firewall.yaml")


def test_production_exige_l_artefact() -> None:
    assert "prompt_firewall" in get_profile("production").required_artifacts


def test_governed_ne_l_exige_pas() -> None:
    assert "prompt_firewall" not in get_profile("governed").required_artifacts


def test_un_projet_production_le_recoit_et_verifie(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="production", project_name="Demo")
    assert (tmp_path / _ARTIFACT).is_file()
    result = verify_standard_profile(tmp_path)
    assert _ARTIFACT not in result.missing
    assert not [c for c in result.checks if c.id.startswith("firewall.") and c.severity == "error"]


def test_un_projet_production_sans_artefact_echoue(tmp_path: Path) -> None:
    """Le garde qui manquait : retirer le fichier doit faire tomber la vérification."""
    setup_standard_profile(tmp_path, profile_id="production", project_name="Demo")
    (tmp_path / _ARTIFACT).unlink()
    result = verify_standard_profile(tmp_path)
    assert _ARTIFACT in result.missing
    assert not result.ok


def test_un_projet_governed_sans_artefact_est_averti_pas_bloque(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="governed", project_name="Demo")
    assert not (tmp_path / _ARTIFACT).exists()
    result = verify_standard_profile(tmp_path)
    found = [c for c in result.checks if c.id == "firewall.artifact_missing"]
    assert found and found[0].severity == "warning"
    assert _ARTIFACT not in result.missing
