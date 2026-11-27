"""Dire ce qu'on attend, rubrique par rubrique : invites, statuts tirés du gate, reste à faire, « sans objet ».

Le point central : le statut vient de la vérité du kit (``Phase.gate``, ``check_interview``, les cibles des angles), et marquer
une rubrique « sans objet » (ligne qui commence par le gabarit) ne change JAMAIS le résultat de ``cadrage check``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from grimoire.core import cadrage
from grimoire.core import cadrage_guidance as guidance
from grimoire.core import cadrage_interview as interview
from grimoire.core.chantiers import new_chantier
from grimoire.tools import workspace_atelier as wa
from grimoire.tools import workspace_atelier_guide as guide

P = "/api/workspace/"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "project-context.yaml").write_text("project:\n  name: demo\n", encoding="utf-8")
    wa.set_atelier(tmp_path, True)
    new_chantier(tmp_path, "Un chantier", slug="un")
    return tmp_path


def _doc(root: Path) -> dict[str, Any]:
    return wa.document(root, "un")


def _rub(doc: dict[str, Any], phase: str, section: str) -> dict[str, Any]:
    p = next(p for p in doc["phases"] if p["id"] == phase)
    return next(r for r in p["rubriques"] if r["section"] == section)


def _put(root: Path, phase: str, section: str, text: str) -> dict[str, Any]:
    """Réécrit le bloc d'une rubrique par la route d'édition de bloc existante, contrôle de conflit compris."""
    view = next(p for p in _doc(root)["phases"] if p["id"] == phase)
    block = next(b for b in view["blocks"] if b["section"] == section and b["kind"] != "rubrique")
    body = {"text": text, "block_fp": block["fp"], "prev": block["prev"], "next": block["next"], "twins": block["twins"],
            "base": block["text"]}
    status, out = wa.put_http(root, f"{P}atelier/chantiers/un/blocs/{block['id']}", json.dumps(body).encode())
    assert status == 200, out
    return out


def _fill(root: Path, phase: str, section: str, text: str = "Du contenu réel.") -> None:
    _put(root, phase, section, text)


def test_chaque_rubrique_a_son_invite_et_la_table_suit_les_phases() -> None:
    assert set(guidance.GUIDE) == {(p.id, s) for p in cadrage.PHASES for s in p.sections}
    for (phase, section), (quoi, exemple) in guidance.GUIDE.items():
        assert quoi.strip() and exemple.strip(), (phase, section)
        assert "MOA" not in quoi + exemple


def test_le_jargon_est_explique_quand_il_est_inevitable() -> None:
    quoi, _ = guidance.GUIDE[("exigences", "Priorisation (MoSCoW)")]
    assert "indispensable" in quoi and "Must" in quoi
    quoi, _ = guidance.GUIDE[("exigences", "Fonctionnelles")]
    assert "Must (indispensable)" in quoi


def test_statuts_depuis_le_gate_et_l_entretien(project: Path) -> None:
    doc = _doc(project)
    for phase in cadrage.PHASES:
        for section in phase.sections:
            r = _rub(doc, phase.id, section)
            if phase.gate:
                assert r["statut"] == "necessaire", (phase.id, section)  # `check` exige toute la phase
            else:
                assert r["statut"] in {"utile", "facultatif"}
    # sans entretien entamé, aucun angle n'est « obligatoire » : seule la phase à gate est nécessaire
    assert _rub(doc, "brief", "Le problème")["statut"] == "utile"  # cible de la 2e question de l'angle « utilisateurs »
    assert _rub(doc, "comprehension", "Faits")["statut"] == "utile"
    assert _rub(doc, "brief", "Pourquoi maintenant")["statut"] == "facultatif"
    assert _rub(doc, "comprehension", "Hypothèses")["statut"] == "facultatif"
    assert _rub(doc, "brief", "Le problème")["statut_libelle"] == "utile"
    assert _rub(doc, "brief", "Pourquoi maintenant")["statut_libelle"] == "peut rester vide"
    assert _rub(doc, "exigences", "Fonctionnelles")["statut_libelle"] == "nécessaire pour valider"


def test_un_entretien_entame_rend_necessaires_les_angles_obligatoires_ouverts(project: Path) -> None:
    interview.interview_plan(project, "un", depth="standard")
    doc = _doc(project)
    assert _rub(doc, "brief", "L'utilisateur")["statut"] == "necessaire"
    assert _rub(doc, "brief", "Un succès ressemble à")["statut"] == "necessaire"
    assert _rub(doc, "comprehension", "Contraintes")["statut"] == "necessaire"
    assert _rub(doc, "comprehension", "Faits")["statut"] == "utile"  # l'angle « existant » n'est pas obligatoire en standard
    interview.answer(project, "un", "criteres", "On voit la liste en une seconde.")
    assert _rub(_doc(project), "brief", "Un succès ressemble à")["statut"] == "utile"  # l'angle est couvert : plus exigé


def test_reste_a_faire_liste_le_necessaire_puis_le_facultatif(project: Path) -> None:
    reste = _doc(project)["reste_a_faire"]
    assert reste["pret"] is False
    assert {(i["phase"], i["section"]) for i in reste["necessaires"]} == {
        (p.id, s) for p in cadrage.PHASES if p.gate for s in p.sections
    }
    assert reste["sans_objet"] == 0
    assert {i["section"] for i in reste["facultatives"]} >= {"Pourquoi maintenant", "Hypothèses"}
    assert {i["section"] for i in reste["utiles"]} >= {"Le problème", "Faits"}


def test_le_critere_du_reste_a_faire_est_celui_du_gate(project: Path) -> None:
    """``pret`` est vrai exactement quand ``cadrage check`` ne rend aucune erreur (rubriques et entretien)."""
    interview.interview_plan(project, "un", depth="standard")

    def agree() -> bool:
        errors, _ = cadrage.check(project, "un")
        pret = _doc(project)["reste_a_faire"]["pret"]
        assert pret is (errors == []), (pret, errors)
        return pret

    assert agree() is False
    for phase in cadrage.PHASES:
        if phase.gate:
            for section in phase.sections:
                _fill(project, phase.id, section)
    assert agree() is False  # les angles obligatoires restent ouverts
    items = _doc(project)["reste_a_faire"]["necessaires"]
    assert items and all(i["kind"] == "angle" or i["etat"] != "rempli" for i in items)
    for angle in ("utilisateurs", "criteres", "hors-perimetre", "contraintes", "risques"):
        interview.answer(project, "un", angle, "Une réponse concrète.")
    assert agree() is True
    reste = _doc(project)["reste_a_faire"]
    assert reste["necessaires"] == [] and reste["pret"] is True


def test_un_angle_couvert_a_la_main_reste_a_faire_meme_si_la_rubrique_est_remplie(project: Path) -> None:
    interview.interview_plan(project, "un", depth="express")
    _fill(project, "brief", "L'utilisateur")  # remplie à la main : l'angle « utilisateurs » reste ouvert pour le gate
    items = _doc(project)["reste_a_faire"]["necessaires"]
    angle = [i for i in items if i["kind"] == "angle"]
    assert any(i["section"] == "L'utilisateur" for i in angle)
    assert any("Entretien" in e for e in cadrage.check(project, "un")[0])


def _snapshot(root: Path) -> tuple[list[str], list[str], str]:
    errors, warnings = cadrage.check(root, "un")
    status = json.dumps(cadrage.status(root, "un"), sort_keys=True)
    return errors, warnings, status


@pytest.mark.parametrize(
    ("phase", "section"),
    [("brief", "Pourquoi maintenant"), ("exigences", "Fonctionnelles"), ("cahier-des-charges", "Jalons"), ("brief", "L'utilisateur")],
)
def test_sans_objet_ne_change_pas_le_gate(project: Path, phase: str, section: str) -> None:
    """Le marquage « sans objet » est une ligne de gabarit : `check` et `status` rendent exactement la même chose."""
    interview.interview_plan(project, "un", depth="standard")
    before = _snapshot(project)
    marker = f"{guidance.SANS_OBJET} pour ce besoin : ça ne me concerne pas (à confirmer)"
    _put(project, phase, section, marker)
    assert _snapshot(project) == before
    rub = _rub(_doc(project), phase, section)
    assert rub["etat"] == "sans-objet" and rub["raison"] == "ça ne me concerne pas"
    # et on revient en arrière : le gabarit exact du kit
    _put(project, phase, section, rub["gabarit"])
    assert _snapshot(project) == before
    assert _rub(_doc(project), phase, section)["etat"] == "vide"


def test_une_rubrique_necessaire_sans_objet_reste_dans_le_reste_a_faire(project: Path) -> None:
    _put(project, "exigences", "Non-fonctionnelles", f"{guidance.SANS_OBJET} pour ce besoin")
    reste = _doc(project)["reste_a_faire"]
    item = next(i for i in reste["necessaires"] if i["section"] == "Non-fonctionnelles")
    assert item["etat"] == "sans-objet" and item["raison"] == ""  # à confirmer pour valider
    assert reste["pret"] is False and reste["sans_objet"] == 1
    assert any("Non-fonctionnelles" in e for e in cadrage.check(project, "un")[0])


def test_une_rubrique_facultative_sans_objet_sort_du_facultatif(project: Path) -> None:
    before = {i["section"] for i in _doc(project)["reste_a_faire"]["facultatives"]}
    assert "Pourquoi maintenant" in before
    _put(project, "brief", "Pourquoi maintenant", f"{guidance.SANS_OBJET} pour ce besoin : pas de date")
    after = _doc(project)["reste_a_faire"]
    assert "Pourquoi maintenant" not in {i["section"] for i in after["facultatives"]}
    assert after["sans_objet"] == 1


def test_le_sans_objet_de_l_entretien_est_reconnu_avec_sa_raison(project: Path) -> None:
    interview.interview_plan(project, "un", depth="standard")
    interview.answer(project, "un", "hors-perimetre", "ne me concerne pas", skip=True)
    rub = _rub(_doc(project), "cahier-des-charges", "Hors périmètre")
    assert rub["etat"] == "sans-objet" and rub["raison"] == "ne me concerne pas"


def test_une_reponse_remplace_le_marqueur(project: Path) -> None:
    _put(project, "brief", "Le problème", f"{guidance.SANS_OBJET} pour ce besoin")
    interview.interview_plan(project, "un", depth="standard")
    interview.answer(project, "un", "utilisateurs", "Les gens perdent leurs notes.")
    # la 1re question d'« utilisateurs » vise « L'utilisateur » ; la 2e, « Le problème »
    interview.answer(project, "un", "utilisateurs", "Elles sont dans trois fichiers.")
    rub = _rub(_doc(project), "brief", "Le problème")
    assert rub["etat"] == "rempli"
    text = (project / "_grimoire/cadrage/un/01-brief.md").read_text(encoding="utf-8")
    assert guidance.SANS_OBJET not in text


def test_l_ecriture_d_un_bloc_rend_les_invites_et_le_reste_a_faire(project: Path) -> None:
    out = _put(project, "brief", "Le problème", "Je perds mes idées.")
    assert out["reste_a_faire"]["pret"] is False
    assert _rub({"phases": [out["phase"]]}, "brief", "Le problème")["etat"] == "rempli"


def test_la_lecture_n_ecrit_rien(project: Path) -> None:
    folder = project / "_grimoire/cadrage/un"
    before = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}
    _doc(project)
    assert {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()} == before


def test_un_entretien_abime_ne_casse_pas_le_document(project: Path) -> None:
    (project / "_grimoire/cadrage/un/entretien.json").write_text("{pas du json", encoding="utf-8")
    reste = _doc(project)["reste_a_faire"]
    assert reste["pret"] is False and reste["erreur"]


def test_chantier_vide_aucun_fichier_de_phase(project: Path) -> None:
    for phase in cadrage.PHASES:
        (project / "_grimoire/cadrage/un" / phase.filename).unlink()
    doc = _doc(project)
    assert all(r["etat"] == "vide" for p in doc["phases"] for r in p["rubriques"])
    assert doc["reste_a_faire"]["pret"] is False


def test_le_guide_dit_ou_ira_la_reponse_et_ou_elle_est_rangee(project: Path) -> None:
    interview.interview_plan(project, "un", depth="standard")
    shown = guide.view(project, "un")
    first = shown["current"]
    assert (first["angle"], first["index"]) == ("utilisateurs", 0)
    assert (first["target_section"], first["target_phase_title"]) == ("L'utilisateur", cadrage.PHASES[0].title)
    interview.answer(project, "un", "utilisateurs", "Une cheffe de projet.")
    shown = guide.view(project, "un")
    assert shown["current"]["target_section"] == "Le problème"  # la 2e question vise une autre rubrique que l'angle
    assert shown["previous"]["stored"] == {
        "kind": "answer", "phase": "brief", "phase_title": cadrage.PHASES[0].title, "section": "L'utilisateur",
    }
    interview.answer(project, "un", "utilisateurs", "", unknown=True)
    assert guide.view(project, "un")["previous"]["stored"]["section"] == "Questions ouvertes"
    interview.answer(project, "un", "criteres", "sans objet", skip=True)
    stored = guide.view(project, "un")["previous"]["stored"]
    assert (stored["kind"], stored["section"]) == ("skip", "Un succès ressemble à")
