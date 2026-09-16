"""Tous les `archetype.dna.yaml` livrés ne référencent que des agents réels.

Régression #550 : `archetypes/minimal/archetype.dna.yaml` et
`archetypes/agentic-standard/archetype.dna.yaml` référençaient
`project-navigator` et `memory-keeper`, retirés du paquet par la refonte
meta (#387, commit f636633c) sans que les DNA en aval soient mis à jour.
Ce test parcourt tous les DNA livrés et échoue si un agent référencé n'a
pas de fichier `.md` livré à côté.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.archetypes import bundled_path
from grimoire.registry.agents import ArchetypeDNA


def _dna_paths() -> list[Path]:
    root = bundled_path()
    return sorted(root.glob("*/archetype.dna.yaml")) + sorted(root.glob("*/*/archetype.dna.yaml"))


@pytest.mark.parametrize("dna_path", _dna_paths(), ids=lambda p: str(p.relative_to(bundled_path())))
def test_dna_agents_have_a_delivered_file(dna_path: Path) -> None:
    dna = ArchetypeDNA.from_yaml(dna_path)
    missing = [(a.id, str(a.path)) for a in dna.agents if not a.exists]
    assert not missing, (
        f"{dna_path.relative_to(bundled_path())} référence des agents sans fichier livré : {missing}"
    )


def test_at_least_one_dna_is_checked() -> None:
    # Garde-fou : si le glob ne trouve plus rien, le test paramétré ci-dessus
    # devient silencieusement vide et ne protège plus rien.
    assert len(_dna_paths()) >= 5
