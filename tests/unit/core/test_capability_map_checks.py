"""Garde-fou de la carte des capacités (issue #636).

Dix patterns de ``capability-map.yaml`` déclaraient ``checks: []`` : un profil
qui les exigeait n'avait aucun vérificateur derrière — une promesse sans
contrôle. ``test_declarative_refs_resolve`` (``tests/test_agentic_standard.py``)
vérifie le sens catalogue → code ; ce module ferme l'autre sens, carte →
registre, et interdit à la liste vide de revenir.
"""

from __future__ import annotations

from pathlib import Path

from ruamel.yaml import YAML

from grimoire.core.agentic_standard import load_capability_map
from grimoire.core.standard_checks.registry import CHECK_DIMENSIONS
from grimoire.data import framework_path


def _catalog_check_refs() -> dict[str, list[str]]:
    path = framework_path() / "agentic-standard" / "templates" / "pattern-catalog.yaml"
    data = YAML(typ="safe").load(path)
    return {str(p["id"]): list(p.get("check_refs") or []) for p in data["patterns"]}


def test_chaque_pattern_de_la_carte_a_au_moins_un_check_present_au_registre() -> None:
    """Critère de clôture de #636 : aucun ``checks: []``, aucun code inconnu du registre."""
    patterns = load_capability_map()["patterns"]
    sans_check = sorted(pid for pid, spec in patterns.items() if not spec.get("checks"))
    assert sans_check == [], f"patterns sans vérificateur : {sans_check}"
    inconnus = sorted(
        f"{pid}:{check}"
        for pid, spec in patterns.items()
        for check in spec["checks"]
        if check not in CHECK_DIMENSIONS
    )
    assert inconnus == [], f"checks déclarés dans la carte mais absents du registre : {inconnus}"


def test_le_fichier_ne_contient_plus_de_liste_vide() -> None:
    """La forme textuelle que l'issue cite comme critère (``grep -c 'checks: \\[\\]'`` = 0)."""
    text = Path(framework_path() / "agentic-standard" / "capability-map.yaml").read_text(encoding="utf-8")
    assert "checks: []" not in text


def test_le_catalogue_scaffolde_cite_les_memes_checks_que_la_carte() -> None:
    """Deux lecteurs, une seule liste : ``check_refs`` du gabarit = ``checks`` de la carte."""
    patterns = load_capability_map()["patterns"]
    catalog = _catalog_check_refs()
    ecarts = {
        pid: (list(spec["checks"]), catalog.get(pid))
        for pid, spec in patterns.items()
        if list(spec["checks"]) != catalog.get(pid)
    }
    assert ecarts == {}, f"carte et catalogue divergent : {ecarts}"
