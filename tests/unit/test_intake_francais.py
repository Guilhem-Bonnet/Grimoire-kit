"""L'intake propose ses titres et justifications en français (GAO-i7-l-intake-001)."""

from __future__ import annotations

import re

from grimoire.missions.intake import intake_proposal

_GABARITS_ANGLAIS = re.compile(
    r"Write tests for|Primary task detected|Auto-suggested|Risk profile is", re.IGNORECASE
)


def _textes(proposal: dict) -> list[str]:
    textes = [p["title"] for p in proposal["task_proposals"]]
    textes += [p["rationale"] for p in proposal["task_proposals"]]
    textes += [h["reason"] for h in proposal["policy_hints"]]
    return textes


def test_idee_de_fonctionnalite_sans_gabarit_anglais() -> None:
    proposal = intake_proposal("Ajouter un export CSV des tâches")
    assert len(proposal["task_proposals"]) == 2
    assert proposal["task_proposals"][1]["title"].startswith("Écrire les tests : ")
    for texte in _textes(proposal):
        assert not _GABARITS_ANGLAIS.search(texte), texte


def test_risque_strict_justifie_en_francais() -> None:
    proposal = intake_proposal("delete all database and deploy to prod")
    raisons = [h["reason"] for h in proposal["policy_hints"] if h["reason"]]
    assert raisons and all(r.startswith("Profil de risque") for r in raisons)


def test_types_et_risques_inchanges() -> None:
    proposal = intake_proposal("Implement a new login page")
    primaire, tests = proposal["task_proposals"]
    assert (primaire["task_type"], tests["task_type"]) == ("implementation", "test")
    assert tests["risk_profile"] == "light"
