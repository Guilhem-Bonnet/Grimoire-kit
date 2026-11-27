"""Le rangement dans le document : l'entretien guidé reste la seule porte d'écriture ; les cas limites se refusent sans rien toucher."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from grimoire.core import cadrage_conversation_document as rangement
from grimoire.core import cadrage_conversation_store as store
from grimoire.core import cadrage_conversation_turn as turn
from grimoire.core import cadrage_interview as interview
from grimoire.core.cadrage_conversation import Conversation, ConversationError
from grimoire.core.cadrage_conversation_engine import Garde, MoteurSansModele
from grimoire.core.cadrage_conversation_hygiene import Reglages
from grimoire.core.chantiers import new_chantier
from grimoire.tools import workspace_atelier as wa
from grimoire.tools import workspace_atelier_conversation as routes

T0 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "project-context.yaml").write_text("project:\n  name: demo\n", encoding="utf-8")
    new_chantier(tmp_path, "Un chantier", slug="un")
    interview.interview_plan(tmp_path, "un", depth="standard")
    return tmp_path


def _ctx(root: Path, moteur: Any = None) -> turn.Contexte:
    return turn.Contexte(root, "un", root / "_grimoire/cadrage/un", Garde(moteur or MoteurSansModele()), T0, Reglages())


def _conv() -> Conversation:
    conv = Conversation("un", T0.isoformat(), T0.isoformat())
    conv.say("utilisateur", "x", "reponse", T0.isoformat())
    return conv


def _brief(root: Path) -> str:
    return (root / "_grimoire/cadrage/un/01-brief.md").read_text(encoding="utf-8")


def test_answer_accepte_une_rubrique_que_l_angle_porte_et_refuse_les_autres(root: Path) -> None:
    interview.answer(root, "un", "utilisateurs", "Le problème en vrai", question="Qui ?", rubrique=("brief", "Le problème"))
    assert "- *Qui ?* Le problème en vrai" in _brief(root).split("## Le problème")[1].split("## L'utilisateur")[0]
    before = _brief(root)
    for bad in (("exigences", "Fonctionnelles"), ("brief", "Pourquoi maintenant"), ("cahier-des-charges", "Risques")):
        with pytest.raises(ValueError, match="ne la porte pas"):
            interview.answer(root, "un", "utilisateurs", "pirate", rubrique=bad)
    assert _brief(root) == before


def test_un_element_dont_la_rubrique_n_est_pas_celle_de_son_angle_est_refuse_sans_ecrire(root: Path) -> None:
    conv = _conv()
    el = conv.new_element("element", "pirate", "brief", "L'utilisateur", "criteres", 1)
    before = _brief(root)
    with pytest.raises(ConversationError, match="ne la porte pas"):
        rangement.ecrire(root, "un", conv, [el])
    assert _brief(root) == before and el.ecrit is False


def test_sans_entretien_commence_le_rangement_le_demarre(tmp_path: Path) -> None:
    new_chantier(tmp_path, "Un chantier", slug="un")
    assert rangement._answers(tmp_path, "un", "utilisateurs") == ([], "ouvert", "")
    conv = _conv()
    el = conv.new_element("element", "Les mainteneurs", "brief", "L'utilisateur", "utilisateurs", 1, statut="confirme")
    rangement.ecrire(tmp_path, "un", conv, [el])
    assert el.ecrit and "Les mainteneurs" in _brief(tmp_path)
    assert rangement._answers(tmp_path, "un", "inconnu") == ([], "ouvert", "")


def test_un_texte_deja_consigne_n_est_pas_ecrit_deux_fois_et_n_est_pas_retirable(root: Path) -> None:
    conv = _conv()
    first = conv.new_element("element", "Les mainteneurs", "brief", "L'utilisateur", "utilisateurs", 1, statut="confirme")
    twin = conv.new_element("element", "Les mainteneurs", "brief", "L'utilisateur", "utilisateurs", 1, statut="confirme")
    rangement.ecrire(root, "un", conv, [first, twin])
    assert (first.ecrit, twin.ecrit) == (True, False) and _brief(root).count("Les mainteneurs") == 1
    with pytest.raises(ConversationError, match="n'est pas rangé"):
        rangement.corriger(root, "un", conv, twin, "autre")
    with pytest.raises(ConversationError, match="n'est pas rangé"):
        rangement.retirer(root, "un", conv, twin)


def test_si_le_document_a_ete_retouche_ailleurs_on_ne_touche_a_rien(root: Path) -> None:
    conv = _conv()
    el = conv.new_element("element", "Les mainteneurs", "brief", "L'utilisateur", "utilisateurs", 1, statut="confirme")
    rangement.ecrire(root, "un", conv, [el])
    interview.answer(root, "un", "utilisateurs", "Une réponse donnée par la CLI")  # une autre porte d'écriture, entre-temps
    before = _brief(root)
    with pytest.raises(ConversationError, match="ne correspond plus"):
        rangement.corriger(root, "un", conv, el, "Autre")
    with pytest.raises(ConversationError, match="ne correspond plus"):
        rangement.retirer(root, "un", conv, el)
    assert _brief(root) == before and el.ecrit is True and el.texte == "Les mainteneurs"


def test_un_angle_ecarte_ensuite_n_est_pas_defait_par_une_correction(root: Path) -> None:
    conv = _conv()
    el = conv.new_element("element", "Les mainteneurs", "brief", "L'utilisateur", "utilisateurs", 1, statut="confirme")
    rangement.ecrire(root, "un", conv, [el])
    interview.answer(root, "un", "utilisateurs", "sans objet", skip=True)
    with pytest.raises(ConversationError, match="ne correspond plus"):
        rangement.retirer(root, "un", conv, el)


def test_un_sans_objet_modifie_ailleurs_n_est_pas_retire(root: Path) -> None:
    conv = _conv()
    el = conv.new_element("sansobjet", "sans objet", "brief", "L'utilisateur", "utilisateurs", 1, statut="confirme")
    rangement.ecrire(root, "un", conv, [el])
    assert el.ecrit is True
    interview.retract(root, "un", "utilisateurs")  # retiré par la CLI
    with pytest.raises(ConversationError, match="n'est plus écarté"):
        rangement.retirer(root, "un", conv, el)


def test_remplacer_un_element_range_retire_l_ancien_et_garde_ce_qui_le_suivait(root: Path) -> None:
    conv = _conv()
    old = conv.new_element("element", "Ancien", "brief", "L'utilisateur", "utilisateurs", 1, statut="confirme")
    after = conv.new_element("element", "Suivant", "brief", "L'utilisateur", "utilisateurs", 1, statut="confirme")
    rangement.ecrire(root, "un", conv, [old, after])
    new = conv.new_element("element", "Nouveau", "brief", "L'utilisateur", "utilisateurs", 1, statut="confirme")
    rangement.ecrire(root, "un", conv, [new], remplace=old)
    brief = _brief(root)
    assert "Ancien" not in brief and brief.index("Suivant") < brief.index("Nouveau")
    assert interview.view(root, "un")["angles"][0]["answers"] == ["Suivant", "Nouveau"]


def test_une_correction_confirmee_apres_que_l_ancien_a_ete_ecarte_ne_retire_rien_de_plus(root: Path) -> None:
    ctx = _ctx(root)
    rev = turn.demarrer(ctx)["rev"]
    rev = turn.recevoir(ctx, rev, "Les mainteneurs")["rev"]
    rev = turn.recevoir(ctx, rev, "oui")["rev"]
    rev = turn.recevoir(ctx, rev, "En fait, plutôt les relecteurs")["rev"]  # propose de remplacer « Les mainteneurs »
    old = turn.vue(ctx)["elements"][0]["id"]
    rev = turn.decider(ctx, rev, old, "ecarter")["rev"]  # l'humain écarte l'ancien lui-même, avant de confirmer le nouveau
    turn.recevoir(ctx, rev, "oui")
    assert "Les mainteneurs" not in _brief(root) and "les relecteurs" in _brief(root)
    assert [e["statut"] for e in turn.vue(ctx)["elements"]] == ["ecarte", "confirme"]


def test_une_contradiction_sans_contenu_retombe_sur_la_reponse_normale(root: Path) -> None:
    ctx = _ctx(root)
    rev = turn.demarrer(ctx)["rev"]
    rev = turn.recevoir(ctx, rev, "Les mainteneurs")["rev"]
    rev = turn.recevoir(ctx, rev, "oui")["rev"]
    react = turn.recevoir(ctx, rev, "En fait")
    assert react["accuse"].startswith("Je n'ai rien retenu") and len(turn.vue(ctx)["elements"]) == 1


def test_un_texte_libre_sans_question_en_cours_n_a_pas_de_rubrique(root: Path) -> None:
    class Rien(MoteurSansModele):
        id = "rien"

    ctx = _ctx(root, Rien())
    turn.demarrer(ctx)
    conv = store.read(ctx.directory, "un")
    assert conv is not None
    conv.question = turn.Question("rien", "Rien à demander.", "parce que")
    store.save(ctx.directory, conv)
    react = turn.recevoir(ctx, conv.rev, "Une idée en l'air")
    assert "pas de rubrique" in react["accuse"] and [n["texte"] for n in turn.vue(ctx)["plus_tard"]] == ["Une idée en l'air"]


@pytest.mark.parametrize(("kind", "expected"), [("question", "Ta demande n'a pas besoin"), ("incident", "action à rétablir")])
def test_une_demande_sans_entretien_bloquant_ne_pose_aucune_question(root: Path, kind: str, expected: str) -> None:
    state = root / "_grimoire/cadrage/un/entretien.json"
    state.write_text(json.dumps({"depth": "standard", "kind": "question" if kind == "question" else "incident", "angles": {}}),
                     encoding="utf-8")
    ctx = _ctx(root)
    react = turn.demarrer(ctx)
    assert expected in react["message"] and react["etape"]["kind"] in ("rien", "reporte")
    assert react["question"]["kind"] in ("rien", "reporte")
    if kind == "question":
        assert turn.vue(ctx)["manque"] == []


def test_le_compris_compte_les_ecartes_sans_les_lister(root: Path) -> None:
    ctx = _ctx(root)
    rev = turn.demarrer(ctx)["rev"]
    rev = turn.recevoir(ctx, rev, "Les mainteneurs")["rev"]
    rev = turn.decider(ctx, rev, "e1", "ecarter")["rev"]
    seen = turn.compris(ctx)
    assert seen["ecartes"] == 1 and seen["rubriques"] == [] and seen["en_attente"] == 0


def test_la_lecture_du_fichier_dit_une_erreur_d_entree_sortie(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(path: Path) -> Any:
        raise OSError("disque")

    monkeypatch.setattr(store, "open_regular", broken)
    with pytest.raises(ConversationError, match="illisible"):
        store.read(tmp_path, "un")


def test_un_chantier_dont_le_dossier_ne_se_resout_pas_n_est_pas_liste(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    new_chantier(root, "Deux", slug="deux")
    (root / "project-context.yaml").write_text(
        "project:\n  name: demo\ncockpit:\n  test_features:\n    atelier: true\n", encoding="utf-8")
    real = wa._phase_path

    def picky(r: Path, slug: str, filename: str) -> Path:
        if slug == "deux":
            raise FileNotFoundError(slug)
        return real(r, slug, filename)

    monkeypatch.setattr(wa, "_phase_path", picky)
    ctx = _ctx(root)
    ctx.now = datetime.now(UTC)
    turn.demarrer(ctx)
    assert [r["chantier"] for r in routes.get(root, ["conversations"])["actives"]] == ["un"]
