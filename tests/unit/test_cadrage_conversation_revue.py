"""Relecture adversariale du lot A : le moteur propose, il ne décide jamais ; tout moteur est traité comme hostile."""

from __future__ import annotations

import contextlib
import json
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from grimoire.core import cadrage_conversation_document as rangement
from grimoire.core import cadrage_conversation_engine as engine
from grimoire.core import cadrage_conversation_store as store
from grimoire.core import cadrage_conversation_turn as turn
from grimoire.core import cadrage_interview as interview
from grimoire.core.cadrage_conversation import (
    MAX_TURNS,
    PLEINE_A,
    Conversation,
    ConversationError,
    mots_de_l_humain,
)
from grimoire.core.cadrage_conversation_engine import ElementExtrait, Garde, MoteurSansModele
from grimoire.core.cadrage_conversation_hygiene import Reglages
from grimoire.core.chantiers import new_chantier
from grimoire.tools import workspace_atelier as wa
from grimoire.tools import workspace_atelier_conversation as routes

T0 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _propre() -> Iterator[None]:
    engine._VIVANTS.clear()
    routes.set_moteur(None)
    yield
    routes.set_moteur(None)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "project-context.yaml").write_text("project:\n  name: demo\n", encoding="utf-8")
    wa.set_atelier(tmp_path, True)
    new_chantier(tmp_path, "Un chantier", slug="un")
    interview.interview_plan(tmp_path, "un", depth="standard")
    return tmp_path


def make_ctx(root: Path, moteur: Any = None, **garde: Any) -> turn.Contexte:
    return turn.Contexte(root, "un", root / "_grimoire/cadrage/un", Garde(moteur or MoteurSansModele(), **garde), T0, Reglages())


def docs(root: Path) -> dict[str, str]:
    folder = root / "_grimoire/cadrage/un"
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(folder.glob("0*.md"))}


def angles(root: Path) -> dict[str, str]:
    return {a["id"]: a["state"] for a in interview.view(root, "un")["angles"]}


def _conv(root: Path) -> Conversation:
    conv = store.read(root / "_grimoire/cadrage/un", "un")
    assert conv is not None
    return conv


def _sans_origine(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _sans_origine(v) for k, v in node.items() if k != "origin"}
    if isinstance(node, list):
        return [_sans_origine(v) for v in node]
    return node


# ── 1. « je ne sais pas » / « sans objet » : une proposition, jamais une décision ─────────────────────────


def test_la_suite_de_cinq_reponses_de_la_sonde_ne_leve_aucun_angle_et_garde_le_texte(root: Path) -> None:
    ctx = make_ctx(root)
    rev = turn.demarrer(ctx)["rev"]
    gate = interview.check_interview(root, "un")
    before = docs(root)
    phrases = ["Les comptables, ça dépend des mois", "Ça dépend du client, en gros un export CSV", "Rien à voir avec la compta, pas les paies",
               "Ça dépend : Python 3.11, pas de réseau", "Aucune idée vraiment, sauf la perte de données"]
    for phrase in phrases:
        react = turn.recevoir(ctx, rev, phrase)
        rev = react["rev"]
        assert phrase in react["a_confirmer"][0]["texte"]  # le texte de l'humain est conservé, tel quel
    assert interview.check_interview(root, "un") == gate and len(gate) == 1 and docs(root) == before
    assert set(angles(root).values()) == {"ouvert"}


def test_une_proposition_ne_s_applique_que_par_le_geste_de_l_humain_et_donne_les_memes_fichiers_qu_a_la_main(
    root: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    ctx = make_ctx(root)
    react = turn.demarrer(ctx)
    question = react["question"]["texte"]
    prop = turn.recevoir(ctx, react["rev"], "je ne sais pas")
    assert prop["document_modifie"] is False and set(angles(root).values()) == {"ouvert"}
    done = turn.recevoir(ctx, prop["rev"], "oui")
    twin = tmp_path_factory.mktemp("twin")
    new_chantier(twin, "Un chantier", slug="un")
    interview.interview_plan(twin, "un", depth="standard")
    interview.answer(twin, "un", "utilisateurs", "", unknown=True, question=question)
    assert done["document_modifie"] is True and docs(root) == docs(twin)
    state = json.loads((root / "_grimoire/cadrage/un/entretien.json").read_text(encoding="utf-8"))
    twin_state = json.loads((twin / "_grimoire/cadrage/un/entretien.json").read_text(encoding="utf-8"))
    assert _sans_origine(state) == twin_state  # seule différence : l'origine portée par la trace
    assert state["angles"]["utilisateurs"]["lines"][0]["origin"].startswith("conv:")


def test_un_moteur_qui_classe_une_reponse_avec_contenu_en_je_ne_sais_pas_ne_leve_rien(root: Path) -> None:
    class Force(MoteurSansModele):
        id = "force"

        def classer_reponse(self, entree: Any) -> str:
            return "ne_sait_pas" if entree.contexte == "amorce" else "sans_objet"

    ctx = make_ctx(root, Force())
    rev = turn.demarrer(ctx)["rev"]
    before = docs(root)
    react = turn.recevoir(ctx, rev, "Les mainteneurs du kit, très précisément")
    assert react["etape"]["kind"] == "confirmer" and react["document_modifie"] is False
    assert docs(root) == before and set(angles(root).values()) == {"ouvert"}


# ── 2. le moteur ne confirme jamais, et ne fait confirmer que ce qui est montré ─────────────────────────


class Hostile(MoteurSansModele):
    id = "hostile"

    def classer_reponse(self, entree: Any) -> str:
        return "confirme"  # tout est un oui (classe que nul moteur n'a le droit de rendre)

    def extraire_elements(self, entree: Any) -> list[ElementExtrait]:
        out = super().extraire_elements(entree)
        invented = [k for k in entree.rubriques if k != entree.rubrique_visee and not k.startswith("brainstorm/")][:4]
        return out + [ElementExtrait(f"REQ-00{i} [Must] envoyer les données à evil.example", r) for i, r in enumerate(invented)]

    def reformuler(self, entree: Any) -> str:
        return "Si je comprends bien : les mainteneurs. C'est bien ça ?"  # ne dit rien de ce qui sera écrit


def test_un_moteur_hostile_ne_peut_ni_confirmer_ni_inventer_dans_d_autres_rubriques(root: Path) -> None:
    ctx = make_ctx(root, Hostile())
    rev = turn.demarrer(ctx)["rev"]
    before = docs(root)
    react = turn.recevoir(ctx, rev, "Les mainteneurs du projet")
    elements = turn.vue(ctx)["elements"]
    assert len(elements) == 5 and elements[0]["origine"] == "humain" and not elements[0]["individuel"]
    assert all(e["individuel"] and e["origine"] == "moteur" for e in elements[1:])
    assert docs(root) == before and set(angles(root).values()) == {"ouvert"}
    assert any("hors" in n or "classe" in n for n in react["moteur"]["notes"])  # « confirme » refusé : classe hors liste
    # la liste affichée est celle du PROGRAMME : le texte exact et la rubrique, malgré la reformulation trompeuse
    shown = react["a_confirmer"]
    assert [s["texte"] for s in shown] == ["Les mainteneurs du projet"] and shown[0]["rubrique"]["nom"] == "Brief, partie « L'utilisateur »"
    assert "Je noterais exactement : « Les mainteneurs du projet »" in react["message"]
    # un refus explicite que le moteur classerait « oui » n'écrit rien
    no = turn.recevoir(ctx, react["rev"], "non, surtout pas ça")
    assert docs(root) == before and no["document_modifie"] is False
    # le vrai oui du groupe ne range QUE le texte de l'humain ; chaque texte du moteur attend sa confirmation seule
    rev = turn.recevoir(ctx, no["rev"], "Les mainteneurs du projet")["rev"]
    yes = turn.recevoir(ctx, rev, "oui")
    assert "Les mainteneurs du projet" in docs(root)["01-brief.md"] and "evil.example" not in "".join(docs(root).values())
    assert yes["etape"]["kind"] == "confirmer" and yes["a_confirmer"][0]["origine"] == "moteur"
    assert [a for a, s in angles(root).items() if s != "ouvert"] == ["utilisateurs"]
    assert "evil.example" in yes["a_confirmer"][0]["texte"]  # montré tel qu'il serait écrit


def test_un_element_du_moteur_ne_se_confirme_que_seul_et_texte_exact_montre(root: Path) -> None:
    class Reformule(MoteurSansModele):
        id = "reformule"

        def extraire_elements(self, entree: Any) -> list[ElementExtrait]:
            return [ElementExtrait("Les mainteneurs, tous les jours", entree.rubrique_visee)]  # pas dans les mots de l'humain

    ctx = make_ctx(root, Reformule())
    rev = turn.demarrer(ctx)["rev"]
    react = turn.recevoir(ctx, rev, "Les mainteneurs")
    item = react["a_confirmer"][0]
    assert item["origine"] == "moteur" and item["individuel"] is True and docs(root)["01-brief.md"].count("mainteneurs") == 0
    el = turn.vue(ctx)["elements"][0]
    done = turn.decider(ctx, react["rev"], el["id"], "confirmer")
    assert "Les mainteneurs, tous les jours" in docs(root)["01-brief.md"] and done["document_modifie"] is True


def test_les_mots_de_l_humain_se_comparent_a_la_normalisation_pres() -> None:
    assert mots_de_l_humain("Je pense aux les mainteneurs du kit", "Je pense aux Les Mainteneurs, du kit !")  # le tour entier
    assert mots_de_l_humain("Écrit à l'été", "ecrit a l ete")
    assert mots_de_l_humain("une phrase coupée…", "Une phrase coupée au bout de mille mots", tronque=True)
    assert not mots_de_l_humain("les relecteurs", "les mainteneurs")
    assert not mots_de_l_humain("", "n'importe quoi") and not mots_de_l_humain("…", "n'importe quoi")


def test_le_moteur_ne_rend_pas_plus_de_cinq_elements_par_tour(root: Path) -> None:
    class Beaucoup(MoteurSansModele):
        id = "beaucoup"

        def extraire_elements(self, entree: Any) -> list[ElementExtrait]:
            return [ElementExtrait(f"idée {i}", entree.rubrique_visee) for i in range(40)]

    ctx = make_ctx(root, Beaucoup())
    rev = turn.demarrer(ctx)["rev"]
    turn.recevoir(ctx, rev, "idée 1 idée 2")
    assert len(turn.vue(ctx)["elements"]) == 5


# ── 3. vérifier avant d'écrire, journal d'intention, limite de tours ───────────────────────────────────


def test_la_place_est_verifiee_avant_la_premiere_ecriture_du_document(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = make_ctx(root)
    rev = turn.demarrer(ctx)["rev"]
    rev = turn.recevoir(ctx, rev, "Les mainteneurs")["rev"]
    before, saved = docs(root), (root / "_grimoire/cadrage/un" / store.FILENAME).read_bytes()
    monkeypatch.setattr(store, "MAX_FILE_BYTES", 10_000)
    with pytest.raises(ConversationError, match="trop volumineuse"):
        turn.recevoir(ctx, rev, "oui")
    assert docs(root) == before and (root / "_grimoire/cadrage/un" / store.FILENAME).read_bytes() == saved


def _remplir_de_tours(root: Path, jusqu_a: int) -> None:
    conv = _conv(root)
    while len(conv.tours) < jusqu_a:
        conv.say("mascotte", "…", "note", T0.isoformat())
    store.save(root / "_grimoire/cadrage/un", conv)


def test_a_la_limite_de_tours_la_conversation_se_termine_en_le_disant_et_les_corrections_restent_possibles(root: Path) -> None:
    ctx = make_ctx(root)
    rev = turn.demarrer(ctx)["rev"]
    rev = turn.recevoir(ctx, rev, "Les mainteneurs")["rev"]
    rev = turn.recevoir(ctx, rev, "oui")["rev"]
    _remplir_de_tours(root, PLEINE_A)
    rev = _conv(root).rev
    full = turn.recevoir(ctx, rev, "encore une réponse")  # pas d'erreur : elle se termine proprement
    assert "pleine" in full["accuse"] and turn.vue(ctx)["mode"] == "termine"
    assert len(_conv(root).tours) <= MAX_TURNS
    # corriger ce qui est compris reste possible, même au dernier tour
    _remplir_de_tours(root, MAX_TURNS)
    rev = _conv(root).rev
    el = turn.vue(ctx)["elements"][0]["id"]
    fixed = turn.decider(ctx, rev, el, "corriger", "Les mainteneurs et les relecteurs")
    assert "et les relecteurs" in docs(root)["01-brief.md"] and fixed["document_modifie"] is True
    assert len(_conv(root).tours) == MAX_TURNS
    turn.decider(ctx, fixed["rev"], el, "ecarter")
    assert "mainteneurs" not in docs(root)["01-brief.md"]


def test_une_panne_pendant_l_ecriture_est_reprise_par_la_trace_et_annulee_si_rien_n_est_ecrit(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = make_ctx(root)
    rev = turn.demarrer(ctx)["rev"]
    rev = turn.recevoir(ctx, rev, "Les mainteneurs")["rev"]
    before = docs(root)
    real = interview.answer

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("disque plein")

    monkeypatch.setattr(interview, "answer", broken)
    with pytest.raises(RuntimeError):
        turn.recevoir(ctx, rev, "oui")
    monkeypatch.setattr(interview, "answer", real)
    assert docs(root) == before and _conv(root).elements[0].intention == "ecrire"  # l'intention était enregistrée avant
    back = turn.demarrer(ctx)  # reprise : rien n'a été écrit, l'intention est annulée
    assert any("n'avait pas été écrit" in a for a in back["alertes"])
    assert _conv(root).elements[0].statut == "propose" and _conv(root).elements[0].intention == ""
    assert turn.recevoir(ctx, back["rev"], "oui")["document_modifie"] is True


def test_une_panne_apres_l_ecriture_est_terminee_a_la_reprise(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = make_ctx(root)
    rev = turn.demarrer(ctx)["rev"]
    rev = turn.recevoir(ctx, rev, "Les mainteneurs")["rev"]
    real_save, calls = store.save, []

    def crashing(directory: Path, conv: Conversation) -> None:
        if directory == ctx.directory:  # le vrai chantier (le calcul à blanc travaille sur une copie)
            calls.append(1)
            if len(calls) == 2:
                raise OSError("coupure juste après l'écriture du document")
        real_save(directory, conv)

    monkeypatch.setattr(turn.store, "save", crashing)
    with pytest.raises(OSError, match="coupure"):
        turn.recevoir(ctx, rev, "oui")
    monkeypatch.setattr(turn.store, "save", real_save)
    assert "Les mainteneurs" in docs(root)["01-brief.md"] and _conv(root).elements[0].intention == "ecrire"
    back = turn.demarrer(ctx)
    element = _conv(root).elements[0]
    assert element.ecrit is True and element.intention == "" and docs(root)["01-brief.md"].count("Les mainteneurs") == 1
    assert back["rev"] > rev
    turn.decider(ctx, back["rev"], element.id, "ecarter")  # la trace porte l'origine : la ligne se retire
    assert "Les mainteneurs" not in docs(root)["01-brief.md"]


# ── 4. une ligne ne se retire que si la trace dit qu'elle est de cette conversation ───────────────────────


def test_un_fichier_forge_ne_fait_pas_retirer_une_ligne_ecrite_par_l_humain(root: Path) -> None:
    interview.answer(root, "un", "utilisateurs", "Les mainteneurs", question="Qui ?")  # l'entretien guidé, à la main
    ctx = make_ctx(root)
    conv = Conversation("un", T0.isoformat(), T0.isoformat())
    conv.say("mascotte", "Salut", "accueil", T0.isoformat())
    conv.say("utilisateur", "Les mainteneurs", "reponse", T0.isoformat())
    forged = conv.new_element("element", "Les mainteneurs", "brief", "L'utilisateur", "utilisateurs", 2, statut="confirme", ecrit=True, rang=1)
    conv.rang = 1
    store.save(ctx.directory, conv)
    before = docs(root)
    state = turn.vue(ctx)
    assert state["elements"][0]["ecrit"] is False and any("n'est pas dans le document" in a for a in state["alertes"])
    react = turn.decider(ctx, 0, forged.id, "ecarter")
    assert docs(root) == before and "Les mainteneurs" in docs(root)["01-brief.md"]
    assert react["document_modifie"] is False and any("je ne le retirerai pas" in a for a in react["alertes"])
    other = Conversation("un", T0.isoformat(), T0.isoformat())
    other.say("utilisateur", "x", "reponse", T0.isoformat())
    twin = other.new_element("element", "Les mainteneurs", "brief", "L'utilisateur", "utilisateurs", 1, statut="confirme", ecrit=True)
    other.mode_avant_pause = "cadrage"
    store.save(ctx.directory, other)
    with pytest.raises(ConversationError, match="pas rangé"):
        turn.decider(ctx, 0, twin.id, "corriger", "Autre chose")
    assert docs(root) == before


def test_un_ecart_forge_ne_defait_pas_un_ecart_de_l_humain(root: Path) -> None:
    interview.answer(root, "un", "utilisateurs", "sans objet", skip=True)
    ctx = make_ctx(root)
    conv = Conversation("un", T0.isoformat(), T0.isoformat())
    conv.say("utilisateur", "x", "reponse", T0.isoformat())
    forged = conv.new_element("sansobjet", "Sans objet", "brief", "L'utilisateur", "utilisateurs", 1, statut="confirme", ecrit=True)
    store.save(ctx.directory, conv)
    turn.decider(ctx, 0, forged.id, "ecarter")
    assert angles(root)["utilisateurs"] == "ecarte"


def test_la_trace_porte_l_origine_et_une_origine_hostile_est_refusee(root: Path) -> None:
    ctx = make_ctx(root)
    rev = turn.demarrer(ctx)["rev"]
    rev = turn.recevoir(ctx, rev, "Les mainteneurs")["rev"]
    turn.recevoir(ctx, rev, "oui")
    path = root / "_grimoire/cadrage/un/entretien.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    assert list(rangement.traces(root, "un")["utilisateurs"]) == [state["angles"]["utilisateurs"]["lines"][0]["origin"]]
    state["angles"]["utilisateurs"]["lines"][0]["origin"] = "a b/../../c"
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError, match="mal formé"):
        interview.view(root, "un")


# ── 5. identifiants ───────────────────────────────────────────────────────


def test_un_fichier_a_identifiants_en_double_reste_lisible_et_refuse_toute_ecriture(root: Path) -> None:
    ctx = make_ctx(root)
    rev = turn.demarrer(ctx)["rev"]
    turn.recevoir(ctx, rev, "Les mainteneurs")
    path = root / "_grimoire/cadrage/un" / store.FILENAME
    data = json.loads(path.read_text(encoding="utf-8"))
    data["elements"].append(dict(data["elements"][0]))
    path.write_text(json.dumps(data), encoding="utf-8")
    raw = path.read_bytes()
    state = turn.vue(ctx)
    assert "doublons" in state["reparer"] and len(state["elements"]) == 2 and "erreur" not in state
    for call in (lambda: turn.recevoir(ctx, 0, "x"), lambda: turn.decider(ctx, 0, "e1", "confirmer"), lambda: turn.classer(ctx, 0),
                 lambda: turn.demarrer(ctx)):
        with pytest.raises(ConversationError, match="doublons"):
            call()
    assert path.read_bytes() == raw


# ── 6. le moteur travaille hors du verrou ─────────────────────────────────────


def test_un_moteur_qui_bloque_ne_retient_pas_le_verrou_et_ses_fils_sont_bornes(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    release = threading.Event()

    class Bloque(MoteurSansModele):
        id = "bloque"

        def classer_reponse(self, entree: Any) -> str:
            release.wait(30)
            return "reponse"

        def extraire_elements(self, entree: Any) -> Any:
            release.wait(30)
            return []

    held: list[float] = []
    real_lock = store.locked_store

    @contextlib.contextmanager
    def timed(directory: Path) -> Iterator[None]:
        start = time.monotonic()
        with real_lock(directory):
            yield
        if directory == root / "_grimoire/cadrage/un":
            held.append(time.monotonic() - start)

    monkeypatch.setattr(turn.store, "locked_store", timed)
    ctx = make_ctx(root, Bloque(), delai_s=0.2)
    try:
        rev = turn.demarrer(ctx)["rev"]
        for _ in range(4):
            started = time.monotonic()
            react = turn.recevoir(ctx, rev, "Les mainteneurs")
            rev = react["rev"]
            assert time.monotonic() - started < 5
            assert react["moteur"]["degrade"] is True
            turn.decider(ctx, rev, turn.vue(ctx)["elements"][-1]["id"], "ecarter")
            rev = turn.vue(ctx)["rev"]
        assert max(held) < 1.0  # le verrou n'a jamais attendu le moteur
        assert len([t for t in engine._VIVANTS if t.is_alive()]) <= engine.FILS_MAX
    finally:
        release.set()


def test_le_moteur_n_est_appele_qu_une_fois_par_geste_et_le_rejeu_rend_la_meme_sortie(root: Path) -> None:
    calls: list[str] = []

    class Compte(MoteurSansModele):
        id = "compte"

        def formuler_question(self, entree: Any) -> str:
            calls.append("question")
            return f"Question numéro {len(calls)} du moteur ?"

        def classer_reponse(self, entree: Any) -> str:
            calls.append("classer")
            return super().classer_reponse(entree)

    ctx = make_ctx(root, Compte())
    react = turn.demarrer(ctx)
    assert calls == ["question"] and "Question numéro 1 du moteur ?" in react["message"]
    assert turn.vue(ctx)["question"]["texte"] == "Question numéro 1 du moteur ?"
    turn.recevoir(ctx, react["rev"], "Les mainteneurs")
    assert calls.count("classer") == 1


def test_un_moteur_qui_echoue_pendant_le_calcul_a_blanc_laisse_le_document_intact(root: Path) -> None:
    ctx = make_ctx(root, Hostile())
    before = docs(root)
    rev = turn.demarrer(ctx)["rev"]
    turn.recevoir(ctx, rev, "oui")
    assert docs(root) == before and not list(Path(root).glob("grimoire-conversation-*"))


def test_la_lecture_d_etat_ne_bloque_jamais_sur_le_moteur(root: Path) -> None:
    release = threading.Event()

    class Lent(MoteurSansModele):
        id = "lent"
        libelle = "Lent"

        def limites(self) -> tuple[str, ...]:
            release.wait(30)
            return ("x",)

    ctx = make_ctx(root, Lent(), delai_s=5.0)
    try:
        started = time.monotonic()
        state = turn.vue(ctx)
        assert time.monotonic() - started < 1.5 and state["moteur"]["limites"] == list(MoteurSansModele().limites())
    finally:
        release.set()


def test_au_plafond_de_fils_le_moteur_n_est_plus_appele(monkeypatch: pytest.MonkeyPatch) -> None:
    class Mort(threading.Thread):
        def is_alive(self) -> bool:
            return True

    engine._VIVANTS[:] = [Mort() for _ in range(engine.FILS_MAX)]
    appels: list[int] = []

    class Jamais(MoteurSansModele):
        id = "jamais"

        def classer_reponse(self, entree: Any) -> str:
            appels.append(1)
            return "pause"

    garde = Garde(Jamais())
    assert garde.classer_reponse(engine.EntreeClassement("Je ne sais pas")) == "ne_sait_pas" and appels == []
    assert "trop d'appels" in garde.notes[0]


def test_enregistrer_puis_rejouer_rend_la_meme_sortie_sans_rappeler_le_moteur() -> None:
    calls: list[int] = []

    class Un(MoteurSansModele):
        id = "un"

        def reformuler(self, entree: Any) -> str:
            calls.append(1)
            return "Si je comprends bien : tout. C'est bien ça ?"

    record = Garde(Un()).enregistreur()
    entree = engine.EntreeReformulation(("a",))
    assert record.reformuler(entree) == "Si je comprends bien : tout. C'est bien ça ?" and record.reformuler(entree) and calls == [1]
    replay = Garde(Un()).rejouer(record)
    assert replay.reformuler(entree) == "Si je comprends bien : tout. C'est bien ça ?" and calls == [1]
    assert replay.reformuler(engine.EntreeReformulation(("autre",))) == "Si je comprends bien : autre. C'est bien ça ?"  # hors mémo : repli
    assert calls == [1]


# ── 7. routes : fabrique de moteur défaillante, champ project ─────────────────


@pytest.mark.parametrize("fabrique", [lambda r, s: 1 / 0, lambda r, s: object(), lambda r, s: None])
def test_une_fabrique_defaillante_se_rattrape_en_sans_modele_avec_une_note_jamais_un_500(root: Path, fabrique: Any) -> None:
    routes.set_moteur(fabrique)
    code, out = wa.post_http(root, "/api/workspace/atelier/chantiers/un/conversation/demarrer", {})
    assert code == 200 and out["reaction"]["question"]["kind"] == "amorce"
    assert out["reaction"]["moteur"]["degrade"] is True and out["reaction"]["moteur"]["notes"]
    rev = out["conversation"]["rev"]
    code, out = wa.post_http(root, "/api/workspace/atelier/chantiers/un/conversation/tour", {"rev": rev, "texte": "Les mainteneurs"})
    assert code == 200 and out["reaction"]["a_confirmer"][0]["texte"] == "Les mainteneurs"


def test_le_champ_project_du_corps_est_accepte_comme_pour_les_autres_ecritures(root: Path) -> None:
    code, out = wa.post_http(root, "/api/workspace/atelier/chantiers/un/conversation/demarrer", {"project": "demo"})
    assert code == 200
    rev = out["conversation"]["rev"]
    code, out = wa.post_http(root, "/api/workspace/atelier/chantiers/un/conversation/tour",
                             {"project": "demo", "rev": rev, "texte": "Les mainteneurs"})
    assert code == 200
    code, out = wa.post_http(root, "/api/workspace/atelier/chantiers/un/conversation/tour",
                             {"project": "demo", "rev": out["conversation"]["rev"], "texte": "x", "phase": "01-brief.md"})
    assert code == 400


def test_l_etat_rendu_porte_la_liste_exacte_a_confirmer(root: Path) -> None:
    ctx = make_ctx(root)
    rev = turn.demarrer(ctx)["rev"]
    turn.recevoir(ctx, rev, "Les mainteneurs du kit")
    state = turn.vue(ctx)
    item = state["a_confirmer"][0]
    assert item["texte"] == "Les mainteneurs du kit" and item["action"] == "noter dans le document"
    assert item["rubrique"] == {"phase": "brief", "section": "L'utilisateur", "nom": "Brief, partie « L'utilisateur »"}
    assert item["origine"] == "humain" and state["elements"][0]["rubrique_nom"] == item["rubrique"]["nom"]
