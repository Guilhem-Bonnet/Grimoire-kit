"""La machine à états : l'étape suivante se déduit de l'avancement réel, par des règles explicables."""

from __future__ import annotations

from typing import Any

from grimoire.core.cadrage_conversation import Conversation, Question
from grimoire.core.cadrage_conversation_flow import (
    INTENTIONS,
    NOMS,
    AngleCadre,
    Cadre,
    cadre_from_view,
    ce_qui_manque,
    prochaine_etape,
    resume,
)
from grimoire.core.cadrage_interview import ANGLES, UNKNOWN_PREFIX

NOW = "2026-11-01T09:00:00+00:00"
REQUIRED = ("utilisateurs", "criteres", "hors-perimetre", "contraintes", "risques")


def _angle(a: Any, *, required: bool, answers: int = 0, state: str = "ouvert", per: int = 2, unknown: bool = False, reason: str = "") -> AngleCadre:
    return AngleCadre(a.id, a.title, required, state, a.seeds[:per], answers, reason, unknown)


def _cadre(done: dict[str, int] | None = None, **kw: Any) -> Cadre:
    done = done or {}
    return Cadre(tuple(
        _angle(a, required=a.id in REQUIRED, answers=done.get(a.id, 0), state="couvert" if done.get(a.id) else "ouvert")
        for a in ANGLES
    ), **kw)


def _conv(**kw: Any) -> Conversation:
    return Conversation("un", NOW, NOW, **kw)


def test_l_ordre_est_celui_des_angles_du_kit_et_seuls_les_obligatoires_comptent() -> None:
    cadre = _cadre()
    assert [a.id for a in ce_qui_manque(cadre)] == list(REQUIRED)
    etape = prochaine_etape(_conv(), cadre)
    assert (etape.kind, etape.angle, etape.index, etape.obligatoire) == ("question", "utilisateurs", 0, True)
    assert (etape.phase, etape.section) == ("brief", "L'utilisateur")
    assert etape.manque == tuple(NOMS[i] for i in REQUIRED) and etape.preambule == ""  # rien de fait : pas de « il ne me manque »


def test_la_machine_dit_pourquoi_et_dit_ce_qu_il_manque() -> None:
    etape = prochaine_etape(_conv(), _cadre({"utilisateurs": 2, "criteres": 2, "hors-perimetre": 2}))
    assert etape.angle == "contraintes" and etape.preambule.startswith("Il me manque encore : ")
    assert NOMS["contraintes"] in etape.preambule and NOMS["risques"] in etape.preambule
    assert etape.pourquoi.startswith("Je te demande ça ") and "indispensable" in etape.pourquoi
    dernier = prochaine_etape(_conv(), _cadre({"utilisateurs": 2, "criteres": 2, "hors-perimetre": 2, "contraintes": 2}))
    assert dernier.preambule == f"Il ne me manque plus que ça : {NOMS['risques']}."
    for angle in INTENTIONS:  # chaque angle a sa raison en mots simples
        assert INTENTIONS[angle].endswith(".")


def test_un_angle_couvert_n_est_plus_essentiel() -> None:
    cadre = _cadre({"utilisateurs": 1})
    assert [a.id for a in ce_qui_manque(cadre)] == list(REQUIRED[1:])
    assert not cadre.angles[0].ferme and cadre.angles[0].couvert


def test_l_essentiel_couvert_propose_le_document_une_fois() -> None:
    cadre = _cadre(dict.fromkeys(REQUIRED, 1))
    etape = prochaine_etape(_conv(), cadre)
    assert etape.kind == "document" and not etape.fin
    assert prochaine_etape(_conv(document="posee"), cadre).kind == "document"


def test_apres_un_refus_on_affine_puis_facultatif_puis_fin_possible() -> None:
    conv = _conv(document="refusee")
    cadre = _cadre(dict.fromkeys(REQUIRED, 1))
    etape = prochaine_etape(conv, cadre)
    assert (etape.kind, etape.angle, etape.index, etape.obligatoire) == ("question", "utilisateurs", 1, False)
    assert "affiner" in etape.pourquoi
    both = _cadre(dict.fromkeys(REQUIRED, 2))
    etape = prochaine_etape(conv, both)
    assert (etape.kind, etape.angle, etape.index) == ("question", "existant", 0) and "facultatif" in etape.pourquoi
    all_done = _cadre({**dict.fromkeys(REQUIRED, 2), "existant": 1, "alternatives": 1})
    etape = prochaine_etape(conv, all_done)
    assert etape.kind == "document" and etape.fin


def test_on_ne_pose_pas_deux_fois_la_meme_question() -> None:
    cadre = _cadre({"utilisateurs": 1})
    assert prochaine_etape(_conv(), cadre).angle == "criteres"
    assert _cadre({"utilisateurs": 2}).angles[0].ferme


def test_je_ne_sais_pas_et_ecarte_ferment_l_angle() -> None:
    unknown = _angle(ANGLES[0], required=True, answers=1, state="couvert", unknown=True)
    assert unknown.ferme
    skipped = _angle(ANGLES[0], required=True, state="ecarte", reason="sans objet")
    assert skipped.couvert and skipped.ferme
    assert not _angle(ANGLES[0], required=True, state="ecarte").couvert  # sans raison : ne compte pas pour le gate
    assert _angle(ANGLES[1], required=False, answers=1, state="couvert").ferme  # facultatif : une question suffit


def test_une_reformulation_en_attente_passe_avant_toute_autre_question() -> None:
    conv = _conv()
    conv.say("utilisateur", "x", "reponse", NOW)
    a = conv.new_element("element", "un", "brief", "L'utilisateur", "utilisateurs", 1)
    b = conv.new_element("element", "deux", "brief", "Le problème", "utilisateurs", 1)
    etape = prochaine_etape(conv, _cadre())
    assert etape.kind == "confirmer" and set(etape.ids) == {a.id, b.id} and etape.angle == "utilisateurs"
    a.statut = b.statut = "confirme"
    assert prochaine_etape(conv, _cadre()).kind == "question"


def test_le_mode_l_emporte() -> None:
    cadre = _cadre()
    assert prochaine_etape(_conv(mode="termine"), cadre).kind == "termine"
    assert prochaine_etape(_conv(mode="pause"), cadre).kind == "pause"
    brain = prochaine_etape(_conv(mode="brainstorm"), cadre)
    assert (brain.kind, brain.section, brain.obligatoire) == ("brainstorm", "Pistes", False)


def test_une_demande_sans_entretien_ou_a_traiter_d_abord_ne_pose_aucune_question() -> None:
    assert prochaine_etape(_conv(), Cadre((), interview=False)).kind == "rien"
    assert prochaine_etape(_conv(), cadre_from_view(None)).kind == "rien"
    assert prochaine_etape(_conv(), _cadre(blocking=False)).kind == "reporte"


def test_cadre_from_view_lit_la_vue_de_l_entretien() -> None:
    view = {
        "interview": True, "blocking": True, "depth": "express",
        "angles": [
            {"id": "utilisateurs", "title": "U", "required": True, "state": "couvert", "questions": ["q1"],
             "answers": [f"{UNKNOWN_PREFIX}Qui ?"], "reason": ""},
            {"id": "criteres", "title": "C", "required": True, "state": "ecarte", "questions": ["q"], "answers": [], "reason": "sans objet"},
            {"id": "risques", "title": "R", "required": False, "state": "ouvert", "questions": ["q"], "answers": []},
        ],
    }
    cadre = cadre_from_view(view)
    assert cadre.profondeur == "express" and [a.id for a in cadre.angles] == ["utilisateurs", "criteres", "risques"]
    assert cadre.angles[0].inconnu and cadre.angles[0].ferme and cadre.angles[1].couvert and not cadre.angles[2].couvert
    assert ce_qui_manque(cadre) == []


def test_le_resume_tient_en_deux_lignes_calculees() -> None:
    conv = _conv()
    cadre = _cadre({"utilisateurs": 2})
    head, tail = resume(conv, cadre).split("\n")
    assert head.startswith("On a noté 0 point ; il me manque encore ") and tail.startswith("Ma prochaine question portera sur ")
    conv.question = Question("amorce", "Comment tu verras que c'est réussi ?", "p", "criteres", 0, "brief", "Un succès ressemble à")
    assert resume(conv, cadre).split("\n")[1] == "Ma prochaine question : « Comment tu verras que c'est réussi ? »"


def test_le_resume_couvre_confirmation_brainstorm_document_pause_et_fin() -> None:
    conv = _conv()
    conv.say("utilisateur", "x", "reponse", NOW)
    conv.new_element("element", "Les mainteneurs", "brief", "L'utilisateur", "utilisateurs", 1)
    assert "J'attends ton accord sur : « Les mainteneurs »" in resume(conv, _cadre()).split("\n")[1]
    full = _cadre(dict.fromkeys(REQUIRED, 1))
    done = _conv()
    assert resume(done, full).startswith("On a noté 0 point ; j'ai l'essentiel.")
    assert resume(done, full).endswith("On peut passer au document quand tu veux.")
    assert resume(_conv(mode="brainstorm"), _cadre()).endswith("On brainstorme : donne-moi tes idées, une par une.")
    assert resume(_conv(mode="termine"), _cadre()).endswith("Rien de plus à demander pour l'instant.")
    paused = _conv(mode="pause", mode_avant_pause="cadrage")
    paused.question = Question("pause", "On fait une pause.", "p")
    assert resume(paused, _cadre()).split("\n")[1].startswith("Ma prochaine question portera sur ")
    one = _conv()
    one.say("utilisateur", "x", "reponse", NOW)
    one.new_element("element", "a", "brief", "L'utilisateur", "utilisateurs", 1, statut="confirme")
    one.new_element("element", "b", "brief", "Le problème", "utilisateurs", 1, statut="confirme")
    assert resume(one, full).startswith("On a noté 2 points ;")
