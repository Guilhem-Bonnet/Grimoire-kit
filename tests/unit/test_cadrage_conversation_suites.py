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
from grimoire.core.cadrage_conversation import Conversation, mots_de_l_humain
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


def test_une_proposition_suivie_d_une_autre_sans_restriction_reste_confirmable_en_groupe(root: Path) -> None:
    ctx = make_ctx(root, Dit("Les mainteneurs du kit"))
    rev = turn.demarrer(ctx)["rev"]
    react = turn.recevoir(ctx, rev, "Les mainteneurs du kit. Les relecteurs aussi.")
    assert [(i["origine"], i["individuel"]) for i in react["a_confirmer"]] == [("humain", False)]
    assert _oui_exact(ctx, root, react)["document_modifie"] is True


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
