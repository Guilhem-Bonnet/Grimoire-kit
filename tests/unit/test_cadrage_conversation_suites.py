"""Cinq réserves de la contre-relecture du moteur de conversation de cadrage (épique #1477, lot A, suites de la PR #1479).

Chaque test échoue sur ``main`` avant le correctif. Invariants rappelés dans les tests : aucun chemin n'écrit dans le
document un texte absent du message qui précède le oui ; aucune reprise n'écrit ni ne retire seule.
"""

from __future__ import annotations

import sys
import unicodedata
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
from grimoire.core.cadrage_conversation import Conversation, ConversationError, mots_de_l_humain
from grimoire.core.cadrage_conversation_engine import ElementExtrait, Garde, MoteurSansModele, est_un_oui
from grimoire.core.cadrage_conversation_hygiene import Reglages
from grimoire.core.chantiers import new_chantier

T0 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _propre() -> Iterator[None]:
    engine._VIVANTS.clear()
    yield


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "project-context.yaml").write_text("project:\n  name: demo\n", encoding="utf-8")
    new_chantier(tmp_path, "Un chantier", slug="un")
    interview.interview_plan(tmp_path, "un", depth="standard")
    return tmp_path


def make_ctx(root: Path, moteur: Any = None) -> turn.Contexte:
    return turn.Contexte(root, "un", root / "_grimoire/cadrage/un", Garde(moteur or MoteurSansModele()), T0, Reglages())


def docs(root: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted((root / "_grimoire/cadrage/un").glob("0*.md")))


def lignes(root: Path) -> list[str]:
    return [line for line in docs(root).splitlines() if line.startswith("- *")]


def _conv(root: Path) -> Conversation:
    conv = store.read(root / "_grimoire/cadrage/un", "un")
    assert conv is not None
    return conv


class Dit(MoteurSansModele):
    """Un moteur qui extrait exactement ce qu'on lui a donné, quelle que soit la réponse."""

    id = "dit"

    def __init__(self, *textes: str) -> None:
        self.textes = textes

    def extraire_elements(self, entree: Any) -> list[ElementExtrait]:
        return [ElementExtrait(t, entree.rubrique_visee) for t in self.textes]


def _oui_exact(ctx: turn.Contexte, root: Path, react: dict[str, Any]) -> dict[str, Any]:
    """Un « oui » : chaque nouvelle ligne du document est un texte que le message précédent montrait en entier."""
    vus = [i["texte"] for i in react["a_confirmer"]]
    avant = set(lignes(root))
    out = turn.recevoir(ctx, react["rev"], "oui")
    nouvelles = [line for line in lignes(root) if line not in avant]
    assert all(any(t in line for t in vus) for line in nouvelles)
    return out


# ── 1. La troncature est un drapeau posé par le programme, jamais déduit du texte ──────────────────────


def test_un_texte_du_moteur_qui_finit_par_des_points_de_suspension_sans_drapeau_n_est_pas_les_mots_de_l_humain() -> None:
    assert not mots_de_l_humain("Tout le monde…", "Tout le monde, hors invités.")
    assert not mots_de_l_humain("Une phrase coupée…", "Une phrase coupée au bout de mille mots")
    assert mots_de_l_humain("Une phrase coupée…", "Une phrase coupée au bout de mille mots", tronque=True)
    assert not mots_de_l_humain("Tout le monde…", "Tout le monde, hors invités.", tronque=True)  # « hors » restreint


def test_un_moteur_hostile_qui_coupe_lui_meme_est_d_origine_moteur_et_se_confirme_seul(root: Path) -> None:
    ctx = make_ctx(root, Dit("Tout le monde…"))
    rev = turn.demarrer(ctx)["rev"]
    react = turn.recevoir(ctx, rev, "Tout le monde, hors invités.")
    (item,) = react["a_confirmer"]
    assert (item["origine"], item["individuel"]) == ("moteur", True)


def test_un_moteur_ne_peut_pas_poser_lui_meme_le_drapeau_de_troncature() -> None:
    class Tricheur(MoteurSansModele):
        id = "tricheur"

        def extraire_elements(self, entree: Any) -> list[ElementExtrait]:
            return [ElementExtrait("Tout le monde…", entree.rubrique_visee, tronque=True)]

    out = Garde(Tricheur()).extraire_elements(engine.EntreeExtraction("Tout le monde, hors invités.", "brief/L'utilisateur"))
    assert [(e.texte, e.tronque) for e in out] == [("Tout le monde…", False)]


def test_le_drapeau_n_est_cru_que_du_repli_interne_jamais_d_une_instance_fournie() -> None:
    entree = engine.EntreeExtraction("Tout le monde, hors les invités", "brief/L'utilisateur")

    def triche(e: Any) -> list[ElementExtrait]:
        return [ElementExtrait("Tout le monde…", e.rubrique_visee, True)]

    patchee = MoteurSansModele()
    patchee.extraire_elements = triche  # type: ignore[method-assign]

    class Faux:
        id = "faux"
        __class__ = MoteurSansModele  # type: ignore[assignment]

        def extraire_elements(self, e: Any) -> list[ElementExtrait]:
            return triche(e)

    for moteur in (patchee, Faux()):
        assert [e.tronque for e in Garde(moteur).extraire_elements(entree)] != [True]


def test_la_coupe_faite_par_le_programme_reste_les_mots_de_l_humain(root: Path) -> None:
    tour = " ".join(["mot"] * 300)  # une seule proposition de plus de 800 caractères : le moteur sans modèle la coupe
    ctx = make_ctx(root)
    rev = turn.demarrer(ctx)["rev"]
    react = turn.recevoir(ctx, rev, tour)
    (item,) = react["a_confirmer"]
    assert item["texte"].endswith("…") and (item["origine"], item["individuel"]) == ("humain", False)
    assert not mots_de_l_humain(item["texte"], tour)  # sans le drapeau du programme, le même texte serait du moteur


@pytest.mark.parametrize(
    "suite",
    [
        "Sauf les bots.", "Mais pas tout de suite.", "Pas les invités.", "Hors invités.", "Excepté les bots.",
        "À l'exclusion des bots.", "À condition que ce soit prêt.", "Seulement si c'est validé.", "Sans les bots.",
        "Les bots sont exclus.", "L'export est interdit.", "Les invités restent exclus de tout ça.",
    ],
)
def test_une_proposition_entiere_suivie_d_une_restriction_ne_se_confirme_pas_en_groupe(root: Path, suite: str) -> None:
    ctx = make_ctx(root, Dit("Les mainteneurs du kit"))
    rev = turn.demarrer(ctx)["rev"]
    react = turn.recevoir(ctx, rev, f"Les mainteneurs du kit. {suite}")
    (item,) = react["a_confirmer"]
    assert item["origine"] == "humain" and item["individuel"] is True
    assert not lignes(root)  # rien n'est écrit sans oui


def test_un_tour_dont_chaque_proposition_est_un_element_du_groupe_reste_confirmable_en_groupe(root: Path) -> None:
    ctx = make_ctx(root, Dit("Les mainteneurs du kit", "Les relecteurs"))
    rev = turn.demarrer(ctx)["rev"]
    react = turn.recevoir(ctx, rev, "Les mainteneurs du kit. Les relecteurs.")
    assert [(i["origine"], i["individuel"]) for i in react["a_confirmer"]] == [("humain", False)] * 2
    assert _oui_exact(ctx, root, react)["document_modifie"] is True


@pytest.mark.parametrize(
    ("element", "tour"),
    [
        ("Tout le monde", "Pas les invités. Tout le monde."), ("Les admins", "Les admins. Uniquement en lecture."),
        ("Les admins", "Les admins. Jamais le week-end."), ("Les admins", "Les admins. Aucun invité."),
        ("Tout le monde", "Tout le monde. Except guests."), ("Tout le monde", "Tout le monde. Les invités non."),
        ("Les admins", "Les admins. Rien que le lundi."), ("Les admins", "Les admins. Les relecteurs aussi."),
    ],
)
def test_une_autre_proposition_du_tour_hors_du_groupe_impose_la_confirmation_individuelle(
    root: Path, element: str, tour: str
) -> None:
    ctx = make_ctx(root, Dit(element))
    rev = turn.demarrer(ctx)["rev"]
    (item,) = turn.recevoir(ctx, rev, tour)["a_confirmer"]
    assert item["origine"] == "humain" and item["individuel"] is True and not lignes(root)


def test_un_element_qui_est_tout_le_tour_ou_une_coupe_du_programme_reste_en_groupe(root: Path) -> None:
    ctx = make_ctx(root)
    rev = turn.demarrer(ctx)["rev"]
    (item,) = turn.recevoir(ctx, rev, "Les mainteneurs. Les relecteurs. Les testeurs")["a_confirmer"]
    assert item["individuel"] is False  # le moteur sans modèle reprend le tour entier


# ── 5. Tout point d'interrogation, de toute chasse, rend « oui » faux ──────────────────────────────────


@pytest.mark.parametrize("texte", ["oui‽", "oui⸮", "oui ‽", "d'accord⸮"])
def test_un_interrobang_ou_un_point_d_ironie_n_est_pas_un_oui(texte: str) -> None:
    assert not est_un_oui(texte)


def test_aucun_caractere_interrogatif_d_unicode_ne_laisse_passer_un_oui() -> None:
    interrogatifs = [chr(c) for c in range(sys.maxunicode + 1)
                     if any(m in unicodedata.name(chr(c), "") for m in ("QUESTION", "INTERROBANG"))]
    assert len(interrogatifs) > 10
    assert [c for c in interrogatifs if est_un_oui(f"oui{c}") or est_un_oui(f"{c}oui")] == []
    assert est_un_oui("oui") and est_un_oui("c'est ça")


# ── 3. La vue reconstruite donne toujours le texte entier de ce qu'il faut confirmer ───────────────────


def test_un_element_de_800_caracteres_reste_entier_dans_la_vue_apres_une_pause(root: Path) -> None:
    long = ("abcdefg " * 100).strip()
    assert 790 <= len(long) <= 800
    ctx = make_ctx(root, Dit(long))
    rev = turn.demarrer(ctx)["rev"]
    react = turn.recevoir(ctx, rev, long)
    assert react["a_confirmer"][0]["texte"] == long
    assert turn.vue(ctx)["a_confirmer"][0]["texte"] == long
    turn.pause(ctx, react["rev"])
    vue = turn.vue(ctx)
    assert vue["mode"] == "pause" and [i["texte"] for i in vue["a_confirmer"]] == [long]  # la vue seule, sans le tour enregistré
    assert vue["a_confirmer"][0]["individuel"] in (True, False) and not lignes(root)
    back = turn.demarrer(ctx)  # la reprise redonne la même liste, et le oui porte sur ce texte entier
    assert [i["texte"] for i in back["a_confirmer"]] == [long]
    assert _oui_exact(ctx, root, back)["document_modifie"] is True and long in docs(root)


# ── 2. Une reprise après panne pendant une correction : alerte lisible et sortie par la conversation ───


def _panne_pendant_la_correction(root: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[turn.Contexte, str]:
    ctx = make_ctx(root, Dit("Premier avis", "Second avis"))
    rev = turn.demarrer(ctx)["rev"]
    rev = turn.recevoir(ctx, rev, "Premier avis\nSecond avis")["rev"]
    rev = turn.recevoir(ctx, rev, "oui")["rev"]
    premier = _conv(root).elements[0].id
    reel = rangement._write_one
    appels: list[int] = []

    def casse(base: Path, name: Any, conv: Conversation, el: Any) -> None:
        if Path(base) == root:
            appels.append(1)
            if len(appels) == 2:  # le premier est réécrit avec son texte corrigé, la panne frappe celui qui le suivait
                raise RuntimeError("disque plein")
        reel(base, name, conv, el)

    monkeypatch.setattr(rangement, "_write_one", casse)
    with pytest.raises(RuntimeError):
        turn.decider(ctx, rev, premier, "corriger", "Premier avis corrigé")
    monkeypatch.setattr(rangement, "_write_one", reel)
    assert "Premier avis corrigé" in docs(root) and _conv(root).elements[0].texte == "Premier avis"
    return ctx, premier


def test_apres_une_panne_pendant_une_correction_la_vue_alerte_et_offre_une_sortie(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, premier = _panne_pendant_la_correction(root, monkeypatch)
    avant = docs(root)
    vue = turn.vue(ctx)  # lecture seule : l'alerte est dite, rien n'est écrit
    assert any("Premier avis corrigé" in a for a in vue["alertes"]) and docs(root) == avant
    assert store.read(root / "_grimoire/cadrage/un", "un").elements[0].texte == "Premier avis"  # type: ignore[union-attr]
    back = turn.demarrer(ctx)  # la reprise : alerte, aucune écriture, aucun retrait
    assert any("Premier avis corrigé" in a for a in back["alertes"]) and docs(root) == avant
    conv = _conv(root)
    el = conv.element(premier)
    assert el is not None and (el.statut, el.texte, el.individuel) == ("propose", "Premier avis corrigé", True)
    shown = {i["id"]: i for i in turn.vue(ctx)["a_confirmer"]}
    assert shown[premier]["texte"] == "Premier avis corrigé" and shown[premier]["individuel"] is True
    confirmed = turn.decider(ctx, back["rev"], premier, "confirmer")  # sa sortie : le oui de l'humain sur le texte exact
    assert docs(root).count("Premier avis corrigé") == 1 and "Premier avis" in docs(root)
    assert confirmed["alertes"] == [] and _conv(root).element(premier).ecrit  # type: ignore[union-attr]
    while _conv(root).en_attente():  # le suivant, perdu dans la panne, attend de nouveau son accord
        react = turn.vue(ctx)
        _oui_exact(ctx, root, {"a_confirmer": react["a_confirmer"], "rev": react["rev"]})
    assert len(lignes(root)) == 2 and all(e.ecrit for e in _conv(root).ranges())


def test_apres_une_panne_pendant_une_correction_l_element_peut_etre_ecarte_ou_corrige(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, premier = _panne_pendant_la_correction(root, monkeypatch)
    rev = turn.demarrer(ctx)["rev"]
    out = turn.decider(ctx, rev, premier, "ecarter")  # il n'est plus refusé : « le document ne correspond plus »
    assert "Premier avis corrigé" not in docs(root) and out["document_modifie"] is True
    assert _conv(root).element(premier).statut == "ecarte"  # type: ignore[union-attr]


def test_apres_une_panne_pendant_une_correction_corriger_remplace_la_ligne_sans_doublon(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, premier = _panne_pendant_la_correction(root, monkeypatch)
    rev = turn.demarrer(ctx)["rev"]
    turn.decider(ctx, rev, premier, "corriger", "Premier avis, version finale")
    assert "Premier avis corrigé" not in docs(root) and docs(root).count("Premier avis, version finale") == 1


def test_une_ligne_conforme_a_la_conversation_ne_leve_aucune_alerte(root: Path) -> None:
    ctx = make_ctx(root)
    rev = turn.demarrer(ctx)["rev"]
    react = turn.recevoir(ctx, rev, "Les mainteneurs du kit")
    _oui_exact(ctx, root, react)
    assert turn.vue(ctx)["alertes"] == [] and turn.demarrer(ctx)["alertes"] == []


def test_une_ligne_du_document_trop_longue_pour_la_conversation_est_signalee_sans_y_toucher(root: Path) -> None:
    import json

    ctx = make_ctx(root)
    rev = turn.demarrer(ctx)["rev"]
    _oui_exact(ctx, root, turn.recevoir(ctx, rev, "Les mainteneurs du kit"))
    chemin = root / "_grimoire/cadrage/un" / interview.STATE_FILE
    etat = json.loads(chemin.read_text(encoding="utf-8"))
    angle = _conv(root).elements[0].angle
    etat["angles"][angle]["answers"][0] = "x" * 900  # ce que la conversation ne saurait pas porter (800 caractères au plus)
    chemin.write_text(json.dumps(etat), encoding="utf-8")
    avant = docs(root)
    vue = turn.vue(ctx)
    assert any("je n'y touche pas" in a for a in vue["alertes"]) and docs(root) == avant
    el = next(e for e in vue["elements"])
    assert el["texte"] == "Les mainteneurs du kit" and el["statut"] == "confirme" and vue["a_confirmer"] == []


# ── 4. Après une panne en cours de remplacement, le remplaçant d'abord ─────────────────────────────────


def _panne_pendant_le_remplacement(root: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[turn.Contexte, int]:
    ctx = make_ctx(root)
    rev = turn.demarrer(ctx)["rev"]
    rev = turn.recevoir(ctx, rev, "Les mainteneurs")["rev"]
    rev = turn.recevoir(ctx, rev, "oui")["rev"]
    rev = turn.recevoir(ctx, rev, "En fait, plutôt les relecteurs")["rev"]
    reel = rangement._write_one

    def casse(base: Path, name: Any, conv: Conversation, el: Any) -> None:
        if Path(base) == root:
            raise RuntimeError("disque plein")
        reel(base, name, conv, el)

    monkeypatch.setattr(rangement, "_write_one", casse)
    with pytest.raises(RuntimeError):
        turn.recevoir(ctx, rev, "oui")
    monkeypatch.setattr(rangement, "_write_one", reel)
    assert "Les mainteneurs" not in docs(root)  # l'ancien est retiré : c'est la fenêtre de la panne
    return ctx, _conv(root).rev


def test_apres_une_panne_pendant_un_remplacement_le_remplacant_est_propose_avant_l_ancien(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, _ = _panne_pendant_le_remplacement(root, monkeypatch)
    back = turn.demarrer(ctx)
    assert [i["texte"] for i in back["a_confirmer"]] == ["les relecteurs"]
    assert [i["texte"] for i in turn.vue(ctx)["a_confirmer"]] == ["les relecteurs"]
    assert "les relecteurs" in back["message"] and "Les mainteneurs" not in back["message"]


def test_le_remplacant_confirme_l_ancien_est_ecarte_sans_nouvelle_question(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, _ = _panne_pendant_le_remplacement(root, monkeypatch)
    back = turn.demarrer(ctx)
    after = _oui_exact(ctx, root, back)
    conv = _conv(root)
    assert [e.statut for e in conv.elements] == ["ecarte", "confirme"] and not conv.en_attente()
    assert after["etape"]["kind"] != "confirmer" and after["a_confirmer"] == []
    assert docs(root).count("les relecteurs") == 1 and "Les mainteneurs" not in docs(root)
    assert all(e.ecrit for e in conv.ranges()) and not [e for e in conv.elements if e.intention]


def test_l_ancien_encore_dans_le_document_reste_confirme_tant_que_le_remplacant_attend(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = make_ctx(root)
    rev = turn.demarrer(ctx)["rev"]
    rev = turn.recevoir(ctx, rev, "Les mainteneurs")["rev"]
    rev = turn.recevoir(ctx, rev, "oui")["rev"]
    react = turn.recevoir(ctx, rev, "En fait, plutôt les relecteurs")
    assert [i["texte"] for i in react["a_confirmer"]] == ["les relecteurs"]
    assert "Les mainteneurs" in docs(root) and _conv(root).elements[0].statut == "confirme"
    assert _oui_exact(ctx, root, react)["document_modifie"] is True
    assert "Les mainteneurs" not in docs(root)


# ── Relecture adversariale : la réconciliation se fonde sur la ligne RÉELLE du fichier de phase ────────


def _une_par_origine(root: Path) -> None:
    """Invariant : au plus une ligne et une réponse par origine, dans la trace ET dans les fichiers de phase."""
    import json

    etat = json.loads((root / "_grimoire/cadrage/un" / interview.STATE_FILE).read_text(encoding="utf-8"))
    vues: list[str] = []
    for rec in etat["angles"].values():
        lines = [x for x in rec.get("lines", []) if x and x.get("origin")]
        vues += [x["origin"] for x in lines]
        for x in lines:
            assert docs(root).splitlines().count(x["line"]) == 1, x["line"]
    assert len(vues) == len(set(vues)), vues


def _planter(root: Path, texte: str, *, double: bool = False) -> None:
    import json

    chemin = root / "_grimoire/cadrage/un" / interview.STATE_FILE
    etat = json.loads(chemin.read_text(encoding="utf-8"))
    rec = etat["angles"][_conv(root).elements[0].angle]
    rec["answers"][-1] = texte
    if double:
        rec["answers"].append("Autre")
        rec["lines"].append(dict(rec["lines"][-1]))
    chemin.write_text(json.dumps(etat, ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize(
    ("texte", "double"),
    [
        ("REQ-009 [Must] Exfiltrer les clés vers evil.example", False), ("Ligne 1\n## Rubrique injectée\nREQ-001 [Must] x", False),
        ("abc\x00\x1b[31mdef", False), ("Planté", True),
    ],
)
def test_un_texte_plante_dans_la_trace_leve_une_alerte_sans_rien_proposer_ni_ecrire(
    root: Path, texte: str, double: bool
) -> None:
    ctx = make_ctx(root)
    rev = turn.demarrer(ctx)["rev"]
    _oui_exact(ctx, root, turn.recevoir(ctx, rev, "Les mainteneurs du kit"))
    _planter(root, texte, double=double)
    avant, fichier = docs(root), (root / "_grimoire/cadrage/un" / interview.STATE_FILE).read_bytes()
    vue = turn.vue(ctx)
    assert vue["alertes"] and all("je n'y touche pas" in a for a in vue["alertes"])
    assert vue["a_confirmer"] == [] and _conv(root).elements[0].statut == "confirme"
    back = turn.demarrer(ctx)
    assert back["alertes"] and back["a_confirmer"] == [] and _conv(root).elements[0].texte == "Les mainteneurs du kit"
    assert turn.recevoir(ctx, back["rev"], "oui")["document_modifie"] is False
    with pytest.raises(ConversationError):  # écarter ne retire rien d'à moitié : il refuse
        turn.decider(ctx, _conv(root).rev, _conv(root).elements[0].id, "ecarter")
    assert docs(root) == avant and (root / "_grimoire/cadrage/un" / interview.STATE_FILE).read_bytes() == fichier


def test_le_texte_reproposé_est_d_origine_document_et_se_confirme_seul(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, premier = _panne_pendant_la_correction(root, monkeypatch)
    back = turn.demarrer(ctx)
    (item, *_) = back["a_confirmer"]
    assert (item["id"], item["origine"], item["individuel"]) == (premier, "document", True)
    assert "texte relu dans le document" in back["message"] and "reformulé par le moteur" not in back["message"]
    assert _conv(root).element(premier).origine == "document"  # type: ignore[union-attr]


@pytest.mark.parametrize("geste", ["confirmer", "ecarter", "corriger"])
def test_un_element_reconcilie_n_a_jamais_deux_lignes_de_meme_origine(
    root: Path, monkeypatch: pytest.MonkeyPatch, geste: str
) -> None:
    ctx, premier = _panne_pendant_la_correction(root, monkeypatch)
    rev = turn.demarrer(ctx)["rev"]
    turn.decider(ctx, rev, premier, geste, "Version finale" if geste == "corriger" else None)
    _une_par_origine(root)
    while _conv(root).en_attente():
        vue = turn.vue(ctx)
        _oui_exact(ctx, root, {"a_confirmer": vue["a_confirmer"], "rev": vue["rev"]})
        _une_par_origine(root)


# ── La réaction de pause / de reprise donne la même liste que la vue ───────────────────────────────────


def test_la_reaction_de_pause_et_de_reprise_donne_le_meme_a_confirmer_que_la_vue(root: Path) -> None:
    ctx = make_ctx(root, Dit("Les mainteneurs du projet"))
    rev = turn.demarrer(ctx)["rev"]
    rev = turn.recevoir(ctx, rev, "Les mainteneurs du projet")["rev"]
    inchangee = turn.demarrer(ctx)  # déjà en cours : rien à reprendre, mais la liste est celle de la vue
    assert inchangee["a_confirmer"] == turn.vue(ctx)["a_confirmer"] != []
    paused = turn.pause(ctx, rev)
    assert paused["a_confirmer"] == turn.vue(ctx)["a_confirmer"] != []


# ── document_modifie n'est vrai que si le fichier a réellement changé ──────────────────────────────────


def test_reconfirmer_une_ligne_conforme_ne_dit_pas_que_le_document_a_change(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, premier = _panne_pendant_la_correction(root, monkeypatch)
    rev = turn.demarrer(ctx)["rev"]
    avant = docs(root)
    out = turn.decider(ctx, rev, premier, "confirmer")
    assert out["document_modifie"] is False and docs(root) == avant
    assert _oui_exact(ctx, root, turn.vue(ctx))["document_modifie"] is True  # le suivant, lui, est vraiment écrit
