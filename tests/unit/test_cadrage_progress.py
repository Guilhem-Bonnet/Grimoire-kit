"""L'avancement honnête d'un chantier : un seul calcul, jamais « complet » avec un bloc resté au gabarit."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.core import cadrage_interview as interview
from grimoire.core.cadrage import PHASES
from grimoire.core.cadrage_progress import progress
from grimoire.core.chantiers import new_chantier


@pytest.fixture
def root(tmp_path: Path) -> Path:
    new_chantier(tmp_path, "Un chantier", slug="un")
    return tmp_path


def _fill_everything(root: Path) -> None:
    """Remplit chaque rubrique de chaque phase (fichiers écrits à la main, comme le ferait une personne)."""
    for phase in PHASES:
        lines = ["---", f"phase: {phase.id}", "---", "", f"# {phase.title}", ""]
        for section in phase.sections:
            lines += [f"## {section}", "", f"- contenu de {section}", ""]
        (root / "_grimoire" / "cadrage" / "un" / phase.filename).write_text("\n".join(lines), encoding="utf-8")


def test_un_gabarit_intact_est_a_cadrer(root: Path) -> None:
    av = progress(root, "un")
    assert av["etat"] == "a-cadrer"
    assert av["a_preciser"] == sum(len(p.sections) for p in PHASES)
    assert all(p["remplis"] == 0 and p["vides"] == p["total"] for p in av["phases"])
    assert "rien" in av["resume"]


def test_une_reponse_remplit_son_bloc_et_le_chantier_est_en_cours(root: Path) -> None:
    interview.interview_plan(root, "un", depth="standard")
    interview.answer(root, "un", "utilisateurs", "les mainteneurs")
    av = progress(root, "un")
    brief = next(p for p in av["phases"] if p["id"] == "brief")
    assert brief["sections"]["L'utilisateur"] == "rempli"
    assert brief["remplis"] == 1
    assert av["etat"] == "en-cours"
    assert "au gabarit" in av["resume"]


def test_je_ne_sais_pas_et_sans_objet_comptent_comme_couverts_sans_remplir(root: Path) -> None:
    interview.interview_plan(root, "un", depth="standard")
    interview.answer(root, "un", "utilisateurs", "", unknown=True)
    interview.answer(root, "un", "criteres", "sans objet", skip=True)
    av = progress(root, "un")
    brief = next(p for p in av["phases"] if p["id"] == "brief")
    assert brief["sections"]["L'utilisateur"] == "je-ne-sais-pas"
    assert brief["sections"]["Un succès ressemble à"] == "sans-objet"  # gabarit intact, mais l'angle est écarté
    assert brief["sections"]["Le problème"] == "vide"  # une rubrique sans angle reste au gabarit
    assert brief["je_ne_sais_pas"] == 1
    assert av["a_confirmer"] == 1
    assert av["angles"]["couverts"] == 2  # les deux comptent comme couverts pour l'entretien


def test_un_angle_ecarte_marque_sa_rubrique_sans_objet(root: Path) -> None:
    interview.interview_plan(root, "un", depth="standard")
    interview.answer(root, "un", "contraintes", "sans objet", skip=True)
    comprehension = next(p for p in progress(root, "un")["phases"] if p["id"] == "comprehension")
    assert comprehension["sections"]["Contraintes"] == "sans-objet"
    assert comprehension["sans_objet"] == 1


def test_angles_obligatoires_couverts_mais_gabarit_restant_donne_cadre_avec_n_points(root: Path) -> None:
    interview.interview_plan(root, "un", depth="express")
    interview.answer(root, "un", "utilisateurs", "les mainteneurs")
    interview.answer(root, "un", "criteres", "un test vert")
    interview.answer(root, "un", "hors-perimetre", "le reste")
    av = progress(root, "un")
    assert av["angles"]["complet"] is True
    assert av["etat"] == "cadre"  # JAMAIS « complet » : des rubriques sont encore au gabarit
    assert av["a_preciser"] > 0
    assert f"{av['a_preciser']} point" in av["resume"]
    assert "à préciser" in av["resume"]


def test_complet_seulement_sans_aucun_gabarit(root: Path) -> None:
    interview.interview_plan(root, "un", depth="express")
    _fill_everything(root)
    av = progress(root, "un")
    assert av["etat"] == "complet"
    assert av["a_preciser"] == 0
    assert av["complet_phases"] == len(PHASES)


def test_un_je_ne_sais_pas_ne_suffit_pas_a_remplir_les_autres_rubriques_mais_le_dit(root: Path) -> None:
    interview.interview_plan(root, "un", depth="express")
    _fill_everything(root)
    brief = root / "_grimoire" / "cadrage" / "un" / "01-brief.md"
    brief.write_text(brief.read_text(encoding="utf-8").replace("- contenu de L'utilisateur", "> À compléter — l'utilisateur."), encoding="utf-8")
    assert progress(root, "un")["etat"] != "complet"  # un seul bloc au gabarit suffit à refuser « complet »
    interview.answer(root, "un", "utilisateurs", "", unknown=True)
    av = progress(root, "un")
    assert av["etat"] == "complet"  # couvert : « je ne sais pas » est dit, pas caché
    assert av["a_confirmer"] == 1
    assert "dont 1 marqué" in av["resume"]


def test_un_chantier_sans_fichiers_n_est_ni_complet_ni_en_erreur(tmp_path: Path) -> None:
    av = progress(tmp_path, None)
    assert av["etat"] == "a-cadrer"
    assert not av["erreur"]


def test_un_entretien_abime_est_dit_sans_casser(root: Path) -> None:
    (root / "_grimoire" / "cadrage" / "un" / "entretien.json").write_text("{pas du json", encoding="utf-8")
    av = progress(root, "un")
    assert av["erreur"]
    assert av["etat"] == "a-cadrer"
