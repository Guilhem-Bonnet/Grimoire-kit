"""Les gestes bornés : le moteur sans modèle (règles simples) et le gardien qui valide toute sortie de moteur."""

from __future__ import annotations

import time
from typing import Any

import pytest

from grimoire.core.cadrage_conversation import MAX_ELEMENT_CHARS, RUBRIQUES
from grimoire.core.cadrage_conversation_engine import (
    CLASSES,
    RUBRIQUE_KEYS,
    ElementExtrait,
    EntreeClassement,
    EntreeExtraction,
    EntreePistes,
    EntreeQuestion,
    EntreeReformulation,
    Garde,
    Moteur,
    MoteurSansModele,
)
from grimoire.core.cadrage_interview import ANGLES

SANS = MoteurSansModele()
JARGON = ("MOA", "MOE", "MoSCoW", "pré-mortem", "gate", "angle", "rubrique")


def test_le_moteur_sans_modele_remplit_l_interface() -> None:
    assert isinstance(SANS, Moteur) and SANS.id == "sans-modele" and "sans modèle" in SANS.libelle
    assert all("modèle" in x or "Je" in x for x in SANS.limites()) and len(SANS.limites()) >= 3


def test_chaque_amorce_du_kit_a_sa_question_en_mots_simples() -> None:
    for angle in ANGLES:
        for index, seed in enumerate(angle.seeds):
            text = SANS.formuler_question(EntreeQuestion("amorce", angle.id, index, seed))
            assert text.endswith("?") and len(text) < 160
            assert not any(word.lower() in text.lower() for word in JARGON), text
            assert "vous" not in text.lower() and "votre" not in text.lower()
    assert SANS.formuler_question(EntreeQuestion("amorce", "inconnu", 9, "Une amorce ?")) == "Dis-moi : Une amorce ?"
    assert SANS.formuler_question(EntreeQuestion("amorce", "inconnu", 9)).startswith("Dis-moi : ")


@pytest.mark.parametrize("kind", ["brainstorm", "document", "pause", "termine", "rien", "reporte"])
def test_les_invitations_sont_chaleureuses_et_sans_jargon(kind: str) -> None:
    text = SANS.formuler_question(EntreeQuestion(kind))
    assert text and not any(word.lower() in text.lower() for word in JARGON)
    assert SANS.formuler_question(EntreeQuestion("document", fin=True)).startswith("Je n'ai plus de question")
    assert SANS.formuler_question(EntreeQuestion("inconnu")) == "Dis-moi ce que tu veux ajouter."
    assert "n'invente pas" in SANS.formuler_question(EntreeQuestion("brainstorm"))


CLASSEMENT = [
    ("Les mainteneurs du kit", "amorce", "reponse"),
    ("Non, personne ne le fait aujourd'hui", "amorce", "reponse"),
    ("oui", "amorce", "reponse"),
    ("Je ne sais pas", "amorce", "ne_sait_pas"),
    ("euh, aucune idée", "amorce", "ne_sait_pas"),
    ("ça dépend", "amorce", "ne_sait_pas"),
    ("Sans objet", "amorce", "sans_objet"),
    ("ça ne me concerne pas", "amorce", "sans_objet"),
    ("On en reparle plus tard", "amorce", "hors_sujet"),
    ("Hors sujet : le logo", "amorce", "hors_sujet"),
    ("Au fait, il faudra penser au logo", "amorce", "hors_sujet"),
    ("pause", "amorce", "pause"),
    ("On fait une pause ?", "amorce", "pause"),
    ("je dois y aller", "amorce", "pause"),
    ("à plus tard", "amorce", "pause"),
    ("On brainstorme ?", "amorce", "brainstorm"),
    ("j'ai besoin d'idées, on peut chercher des idées", "amorce", "brainstorm"),
    ("Pourquoi tu me demandes ça ?", "amorce", "question"),
    ("c'est quoi un angle ?", "amorce", "question"),
    ("En fait, c'est pour les équipes support", "amorce", "contredit"),
    ("Finalement non, plutôt les testeurs", "amorce", "contredit"),
    ("oui", "confirmer", "confirme"),
    ("Oui, c'est ça", "confirmer", "confirme"),
    ("Exactement !", "confirmer", "confirme"),
    ("oui mais pas pour tout le monde", "confirmer", "contredit"),
    ("Non", "confirmer", "contredit"),
    ("Non, plutôt les testeurs", "confirmer", "contredit"),
    ("pas vraiment, ce sont les relecteurs", "confirmer", "contredit"),
    ("oui tout à fait, c'est exactement ça, et en plus ça sert aux équipes", "confirmer", "reponse"),
    ("oui", "document", "confirme"),
    ("non", "document", "contredit"),
    ("c'est tout", "brainstorm", "reprise"),
    ("on continue", "amorce", "reponse"),
]


@pytest.mark.parametrize(("texte", "contexte", "attendu"), CLASSEMENT)
def test_le_classement_par_regles_simples(texte: str, contexte: str, attendu: str) -> None:
    mode = "brainstorm" if contexte == "brainstorm" else "cadrage"
    assert SANS.classer_reponse(EntreeClassement(texte, contexte, mode)) == attendu
    assert attendu in CLASSES


def test_la_reprise_ne_se_reconnait_qu_en_brainstorm() -> None:
    assert SANS.classer_reponse(EntreeClassement("on continue", "amorce", "brainstorm")) == "reprise"
    assert SANS.classer_reponse(EntreeClassement("stop", "", "brainstorm")) == "reprise"
    assert SANS.classer_reponse(EntreeClassement("on continue", "amorce", "cadrage")) == "reponse"


def test_l_extraction_range_dans_la_rubrique_visee_et_reprend_les_mots() -> None:
    out = SANS.extraire_elements(EntreeExtraction("Les  mainteneurs\ndu kit", "brief/L'utilisateur"))
    assert out == [ElementExtrait("Les mainteneurs du kit", "brief/L'utilisateur")]
    assert SANS.extraire_elements(EntreeExtraction("non", "brief/L'utilisateur")) == []
    assert SANS.extraire_elements(EntreeExtraction("En fait, non, plutôt les testeurs", "brief/L'utilisateur"))[0].texte == "les testeurs"
    assert SANS.extraire_elements(EntreeExtraction("Finalement", "brief/L'utilisateur")) == []


def test_en_brainstorm_chaque_idee_de_l_humain_est_rangee_sans_en_inventer() -> None:
    out = SANS.extraire_elements(EntreeExtraction("- un guide\n* une vidéo; un forum ;;  ", "brainstorm/Pistes", mode="brainstorm"))
    assert [e.texte for e in out] == ["un guide", "une vidéo", "un forum"]
    assert all(e.rubrique == "brainstorm/Pistes" for e in out)
    assert SANS.proposer_pistes(EntreePistes("un besoin", ("a",))) == []
    many = SANS.extraire_elements(EntreeExtraction(";".join(f"idée {i}" for i in range(20)), "brainstorm/Pistes", mode="brainstorm"))
    assert len(many) == 5


def test_un_long_texte_est_decoupe_par_phrases_sans_rien_perdre_d_utile() -> None:
    phrase = "Une phrase assez longue pour remplir la place. "
    text = phrase * 40
    out = SANS.extraire_elements(EntreeExtraction(text, "brief/Le problème"))
    assert 1 < len(out) <= 5 and all(len(e.texte) <= MAX_ELEMENT_CHARS for e in out)
    one = SANS.extraire_elements(EntreeExtraction("mot " * 500, "brief/Le problème"))
    assert all(len(e.texte) <= MAX_ELEMENT_CHARS for e in one) and one[0].texte.endswith("…")


def test_la_reformulation_reprend_fidelement() -> None:
    assert SANS.reformuler(EntreeReformulation(("a", "b"))) == "Si je comprends bien : a ; b. C'est bien ça ?"
    assert SANS.reformuler(EntreeReformulation(("a",), remplace=True)) == "Je corrige donc : a. C'est bien ça ?"


def test_sans_modele_on_dit_honnetement_ce_qu_on_ne_sait_pas_faire() -> None:
    assert SANS.repondre_question("Pourquoi tu demandes ça ?", "Pour savoir à qui ça sert.") == "Pour savoir à qui ça sert."
    refus = SANS.repondre_question("C'est quoi un pipeline ?", "Pour savoir")
    assert "aucun modèle" in refus and "plus tard" in refus
    assert SANS.est_tour_lourd("n'importe quoi", "question") is False


# ── le gardien ─────────────────────────────────────────────────────────────


class Faux:
    """Un moteur factice : chaque geste rend ce qu'on lui dit, ou lève, ou tarde."""

    id = "faux"
    libelle = "Faux"

    def __init__(self, **out: Any) -> None:
        self.out = out

    def _give(self, name: str, default: Any) -> Any:
        value = self.out.get(name, default)
        if isinstance(value, BaseException):
            raise value
        if callable(value):
            return value()
        return value

    def formuler_question(self, entree: Any) -> Any:
        return self._give("question", "Une question ?")

    def classer_reponse(self, entree: Any) -> Any:
        return self._give("classe", "reponse")

    def extraire_elements(self, entree: Any) -> Any:
        return self._give("elements", [ElementExtrait("un texte", "brief/L'utilisateur")])

    def reformuler(self, entree: Any) -> Any:
        return self._give("reformulation", "Si je comprends bien : x.")

    def proposer_pistes(self, entree: Any) -> Any:
        return self._give("pistes", ["une piste"])

    def repondre_question(self, texte: str, pourquoi: str) -> Any:
        return self._give("reponse", "Une réponse.")

    def est_tour_lourd(self, texte: str, etape: str) -> Any:
        return self._give("lourd", True)

    def limites(self) -> Any:
        return self._give("limites", ["Je ne fais pas tout."])


Q = EntreeQuestion("amorce", "utilisateurs", 0, "Qui ?")
C = EntreeClassement("texte")
X = EntreeExtraction("texte", "brief/L'utilisateur")
R = EntreeReformulation(("a",))
P = EntreePistes()


def test_un_bon_moteur_passe_tel_quel() -> None:
    garde = Garde(Faux())
    assert garde.id == "faux" and not garde.degrade
    assert garde.formuler_question(Q) == "Une question ?"
    assert garde.classer_reponse(C) == "reponse"
    assert garde.extraire_elements(X) == [ElementExtrait("un texte", "brief/L'utilisateur")]
    assert garde.reformuler(R) == "Si je comprends bien : x."
    assert garde.proposer_pistes(P) == ["une piste"]
    assert garde.repondre_question("q", "p") == "Une réponse."
    assert garde.est_tour_lourd("t", "question") is True
    assert garde.limites() == ("Je ne fais pas tout.",)
    assert garde.notes == []


def test_une_exception_est_remplacee_par_le_repli_sans_modele_avec_une_note() -> None:
    boom = RuntimeError("boum")
    garde = Garde(Faux(question=boom, classe=boom, elements=boom, reformulation=boom, pistes=boom, reponse=boom, lourd=boom, limites=boom))
    assert garde.formuler_question(Q).startswith("Dis-moi : qui va se servir")
    assert garde.classer_reponse(EntreeClassement("Je ne sais pas")) == "ne_sait_pas"
    assert garde.extraire_elements(X)[0].texte == "texte"
    assert garde.reformuler(R).startswith("Si je comprends bien")
    assert garde.proposer_pistes(P) == []
    assert "aucun modèle" in garde.repondre_question("hein ?", "")
    assert garde.est_tour_lourd("t", "question") is False
    assert garde.limites() == SANS.limites()
    assert garde.degrade and len(garde.notes) == 8 and all("RuntimeError" in n for n in garde.notes)


def test_un_moteur_lent_est_abandonne_apres_le_delai() -> None:
    garde = Garde(Faux(question=lambda: time.sleep(2) or "tard"), delai_s=0.1)
    started = time.monotonic()
    assert garde.formuler_question(Q).startswith("Dis-moi")
    assert time.monotonic() - started < 1.5
    assert "trop de temps" in garde.notes[0]


@pytest.mark.parametrize(
    "mauvaise",
    [None, 12, "", "   ", "x" * 700, ["liste"], {"a": 1}, "​\x00"],
)
def test_une_question_hors_cadre_est_refusee(mauvaise: Any) -> None:
    garde = Garde(Faux(question=mauvaise))
    assert garde.formuler_question(Q).startswith("Dis-moi")
    assert garde.degrade


@pytest.mark.parametrize("mauvaise", ["exec", "", None, 3, "REPONSE", ["reponse"]])
def test_une_classe_hors_liste_est_refusee(mauvaise: Any) -> None:
    garde = Garde(Faux(classe=mauvaise))
    assert garde.classer_reponse(EntreeClassement("Je ne sais pas")) == "ne_sait_pas"
    assert garde.degrade


def test_une_rubrique_hors_liste_est_refusee_et_le_texte_n_est_pas_perdu() -> None:
    hostile = [ElementExtrait("pirate", "exigences/Fonctionnelles"), ElementExtrait("pirate", "../../etc/passwd"),
               ElementExtrait("pirate", "brainstorm/Questions ouvertes")]
    garde = Garde(Faux(elements=hostile))
    out = garde.extraire_elements(X)
    assert [e.texte for e in out] == ["texte"] and out[0].rubrique == "brief/L'utilisateur"  # repli sans modèle
    assert garde.degrade and any("hors cadre" in n for n in garde.notes)
    assert all(k in RUBRIQUE_KEYS for k in ("brief/L'utilisateur", "brainstorm/Pistes"))
    assert len(RUBRIQUE_KEYS) == len(RUBRIQUES) - 1


def test_un_element_valide_survit_a_un_element_hostile_du_meme_lot() -> None:
    mixed = [ElementExtrait("pirate", "exigences/Fonctionnelles"), ElementExtrait("bon", "brief/Le problème")]
    garde = Garde(Faux(elements=mixed))
    assert garde.extraire_elements(X) == [ElementExtrait("bon", "brief/Le problème")]
    assert garde.degrade


@pytest.mark.parametrize(
    "mauvais",
    [
        "pas une liste", None, [("texte", "brief/L'utilisateur")], [{"texte": "x", "rubrique": "brief/L'utilisateur"}],
        [ElementExtrait("x" * (MAX_ELEMENT_CHARS + 1), "brief/L'utilisateur")], [ElementExtrait("", "brief/L'utilisateur")],
        [ElementExtrait(3, "brief/L'utilisateur")],  # type: ignore[arg-type]
    ],
)
def test_une_extraction_hors_cadre_tombe_sur_le_repli(mauvais: Any) -> None:
    garde = Garde(Faux(elements=mauvais))
    assert [e.texte for e in garde.extraire_elements(X)] == ["texte"]
    assert garde.degrade


def test_trop_d_elements_sont_bornes() -> None:
    many = [ElementExtrait(f"idée {i}", "brainstorm/Pistes") for i in range(40)]
    assert len(Garde(Faux(elements=many)).extraire_elements(EntreeExtraction("t", "brainstorm/Pistes"))) == 5


def test_un_texte_enorme_et_des_pistes_hostiles_sont_refuses() -> None:
    assert Garde(Faux(reformulation="x" * 5000)).reformuler(R).startswith("Si je comprends bien")
    assert Garde(Faux(reponse="y" * 5000)).repondre_question("q", "p").startswith("Je ne peux pas")
    assert Garde(Faux(pistes="pas une liste")).proposer_pistes(P) == []
    assert Garde(Faux(pistes=["z" * 1000])).proposer_pistes(P) == []
    assert len(Garde(Faux(pistes=[f"p{i}" for i in range(30)])).proposer_pistes(P)) == 5
    assert Garde(Faux(lourd="oui")).est_tour_lourd("t", "q") is False
    assert Garde(Faux(limites="une chaîne")).limites() == SANS.limites()


def test_l_identite_d_un_moteur_est_bornee() -> None:
    class Bizarre(Faux):
        id = "x" * 100

    assert Garde(Bizarre()).id == "inconnu"
    assert Garde(object()).id == "inconnu"
