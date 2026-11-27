"""La conversation de bout en bout, sans modèle : parcours, rangement dans le document, pause, moteur hostile."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from grimoire.core import cadrage_conversation_store as store
from grimoire.core import cadrage_conversation_turn as turn
from grimoire.core import cadrage_interview as interview
from grimoire.core.cadrage import check
from grimoire.core.cadrage_conversation import ConversationConflictError, ConversationError
from grimoire.core.cadrage_conversation_engine import ElementExtrait, Garde, MoteurSansModele
from grimoire.core.cadrage_conversation_hygiene import Reglages
from grimoire.core.chantiers import new_chantier

T0 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "project-context.yaml").write_text("project:\n  name: demo\n", encoding="utf-8")
    new_chantier(tmp_path, "Un chantier", slug="un")
    interview.interview_plan(tmp_path, "un", depth="standard")
    return tmp_path


def make_ctx(root: Path, moteur: Any = None, now: datetime = T0, slug: str = "un", **garde: Any) -> turn.Contexte:
    directory = root / "_grimoire" / "cadrage" / ("" if slug == "principal" else slug)
    return turn.Contexte(root, slug, directory, Garde(moteur or MoteurSansModele(), **garde), now, Reglages())


@pytest.fixture
def ctx(root: Path) -> turn.Contexte:
    return make_ctx(root)


def _doc(root: Path, name: str) -> str:
    return (root / "_grimoire" / "cadrage" / "un" / name).read_text(encoding="utf-8")


def _all_docs(root: Path, base: str = "un") -> dict[str, str]:
    folder = root / "_grimoire" / "cadrage" / base
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(folder.iterdir()) if p.suffix in (".md", ".json")
            and p.name != store.FILENAME}


def _say(ctx: turn.Contexte, rev: int, text: str) -> dict[str, Any]:
    return turn.recevoir(ctx, rev, text)


def _drive(ctx: turn.Contexte, rev: int, limit: int = 60) -> tuple[int, list[tuple[str, str]]]:
    """Joue la conversation jusqu'à la proposition du document : une réponse par question, « oui » aux reformulations."""
    given: list[tuple[str, str]] = []
    out = turn.vue(ctx)
    for i in range(limit):
        question = out["question"]
        if question["kind"] == "confirmer":
            text = "oui"
        elif question["kind"] == "amorce":
            text = f"Réponse numéro {i} sur {question['angle']}"
            given.append((question["angle"], text))
        else:
            break
        react = _say(ctx, rev, text)
        rev = react["rev"]
        out = turn.vue(ctx)
    return rev, given


# ── le parcours complet ────────────────────────────────────────────────────


def test_un_besoin_simple_se_cadre_sans_modele_et_le_gate_est_celui_d_un_remplissage_a_la_main(
    root: Path, ctx: turn.Contexte, tmp_path_factory: pytest.TempPathFactory
) -> None:
    started = turn.demarrer(ctx)
    assert started["cree"] is True and started["question"]["kind"] == "amorce" and started["lourd"] is False
    assert "sans modèle" in turn.vue(ctx)["tours"][0]["texte"]
    rev, given = _drive(ctx, started["rev"])
    state = turn.vue(ctx)
    assert state["etape"]["kind"] == "document" and state["manque"] == [] and given
    assert state["question"]["kind"] == "document" and state["document"] == "posee"
    kept = [e for e in state["elements"] if e["statut"] == "confirme"]
    assert kept and all(e["ecrit"] and e["source"] >= 1 for e in kept)
    assert not any(e["statut"] == "propose" for e in state["elements"])
    # le même document, rempli à la main par les fonctions de l'entretien : mêmes fichiers, même trace, même gate
    twin = tmp_path_factory.mktemp("twin")
    new_chantier(twin, "Un chantier", slug="un")
    interview.interview_plan(twin, "un", depth="standard")
    for e in kept:
        interview.answer(twin, "un", e["angle"] if "angle" in e else _angle_of(root, e), e["texte"], question=e["question"],
                         rubrique=(e["phase"], e["section"]))
    assert check(root, "un") == check(twin, "un")
    assert interview.view(root, "un")["open_required"] == [] and interview.view(root, "un") == interview.view(twin, "un")
    assert _all_docs(root) == _all_docs(twin)
    assert interview.check_interview(root, "un") == []
    # et accepter le document termine la conversation
    done = _say(ctx, rev, "oui")
    assert done["etape"]["kind"] == "termine" and turn.vue(ctx)["mode"] == "termine"


def _angle_of(root: Path, element: dict[str, Any]) -> str:
    conv = store.read(root / "_grimoire" / "cadrage" / "un", "un")
    assert conv is not None
    found = conv.element(element["id"])
    assert found is not None
    return found.angle


def test_les_lignes_ont_la_forme_de_l_entretien_guide_et_la_trace_permet_de_retirer(root: Path, ctx: turn.Contexte) -> None:
    rev = turn.demarrer(ctx)["rev"]
    _say(ctx, rev, "Les mainteneurs du kit")
    assert "Les mainteneurs" not in _doc(root, "01-brief.md")  # proposé : rien n'est écrit sans accord
    rev = turn.vue(ctx)["rev"]
    _say(ctx, rev, "oui")
    assert "- *Dis-moi : qui va se servir de ça, et dans quelle situation ?* Les mainteneurs du kit" in _doc(root, "01-brief.md")
    state = json.loads((root / "_grimoire/cadrage/un/entretien.json").read_text(encoding="utf-8"))
    assert state["angles"]["utilisateurs"]["lines"][0]["phase"] == "brief"
    assert interview.retract(root, "un", "utilisateurs")["removed"] is True  # la trace de l'entretien suffit à retirer


def test_une_reponse_hors_de_la_rubrique_de_la_question_va_dans_la_bonne(root: Path, ctx: turn.Contexte) -> None:
    class Range:
        id = "range"
        libelle = "Range"

        def __getattr__(self, name: str) -> Any:
            return getattr(MoteurSansModele(), name)

        def extraire_elements(self, entree: Any) -> list[ElementExtrait]:
            return [ElementExtrait("Il ne doit pas casser l'API", "comprehension/Contraintes")]

    moteur_ctx = make_ctx(root, Range())
    rev = turn.demarrer(moteur_ctx)["rev"]
    rev = _say(moteur_ctx, rev, "Les mainteneurs")["rev"]
    _say(moteur_ctx, rev, "oui")
    assert "Il ne doit pas casser l'API" in _doc(root, "03-comprehension.md")
    assert interview.view(root, "un")["angles"][4]["state"] == "couvert"  # l'angle « contraintes »


# ── je ne sais pas, sans objet, hors sujet, question ──────────────────────


def test_je_ne_sais_pas_range_une_question_ouverte_et_la_conversation_continue(root: Path, ctx: turn.Contexte) -> None:
    rev = turn.demarrer(ctx)["rev"]
    react = _say(ctx, rev, "Je ne sais pas")
    assert react["document_modifie"] is True and react["question"]["angle"] == "criteres"
    assert "- À préciser : Dis-moi : qui va se servir" in _doc(root, "02-brainstorm.md")
    state = turn.vue(ctx)
    assert state["ouvertes"][0]["texte"].startswith("Dis-moi : qui va se servir")
    assert [e["genre"] for e in state["elements"]] == ["inconnu"] and state["elements"][0]["statut"] == "confirme"
    assert interview.view(root, "un")["angles"][0]["state"] == "couvert"  # comme « je ne sais pas » de l'entretien guidé


def test_sans_objet_ecarte_l_angle_et_se_retire(root: Path, ctx: turn.Contexte) -> None:
    rev = turn.demarrer(ctx)["rev"]
    react = _say(ctx, rev, "sans objet")
    assert react["document_modifie"] is True and "sans objet" in react["accuse"]
    view = interview.view(root, "un")["angles"][0]
    assert view["state"] == "ecarte" and view["reason"] == "sans objet"
    turn.decider(ctx, react["rev"], turn.vue(ctx)["elements"][0]["id"], "ecarter")
    assert interview.view(root, "un")["angles"][0]["state"] == "ouvert"


def test_ne_sait_pas_hors_question_d_amorce_ne_change_rien(root: Path, ctx: turn.Contexte) -> None:
    rev = turn.demarrer(ctx)["rev"]
    rev = _say(ctx, rev, "Les mainteneurs")["rev"]  # en attente de confirmation
    react = _say(ctx, rev, "je ne sais pas")
    assert react["document_modifie"] is False and react["etape"]["kind"] == "confirmer"
    assert react["accuse"] == "Pas de souci, on y reviendra."


def test_le_hors_sujet_est_note_pour_plus_tard_et_la_question_reste(root: Path, ctx: turn.Contexte) -> None:
    rev = turn.demarrer(ctx)["rev"]
    react = _say(ctx, rev, "Au fait, il faudra penser au logo")
    state = turn.vue(ctx)
    assert [n["texte"] for n in state["plus_tard"]] == ["Au fait, il faudra penser au logo"]
    assert react["question"]["angle"] == "utilisateurs" and "pour plus tard" in react["accuse"]
    assert not state["elements"] and "logo" not in "".join(_all_docs(root).values())


def test_une_question_de_l_humain_recoit_le_pourquoi_ou_une_reponse_honnete(root: Path, ctx: turn.Contexte) -> None:
    rev = turn.demarrer(ctx)["rev"]
    why = _say(ctx, rev, "Pourquoi tu me demandes ça ?")
    assert why["accuse"].startswith("Je te demande ça pour savoir à qui ça sert") and turn.vue(ctx)["ouvertes"] == []
    other = _say(ctx, why["rev"], "C'est quoi un pipeline ?")
    assert "aucun modèle" in other["accuse"] and [n["texte"] for n in turn.vue(ctx)["ouvertes"]] == ["C'est quoi un pipeline ?"]
    assert other["question"]["angle"] == "utilisateurs"  # la question est reposée


def test_la_machine_dit_pourquoi_chaque_question_est_posee(ctx: turn.Contexte) -> None:
    react = turn.demarrer(ctx)
    assert react["question"]["pourquoi"].startswith("Je te demande ça ") and react["etape"]["pourquoi"]
    assert react["etape"]["manque"] and turn.vue(ctx)["manque"][0]["id"] == "utilisateurs"


# ── confirmer, corriger, écarter ──────────────────────────────────────────


def test_corriger_un_element_propose_le_range_corrige(root: Path, ctx: turn.Contexte) -> None:
    rev = turn.demarrer(ctx)["rev"]
    rev = _say(ctx, rev, "Les mainteneurs")["rev"]
    el = turn.vue(ctx)["elements"][0]
    react = turn.decider(ctx, rev, el["id"], "corriger", "Les mainteneurs ET les contributeurs")
    fixed = turn.vue(ctx)["elements"][0]
    assert fixed["statut"] == "corrige" and fixed["ecrit"] and fixed["avant"] == ["Les mainteneurs"]
    assert "Les mainteneurs ET les contributeurs" in _doc(root, "01-brief.md") and react["document_modifie"] is True


def test_corriger_un_element_deja_confirme_retire_puis_reecrit_en_gardant_l_ordre(root: Path, ctx: turn.Contexte) -> None:
    class Deux(MoteurSansModele):
        id = "deux"

        def extraire_elements(self, entree: Any) -> list[ElementExtrait]:
            return [ElementExtrait("Premier avis sur les utilisateurs", "brief/L'utilisateur"),
                    ElementExtrait("Second avis sur les utilisateurs", "brief/L'utilisateur")]

    ctx = make_ctx(root, Deux())
    rev = turn.demarrer(ctx)["rev"]
    for text in ("des avis", "oui"):
        rev = _say(ctx, rev, text)["rev"]
    state = turn.vue(ctx)
    first = state["elements"][0]
    assert [e["ecrit"] for e in state["elements"]] == [True, True]
    before = _doc(root, "01-brief.md")
    assert "Premier avis" in before and "Second avis" in before
    turn.decider(ctx, state["rev"], first["id"], "corriger", "Premier avis corrigé")
    after = _doc(root, "01-brief.md")
    assert "Premier avis corrigé" in after and "Premier avis sur" not in after and "Second avis" in after
    assert after.index("Premier avis corrigé") < after.index("Second avis")
    assert [e["statut"] for e in turn.vue(ctx)["elements"]] == ["corrige", "confirme"]
    answers = interview.view(root, "un")["angles"][0]["answers"]
    assert answers == ["Premier avis corrigé", "Second avis sur les utilisateurs"]


def test_ecarter_retire_du_document_et_l_angle_se_rouvre(root: Path, ctx: turn.Contexte) -> None:
    rev = turn.demarrer(ctx)["rev"]
    rev = _say(ctx, rev, "Les mainteneurs")["rev"]
    rev = _say(ctx, rev, "oui")["rev"]
    el = turn.vue(ctx)["elements"][0]
    assert "Les mainteneurs" in _doc(root, "01-brief.md")
    react = turn.decider(ctx, rev, el["id"], "ecarter")
    assert "Les mainteneurs" not in _doc(root, "01-brief.md") and react["document_modifie"] is True
    assert turn.vue(ctx)["elements"][0]["statut"] == "ecarte" and interview.view(root, "un")["angles"][0]["state"] == "ouvert"
    assert react["question"]["angle"] == "utilisateurs"  # la question revient : l'information manque de nouveau


def test_ecarter_un_element_encore_propose_n_ecrit_rien(root: Path, ctx: turn.Contexte) -> None:
    rev = turn.demarrer(ctx)["rev"]
    rev = _say(ctx, rev, "Les mainteneurs")["rev"]
    react = turn.decider(ctx, rev, turn.vue(ctx)["elements"][0]["id"], "ecarter")
    assert react["document_modifie"] is False and "mainteneurs" not in _doc(root, "01-brief.md")


def test_confirmer_par_le_geste_range_et_un_element_deja_tranche_est_un_conflit(root: Path, ctx: turn.Contexte) -> None:
    rev = turn.demarrer(ctx)["rev"]
    rev = _say(ctx, rev, "Les mainteneurs")["rev"]
    el = turn.vue(ctx)["elements"][0]["id"]
    react = turn.decider(ctx, rev, el, "confirmer")
    assert react["document_modifie"] is True and "Les mainteneurs" in _doc(root, "01-brief.md")
    with pytest.raises(ConversationConflictError) as err:
        turn.decider(ctx, react["rev"], el, "confirmer")
    assert err.value.reason == "deja-tranche"
    with pytest.raises(FileNotFoundError):
        turn.decider(ctx, react["rev"], "e99", "confirmer")
    with pytest.raises(ConversationError, match="décision inconnue"):
        turn.decider(ctx, react["rev"], el, "valider")
    with pytest.raises(ConversationError, match="texte"):
        turn.decider(ctx, react["rev"], el, "corriger", None)
    react = turn.decider(ctx, react["rev"], el, "ecarter")
    for decision in ("ecarter", "corriger"):
        with pytest.raises(ConversationConflictError):
            turn.decider(ctx, react["rev"], el, decision, "x")


def test_corriger_un_je_ne_sais_pas_est_refuse_mais_l_ecarter_le_retire(root: Path, ctx: turn.Contexte) -> None:
    rev = turn.demarrer(ctx)["rev"]
    react = _say(ctx, rev, "Je ne sais pas")
    el = turn.vue(ctx)["elements"][0]["id"]
    with pytest.raises(ConversationError, match="pas de texte à corriger"):
        turn.decider(ctx, react["rev"], el, "corriger", "x")
    turn.decider(ctx, react["rev"], el, "ecarter")
    assert "À préciser" not in _doc(root, "02-brainstorm.md")


def test_une_contradiction_remplace_ce_qui_avait_ete_confirme(root: Path, ctx: turn.Contexte) -> None:
    rev = turn.demarrer(ctx)["rev"]
    rev = _say(ctx, rev, "Les mainteneurs")["rev"]
    rev = _say(ctx, rev, "oui")["rev"]
    react = _say(ctx, rev, "En fait, plutôt les relecteurs")  # contredit, hors confirmation
    assert react["etape"]["kind"] == "confirmer" and "Je corrige donc : les relecteurs" in react["message"]
    assert "Les mainteneurs" in _doc(root, "01-brief.md")  # pas encore : il faut dire oui
    _say(ctx, react["rev"], "oui")
    brief = _doc(root, "01-brief.md")
    assert "Les mainteneurs" not in brief and "les relecteurs" in brief
    assert [e["statut"] for e in turn.vue(ctx)["elements"]] == ["ecarte", "confirme"]


def test_dire_non_a_une_reformulation_demande_ce_qui_ne_va_pas_puis_corrige(root: Path, ctx: turn.Contexte) -> None:
    rev = turn.demarrer(ctx)["rev"]
    rev = _say(ctx, rev, "Les mainteneurs")["rev"]
    ask = _say(ctx, rev, "non")
    assert ask["accuse"] == "Dis-moi ce qui ne va pas, avec tes mots." and ask["etape"]["kind"] == "confirmer"
    fixed = _say(ctx, ask["rev"], "Non, plutôt les testeurs")
    assert "les testeurs" in fixed["message"] and [e["statut"] for e in turn.vue(ctx)["elements"]] == ["ecarte", "propose"]
    _say(ctx, fixed["rev"], "oui")
    assert "les testeurs" in _doc(root, "01-brief.md") and "mainteneurs" not in _doc(root, "01-brief.md")


def test_une_contradiction_sans_rien_a_contredire_est_une_reponse_normale(ctx: turn.Contexte) -> None:
    rev = turn.demarrer(ctx)["rev"]
    react = _say(ctx, rev, "En fait, ce sont les testeurs")
    assert "les testeurs" in react["message"] and react["etape"]["kind"] == "confirmer"


def test_une_reponse_dont_rien_n_est_retenu_est_redemandee(ctx: turn.Contexte) -> None:
    rev = turn.demarrer(ctx)["rev"]
    react = _say(ctx, rev, "Finalement")
    assert react["accuse"].startswith("Je n'ai rien retenu") and react["question"]["kind"] == "amorce"


# ── brainstorm ─────────────────────────────────────────────────────────────


def test_le_brainstorm_sans_modele_range_les_pistes_de_l_humain_et_n_en_invente_pas(root: Path, ctx: turn.Contexte) -> None:
    rev = turn.demarrer(ctx)["rev"]
    start = _say(ctx, rev, "On brainstorme")
    assert start["etape"]["kind"] == "brainstorm" and "n'invente pas" in start["message"] and "Une piste" not in start["message"]
    ideas = _say(ctx, start["rev"], "un guide\nune vidéo ; un forum")
    assert ideas["etape"]["kind"] == "confirmer" and "un guide ; une vidéo ; un forum" in ideas["message"]
    ok = _say(ctx, ideas["rev"], "oui")
    brain = _doc(root, "02-brainstorm.md")
    assert "- *Piste du brainstorm* un guide" in brain and "- *Piste du brainstorm* une vidéo" in brain and "un forum" in brain
    assert "Quelle version plus simple" not in brain  # une piste n'est pas la réponse à une amorce du kit
    back = _say(ctx, ok["rev"], "c'est tout")
    assert back["etape"]["kind"] == "question" and turn.vue(ctx)["mode"] == "cadrage"


def test_un_moteur_avec_pistes_les_montre_sans_les_ranger(root: Path) -> None:
    class Inspire(MoteurSansModele):
        id = "inspire"

        def proposer_pistes(self, entree: Any) -> list[str]:
            return ["un atelier", "un questionnaire"]

    ctx2 = make_ctx(root, Inspire())
    rev = turn.demarrer(ctx2)["rev"]
    react = _say(ctx2, rev, "brainstorm")
    assert "Une piste : un atelier" in react["message"] and "Une piste : un questionnaire" in react["message"]
    assert turn.vue(ctx2)["elements"] == [] and "atelier" not in _doc(root, "02-brainstorm.md")


# ── pause, reprise, rechargement ───────────────────────────────────────────


def test_pause_puis_reprise_apres_rechargement_depuis_le_disque(root: Path, ctx: turn.Contexte) -> None:
    rev = turn.demarrer(ctx)["rev"]
    rev = _say(ctx, rev, "Les mainteneurs")["rev"]
    rev = _say(ctx, rev, "oui")["rev"]
    paused = _say(ctx, rev, "pause")
    assert paused["etape"]["kind"] == "pause" and turn.vue(ctx)["mode"] == "pause"
    with pytest.raises(ConversationConflictError) as err:
        _say(ctx, paused["rev"], "coucou")
    assert err.value.reason == "en-pause"
    fresh = make_ctx(root, now=T0 + timedelta(hours=2))  # un autre processus, un autre moteur : tout vient du disque
    back = turn.demarrer(fresh)
    line1, line2 = back["resume"].split("\n")
    assert line1.startswith("On a noté 1 point ; il me manque encore ") and line2.startswith("Ma prochaine question portera sur")
    assert back["cree"] is False and back["question"]["kind"] == "amorce" and back["question"]["angle"] == "criteres"
    assert turn.vue(fresh)["mode"] == "cadrage"
    assert "On a noté 1 point" in turn.vue(fresh)["tours"][-1]["texte"]
    again = turn.demarrer(fresh)  # déjà en cours : aucun effet
    assert again["rev"] == back["rev"] and again["resume"].split("\n")[1].startswith("Ma prochaine question : «")


def test_la_pause_par_le_geste_puis_terminer(root: Path, ctx: turn.Contexte) -> None:
    rev = turn.demarrer(ctx)["rev"]
    paused = turn.pause(ctx, rev)
    assert turn.vue(ctx)["mode"] == "pause"
    resumed = turn.demarrer(ctx)
    ended = turn.terminer(ctx, resumed["rev"])
    assert ended["etape"]["kind"] == "termine" and paused["rev"] < resumed["rev"] < ended["rev"]
    with pytest.raises(ConversationConflictError) as err:
        _say(ctx, ended["rev"], "encore")
    assert err.value.reason == "terminee"
    assert turn.demarrer(ctx)["etape"]["kind"] == "termine"


def test_le_refus_du_document_continue_a_poser_des_questions(root: Path, ctx: turn.Contexte) -> None:
    rev = turn.demarrer(ctx)["rev"]
    rev, _ = _drive(ctx, rev)
    no = _say(ctx, rev, "non")
    assert no["question"]["kind"] == "amorce" and turn.vue(ctx)["document"] == "refusee" and turn.vue(ctx)["plus_tard"] == []
    assert no["question"]["obligatoire"] is False  # on affine : ce n'est plus indispensable
    answered = _say(ctx, no["rev"], "Un avis de plus")  # la question d'affinage reçoit sa réponse comme les autres
    assert answered["etape"]["kind"] == "confirmer"


def test_un_refus_qui_porte_du_contenu_le_garde_pour_plus_tard_sans_le_ranger(root: Path, ctx: turn.Contexte) -> None:
    rev, _ = _drive(ctx, turn.demarrer(ctx)["rev"])
    react = _say(ctx, rev, "non, j'ajouterais aussi le point sur les logs")
    assert [n["texte"] for n in turn.vue(ctx)["plus_tard"]] == ["non, j'ajouterais aussi le point sur les logs"]
    assert react["question"]["kind"] == "amorce" and "logs" not in "".join(_all_docs(root).values())


def test_quand_tout_est_pose_la_fin_est_possible_et_un_texte_libre_est_garde_pour_plus_tard(ctx: turn.Contexte) -> None:
    rev, _ = _drive(ctx, turn.demarrer(ctx)["rev"])
    rev = _say(ctx, rev, "non")["rev"]
    for _ in range(40):  # on épuise affinage et facultatifs
        kind = turn.vue(ctx)["question"]["kind"]
        if kind not in ("amorce", "confirmer"):
            break
        rev = _say(ctx, rev, "oui" if kind == "confirmer" else "ok voilà")["rev"]
    final = turn.vue(ctx)
    assert final["question"]["kind"] == "document" and "plus de question" in final["question"]["texte"]
    free = _say(ctx, rev, "Une dernière précision sur le déploiement")
    assert [n["texte"] for n in turn.vue(ctx)["plus_tard"]] == ["Une dernière précision sur le déploiement"]
    assert free["question"]["kind"] == "document"
    assert _say(ctx, free["rev"], "oui")["etape"]["kind"] == "termine"


def test_une_demande_sans_entretien_dit_qu_il_n_y_a_rien_a_cadrer(root: Path) -> None:
    from grimoire.core import cadrage_conversation_flow as flow

    cadre = flow.Cadre((), interview=False)
    conv = turn.Conversation("un", T0.isoformat(), T0.isoformat())
    assert flow.prochaine_etape(conv, cadre).kind == "rien"
    ctx2 = make_ctx(root)
    assert ctx2.moteur.formuler_question(turn.EntreeQuestion("rien")).startswith("Ta demande n'a pas besoin")


# ── document accepté, concurrence, hygiène ────────────────────────────────


def test_dans_un_document_accepte_rien_n_est_ecrit(root: Path, ctx: turn.Contexte) -> None:
    rev = turn.demarrer(ctx)["rev"]
    rev = _say(ctx, rev, "Les mainteneurs")["rev"]
    brief = root / "_grimoire/cadrage/un/01-brief.md"
    brief.write_text(brief.read_text(encoding="utf-8").replace("status: draft", "status: accepted"), encoding="utf-8")
    before = _all_docs(root)
    saved = (root / "_grimoire/cadrage/un" / store.FILENAME).read_bytes()
    with pytest.raises(ConversationError, match="statut accepté"):
        _say(ctx, rev, "oui")
    el = turn.vue(ctx)["elements"][0]["id"]
    with pytest.raises(ConversationError, match="statut accepté"):
        turn.decider(ctx, rev, el, "confirmer")
    assert _all_docs(root) == before
    assert (root / "_grimoire/cadrage/un" / store.FILENAME).read_bytes() == saved  # rien n'est enregistré non plus
    assert turn.vue(ctx)["elements"][0]["statut"] == "propose"


def test_un_je_ne_sais_pas_dans_un_document_accepte_n_ecrit_rien(root: Path, ctx: turn.Contexte) -> None:
    rev = turn.demarrer(ctx)["rev"]
    brain = root / "_grimoire/cadrage/un/02-brainstorm.md"
    brain.write_text(brain.read_text(encoding="utf-8").replace("status: draft", "status: accepted"), encoding="utf-8")
    before = _all_docs(root)
    with pytest.raises(ConversationError, match="statut accepté"):
        _say(ctx, rev, "je ne sais pas")
    assert _all_docs(root) == before and turn.vue(ctx)["elements"] == []


def test_un_lot_de_confirmations_est_tout_ou_rien_si_un_fichier_est_accepte(root: Path, ctx: turn.Contexte) -> None:
    class Deux(MoteurSansModele):
        id = "deux"

        def extraire_elements(self, entree: Any) -> list[ElementExtrait]:
            return [ElementExtrait("Un utilisateur", "brief/L'utilisateur"), ElementExtrait("Une contrainte", "comprehension/Contraintes")]

    ctx2 = make_ctx(root, Deux())
    rev = turn.demarrer(ctx2)["rev"]
    rev = _say(ctx2, rev, "Les mainteneurs")["rev"]
    comp = root / "_grimoire/cadrage/un/03-comprehension.md"
    comp.write_text(comp.read_text(encoding="utf-8").replace("status: draft", "status: accepted"), encoding="utf-8")
    before = _all_docs(root)
    with pytest.raises(ConversationError, match="statut accepté"):
        _say(ctx2, rev, "oui")
    assert _all_docs(root) == before


def test_ecrire_sur_une_conversation_qui_a_change_est_un_conflit(ctx: turn.Contexte) -> None:
    rev = turn.demarrer(ctx)["rev"]
    _say(ctx, rev, "Les mainteneurs")
    for action in (lambda: _say(ctx, rev, "autre"), lambda: turn.pause(ctx, rev), lambda: turn.terminer(ctx, rev),
                   lambda: turn.classer(ctx, rev), lambda: turn.rouvrir(ctx, rev), lambda: turn.decider(ctx, rev, "e1", "confirmer")):
        with pytest.raises(ConversationConflictError) as err:
            action()
        assert err.value.reason == "conversation-changee"


def test_un_tour_vide_trop_long_ou_sans_conversation_est_refuse(root: Path, ctx: turn.Contexte) -> None:
    with pytest.raises(FileNotFoundError):
        _say(ctx, 0, "bonjour")
    for name in ("pause", "terminer", "classer", "rouvrir"):
        with pytest.raises(FileNotFoundError):
            getattr(turn, name)(ctx, 0)
    rev = turn.demarrer(ctx)["rev"]
    for bad in ("", "   ", None, 12, "x" * 5000):
        with pytest.raises(ConversationError):
            turn.recevoir(ctx, rev, bad)
    assert turn.vue(ctx)["rev"] == rev


def test_classer_et_rouvrir_avec_une_horloge_injectee(root: Path) -> None:
    ctx = make_ctx(root)
    rev = turn.demarrer(ctx)["rev"]
    sleeping = make_ctx(root, now=T0 + timedelta(days=4))
    assert turn.vue(sleeping)["statut"] == "en-sommeil"
    old = make_ctx(root, now=T0 + timedelta(days=20))
    assert turn.vue(old)["statut"] == "classee" and turn.vue(old)["classee"] is False
    with pytest.raises(ConversationError, match="classée"):
        _say(old, rev, "bonjour")
    with pytest.raises(ConversationError, match="classée"):
        turn.demarrer(old)
    with pytest.raises(ConversationError, match="pas classée"):
        turn.rouvrir(ctx, rev)
    reopened = turn.rouvrir(old, rev)
    assert reopened["statut"] == "active" and (root / "_grimoire/cadrage/un" / store.FILENAME).exists()
    classed = turn.classer(make_ctx(root, now=T0 + timedelta(days=21)), reopened["rev"])
    assert classed["statut"] == "classee" and turn.vue(make_ctx(root, now=T0 + timedelta(days=21)))["classee"] is True
    with pytest.raises(ConversationError, match="classée"):
        turn.pause(make_ctx(root, now=T0 + timedelta(days=21)), classed["rev"])


def test_la_vue_et_le_compris_se_lisent_sans_rien_ecrire(root: Path, ctx: turn.Contexte) -> None:
    assert turn.vue(ctx)["existe"] is False and turn.compris(ctx) == {"existe": False, "rubriques": [], "ecartes": 0}
    assert not (root / "_grimoire/cadrage/un" / store.FILENAME).exists()
    rev = turn.demarrer(ctx)["rev"]
    rev = _say(ctx, rev, "Les mainteneurs")["rev"]
    _say(ctx, rev, "oui")
    before = _all_docs(root)
    state = turn.vue(ctx)
    assert state["moteur"]["sans_modele"] is True and state["moteur"]["limites"] and state["avancement"]["etat"] == "en-cours"
    assert state["tours_total"] == len(state["tours"])
    seen = turn.compris(ctx)
    assert seen["rubriques"][0]["section"] == "L'utilisateur" and seen["rubriques"][0]["phase_titre"].startswith("Brief")
    assert seen["rubriques"][0]["elements"][0]["texte"] == "Les mainteneurs" and seen["ecartes"] == 0
    assert _all_docs(root) == before


def test_un_fichier_de_conversation_abime_se_lit_en_erreur_et_n_est_jamais_ecrase(root: Path, ctx: turn.Contexte) -> None:
    path = root / "_grimoire/cadrage/un" / store.FILENAME
    path.write_text('{"schema": 1, "mode": "hack"}', encoding="utf-8")
    assert "erreur" in turn.vue(ctx) and "erreur" in turn.compris(ctx)
    for call in (lambda: turn.demarrer(ctx), lambda: _say(ctx, 0, "x")):
        with pytest.raises(ConversationError):
            call()
    assert path.read_text(encoding="utf-8") == '{"schema": 1, "mode": "hack"}'


def test_un_entretien_abime_est_dit_sans_rien_ecrire(root: Path, ctx: turn.Contexte) -> None:
    rev = turn.demarrer(ctx)["rev"]
    (root / "_grimoire/cadrage/un/entretien.json").write_text("{pas du json", encoding="utf-8")
    with pytest.raises(ConversationError, match=r"entretien\.json"):
        _say(ctx, rev, "bonjour")


def test_le_chantier_principal_se_cadre_aussi(tmp_path: Path) -> None:
    from grimoire.core.cadrage import scaffold

    scaffold(tmp_path, project_name="demo")
    interview.interview_plan(tmp_path, None, depth="express")
    ctx = turn.Contexte(tmp_path, "principal", tmp_path / "_grimoire" / "cadrage", Garde(MoteurSansModele()), T0, Reglages())
    rev = turn.demarrer(ctx)["rev"]
    rev = _say(ctx, rev, "Les mainteneurs")["rev"]
    _say(ctx, rev, "oui")
    assert "Les mainteneurs" in (tmp_path / "_grimoire/cadrage/01-brief.md").read_text(encoding="utf-8")


# ── un moteur hostile ne sort pas du cadre ────────────────────────────────


class Hostile(MoteurSansModele):
    """Un moteur qui triche : rubriques inventées, textes énormes, exceptions, lenteur."""

    id = "hostile"

    def __init__(self, **out: Any) -> None:
        self.out = out

    def _maybe(self, name: str, fallback: Any) -> Any:
        value = self.out.get(name)
        if isinstance(value, BaseException):
            raise value
        return fallback if value is None else (value() if callable(value) else value)

    def classer_reponse(self, entree: Any) -> Any:
        return self._maybe("classe", super().classer_reponse(entree))

    def extraire_elements(self, entree: Any) -> Any:
        return self._maybe("elements", super().extraire_elements(entree))

    def formuler_question(self, entree: Any) -> Any:
        return self._maybe("question", super().formuler_question(entree))

    def reformuler(self, entree: Any) -> Any:
        return self._maybe("reformulation", super().reformuler(entree))


@pytest.mark.parametrize(
    "out",
    [
        {"elements": [ElementExtrait("pirate", "exigences/Fonctionnelles")]},
        {"elements": [ElementExtrait("pirate", "../../etc/passwd")]},
        {"elements": [ElementExtrait("x" * 100_000, "brief/L'utilisateur")]},
        {"elements": "pas une liste"},
        {"elements": RuntimeError("boum")},
        {"classe": "exec"},
        {"classe": RuntimeError("boum")},
        {"question": "q" * 100_000},
        {"question": RuntimeError("boum")},
        {"reformulation": "r" * 100_000},
        {"reformulation": ValueError("boum")},
        {"classe": lambda: time.sleep(1) or "reponse"},
    ],
)
def test_un_moteur_hostile_ne_sort_pas_du_cadre(root: Path, out: dict[str, Any]) -> None:
    ctx2 = make_ctx(root, Hostile(**out), delai_s=0.2)
    rev = turn.demarrer(ctx2)["rev"]
    react = _say(ctx2, rev, "Les mainteneurs")
    assert react["moteur"]["degrade"] is True and react["moteur"]["notes"]
    state = turn.vue(ctx2)
    assert [e["texte"] for e in state["elements"]] == ["Les mainteneurs"]  # le texte de l'humain n'est pas perdu
    assert all(e["phase"] == "brief" for e in state["elements"])
    before = _all_docs(root)
    assert "pirate" not in "".join(before.values()) and "passwd" not in "".join(before.values())
    after = _say(ctx2, react["rev"], "oui")
    assert "Les mainteneurs" in _doc(root, "01-brief.md") and after["document_modifie"] is True
    assert store.read(root / "_grimoire/cadrage/un", "un") is not None  # le fichier reste valide
    assert not list((root / "_grimoire/cadrage").rglob("passwd"))


def test_un_moteur_ne_recoit_ni_chemin_ni_moyen_d_ecrire(root: Path) -> None:
    seen: list[Any] = []

    class Espion(MoteurSansModele):
        id = "espion"

        def extraire_elements(self, entree: Any) -> Any:
            seen.append(entree)
            return super().extraire_elements(entree)

        def formuler_question(self, entree: Any) -> Any:
            seen.append(entree)
            return super().formuler_question(entree)

    ctx2 = make_ctx(root, Espion())
    rev = turn.demarrer(ctx2)["rev"]
    _say(ctx2, rev, "Les mainteneurs")
    flat = repr(seen)
    assert str(root) not in flat and "_grimoire" not in flat and "Path" not in flat


def test_un_moteur_qui_dit_oui_hors_confirmation_est_traite_comme_une_reponse(root: Path) -> None:
    ctx2 = make_ctx(root, Hostile(classe="confirme"))
    rev = turn.demarrer(ctx2)["rev"]
    react = _say(ctx2, rev, "Les mainteneurs")
    assert react["etape"]["kind"] == "confirmer" and "mainteneurs" not in _doc(root, "01-brief.md")
    ctx3 = make_ctx(root, Hostile(classe="reprise"))
    assert _say(ctx3, react["rev"], "x")["accuse"]  # une classe valide mais hors contexte ne casse rien
