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


# ── Le pare-feu vérifie un câblage, pas un YAML ───────────────────────────────


_MANIFEST = Path("_grimoire/kit/tool-manifest.csv")

_HEADER = "name,file,description,entrypoint\n"
_WRAPPED = f"{_HEADER}web-browser,web-browser.py,Navigateur sandboxé,grimoire web fetch\n"
_NAKED = f"{_HEADER}web-browser,web-browser.py,Navigateur sandboxé,\n"


def _write_manifest(root: Path, content: str) -> None:
    (root / _MANIFEST).parent.mkdir(parents=True, exist_ok=True)
    (root / _MANIFEST).write_text(content, encoding="utf-8")


def _firewall_checks(result) -> list:
    return [c for c in result.checks if c.id == "firewall.untrusted_output_unwrapped"]


def test_un_manifeste_qui_pointe_le_script_nu_echoue_en_production(tmp_path: Path) -> None:
    """Le trou relevé par la revue : `_verify_prompt_firewall` ne contrôlait
    qu'un YAML déclaratif. Un projet pouvait cocher `isolate_external_content:
    true` et laisser ses agents appeler le navigateur nu."""
    setup_standard_profile(tmp_path, profile_id="production", project_name="Demo")
    _write_manifest(tmp_path, _NAKED)
    result = verify_standard_profile(tmp_path)
    found = _firewall_checks(result)
    assert found and found[0].severity == "error"
    assert "grimoire web fetch" in found[0].message
    assert not result.ok


def test_le_meme_manifeste_avertit_seulement_en_governed(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="governed", project_name="Demo")
    _write_manifest(tmp_path, _NAKED)
    result = verify_standard_profile(tmp_path)
    found = _firewall_checks(result)
    assert found and found[0].severity == "warning"


def test_un_manifeste_cable_passe(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="production", project_name="Demo")
    _write_manifest(tmp_path, _WRAPPED)
    result = verify_standard_profile(tmp_path)
    assert _firewall_checks(result) == []


def test_un_projet_sans_manifeste_ne_declenche_rien(tmp_path: Path) -> None:
    """Pas de navigateur livré, pas de câblage à exiger."""
    setup_standard_profile(tmp_path, profile_id="production", project_name="Demo")
    result = verify_standard_profile(tmp_path)
    assert _firewall_checks(result) == []


def test_le_scaffold_livre_le_manifeste_cable(tmp_path: Path) -> None:
    """Ce que le kit installe doit satisfaire ce que le kit vérifie."""
    from grimoire.core.scaffold import UNTRUSTED_OUTPUT_ENTRYPOINTS

    assert UNTRUSTED_OUTPUT_ENTRYPOINTS["web-browser.py"] == "grimoire web fetch"


# ── Le catalogue de résolution livré, pas seulement le manifeste ──────────────


_RESOLVER = Path("_grimoire/kit/tools/tool-resolver.py")

_RESOLVER_NAKED = '''CAPABILITY_CATALOG = {
    "testing": {"description": "tests", "providers": []},
    "web-browsing": {
        "description": "Navigation web, scraping, screenshots",
        "providers": [
            {
                "id": "web-browser-grimoire",
                "type": "grimoire_tool",
                "check": {"method": "grimoire_tool", "tool": "web-browser.py"},
            },
        ],
    },
    "documentation": {"description": "docs", "providers": []},
}
'''

_RESOLVER_WRAPPED = _RESOLVER_NAKED.replace(
    '"type": "grimoire_tool",\n                "check": {"method": "grimoire_tool", "tool": "web-browser.py"},',
    '"type": "cli_command",\n                "check": {"method": "command", "command": "grimoire web fetch --help"},',
)


def _write_resolver(root: Path, content: str) -> None:
    (root / _RESOLVER).parent.mkdir(parents=True, exist_ok=True)
    (root / _RESOLVER).write_text(content, encoding="utf-8")


def _resolver_checks(result) -> list:
    return [c for c in result.checks if c.id == "firewall.capability_resolves_unwrapped"]


def test_un_resolver_qui_pointe_le_script_nu_echoue_en_production(tmp_path: Path) -> None:
    """Le trou du deuxième re-contrôle : le manifeste pouvait être câblé pendant
    que le catalogue de résolution de capacité, lui, envoyait toujours les
    agents sur `web-browser.py`. Le contrôle ne lisait que le manifeste."""
    setup_standard_profile(tmp_path, profile_id="production", project_name="Demo")
    _write_manifest(tmp_path, _WRAPPED)
    _write_resolver(tmp_path, _RESOLVER_NAKED)
    result = verify_standard_profile(tmp_path)
    found = _resolver_checks(result)
    assert found and found[0].severity == "error"
    assert "web-browsing" in found[0].message
    assert "grimoire web fetch" in found[0].message
    assert not result.ok


def test_le_meme_resolver_avertit_seulement_en_governed(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="governed", project_name="Demo")
    _write_resolver(tmp_path, _RESOLVER_NAKED)
    result = verify_standard_profile(tmp_path)
    found = _resolver_checks(result)
    assert found and found[0].severity == "warning"


def test_un_resolver_cable_passe(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="production", project_name="Demo")
    _write_resolver(tmp_path, _RESOLVER_WRAPPED)
    result = verify_standard_profile(tmp_path)
    assert _resolver_checks(result) == []


def test_un_projet_sans_resolver_ne_declenche_rien(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="production", project_name="Demo")
    result = verify_standard_profile(tmp_path)
    assert _resolver_checks(result) == []


def test_le_resolver_du_kit_satisfait_sa_propre_regle() -> None:
    """Ce que le kit livre doit passer ce que le kit vérifie.

    Sans ce test, la redirection de `framework/tools/tool-resolver.py` pourrait
    être défaite et seuls les projets consommateurs le verraient.
    """
    from grimoire.core.standard_checks.controls import capability_resolves_wrapped
    from grimoire.data import framework_path

    source = (framework_path() / "tools" / "tool-resolver.py").read_text(encoding="utf-8")
    assert capability_resolves_wrapped(source) is True
