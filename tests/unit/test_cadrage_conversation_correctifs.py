"""Correctifs de la contre-vérification de la PR #1479 (N1 à N4) et pannes sur le chemin de correction d'un élément confirmé."""

from __future__ import annotations

import json
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
from grimoire.core.cadrage_conversation_engine import ElementExtrait, Garde, MoteurSansModele
from grimoire.core.cadrage_conversation_flow import MAX_GROUPE
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


# ── N1 : un « oui » ne confirme que ce que le message montre ───────────────────────────────────────────


def test_un_oui_n_ecrit_jamais_un_texte_absent_du_message_precedent(root: Path) -> None:
    cinq = [f"Idée numéro {i}" for i in range(1, 6)]
    ctx = make_ctx(root, Dit(*cinq))
    rev = turn.demarrer(ctx)["rev"]
    react = turn.recevoir(ctx, rev, "\n".join(cinq))
    rev = react["rev"]
    assert len(cinq) > MAX_GROUPE and all(i["origine"] == "humain" and not i["individuel"] for i in react["a_confirmer"])
    for _ in range(4):
        if react["etape"]["kind"] != "confirmer":
            break
        vus = [i["texte"] for i in react["a_confirmer"]]
        assert 1 <= len(vus) <= MAX_GROUPE and all(t in react["message"] for t in vus)  # tout le groupe est dans le message
        avant = set(lignes(root))
        react = turn.recevoir(ctx, rev, "oui")
        rev = react["rev"]
        nouvelles = [line for line in lignes(root) if line not in avant]
        assert len(nouvelles) == len(vus) and all(any(t in line for line in nouvelles) for t in vus)
        assert not [e for e in _conv(root).ranges() if e.texte not in vus and e.texte in nouvelles]
    assert len(lignes(root)) == 5  # le reste a été proposé aux tours suivants, un oui chacun


def test_le_groupe_est_aussi_borne_par_la_longueur_du_texte_montre(root: Path) -> None:
    longs = [f"Réponse {i} " + "x" * 280 for i in range(5)]
    ctx = make_ctx(root, Dit(*longs))
    rev = turn.demarrer(ctx)["rev"]
    react = turn.recevoir(ctx, rev, "\n".join(longs))
    assert len(react["a_confirmer"]) == 2  # 3 x 290 caractères dépasseraient ce que le message montre en entier
    assert all(i["texte"] in react["message"] for i in react["a_confirmer"])
    assert all(i["texte"] in _conv(root).tours[-1].texte for i in react["a_confirmer"])  # et le message enregistré les porte


def test_le_script_d_essai_affiche_la_liste_exacte_a_confirmer(root: Path) -> None:
    import importlib.util
    import io

    script = Path(__file__).resolve().parents[2] / "scripts" / "cadrage-converse.py"
    spec = importlib.util.spec_from_file_location("cadrage_converse_n1", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out = io.StringIO()
    assert module.main(["--chantier", "un", "--root", str(root)], io.StringIO("Les mainteneurs\n"), out) == 0
    text = out.getvalue()
    assert "« Les mainteneurs » (noter dans le document : Brief, partie « L'utilisateur » ; origine : humain)" in text


# ── N2 : « mots de l'humain » = le tour ou une proposition ENTIÈRE ─────────────────────────────────────


@pytest.mark.parametrize(
    ("element", "tour"),
    [
        ("export CSV", "je ne veux pas d'export CSV"),
        ("ni les invités", "Les mainteneurs, ni les invités"),
        ("sauf les bots", "Tous les comptes sauf les bots"),
        ("Les mainteneurs", "Les mainteneurs, mais je ne veux pas d'export CSV"),
        ("les comptes", "Tous les comptes"),
        ("une phrase coupée…", "Une phrase coupée sauf pour les bots"),
        ("une phrase coupée…", "Une phrase coupée, pas pour les bots"),
    ],
)
def test_un_fragment_sorti_de_son_contexte_n_est_pas_les_mots_de_l_humain(element: str, tour: str) -> None:
    assert not mots_de_l_humain(element, tour)


@pytest.mark.parametrize(
    ("element", "tour"),
    [
        ("Les mainteneurs du kit", "Les mainteneurs du kit"),
        ("les mainteneurs du kit", "Les Mainteneurs, du kit !"),
        ("Les relecteurs", "Les mainteneurs. Les relecteurs"),
        ("Les mainteneurs", "Les mainteneurs ; les relecteurs\nles testeurs"),
        ("je ne veux pas d'export CSV", "Les mainteneurs. Je ne veux pas d'export CSV !"),
        ("une phrase coupée…", "Une phrase coupée au bout de mille mots"),
    ],
)
def test_le_tour_entier_ou_une_proposition_entiere_est_les_mots_de_l_humain(element: str, tour: str) -> None:
    assert mots_de_l_humain(element, tour)


def test_un_fragment_extrait_par_le_moteur_se_confirme_seul_avec_origine_moteur(root: Path) -> None:
    ctx = make_ctx(root, Dit("export CSV", "ni les invités", "sauf les bots"))
    rev = turn.demarrer(ctx)["rev"]
    react = turn.recevoir(ctx, rev, "Je ne veux pas d'export CSV, ni les invités, sauf les bots")
    assert {(e["origine"], e["individuel"]) for e in turn.vue(ctx)["elements"]} == {("moteur", True)}
    assert len(react["a_confirmer"]) == 1  # jamais un oui de groupe : un seul élément à la fois, texte exact montré
    assert "export CSV" in react["a_confirmer"][0]["texte"] and "reformulé par le moteur" in react["message"]


def test_une_reponse_simple_reprise_en_entier_reste_confirmable_par_le_oui_de_groupe(root: Path) -> None:
    ctx = make_ctx(root)
    rev = turn.demarrer(ctx)["rev"]
    react = turn.recevoir(ctx, rev, "Les mainteneurs du kit")
    assert react["a_confirmer"][0]["origine"] == "humain" and react["a_confirmer"][0]["individuel"] is False
    assert turn.recevoir(ctx, react["rev"], "oui")["document_modifie"] is True


# ── N3 : une question n'est jamais un oui ──────────────────────────────────────────────────────────────


def test_une_question_posee_a_la_place_d_un_oui_n_ecrit_rien(root: Path) -> None:
    ctx = make_ctx(root)
    rev = turn.demarrer(ctx)["rev"]
    rev = turn.recevoir(ctx, rev, "Les mainteneurs")["rev"]
    for texte in ("d'accord ?", "oui ?", "c'est bon ?", "oui？", "¿oui"):
        before = docs(root)
        react = turn.recevoir(ctx, rev, texte)
        rev = react["rev"]
        assert react["document_modifie"] is False and docs(root) == before
        assert not any(e.ecrit for e in _conv(root).elements if e.texte == "Les mainteneurs")
    assert turn.recevoir(ctx, rev, "oui")["document_modifie"] is True or "Les mainteneurs" in docs(root)


# ── N4 : un état forgé ne lève pas ─────────────────────────────────────────────────────────────────────


def _forger(root: Path, **champs: Any) -> None:
    chemin = store.store_path(root / "_grimoire/cadrage/un")
    data = json.loads(chemin.read_text(encoding="utf-8"))
    data["question"].update(champs)
    chemin.write_text(json.dumps(data), encoding="utf-8")


def test_une_question_confirmer_sans_element_en_attente_ne_leve_pas_et_n_ecrit_rien(root: Path) -> None:
    ctx = make_ctx(root)
    rev = turn.demarrer(ctx)["rev"]
    _forger(root, kind="confirmer")
    before = docs(root)
    react = turn.recevoir(ctx, rev, "oui")
    assert docs(root) == before and react["document_modifie"] is False
    assert react["etape"]["kind"] == "question" and react["question"]["kind"] == "amorce"  # recalculée par la machine à états
    assert not _conv(root).elements


@pytest.mark.parametrize("texte", ["sans objet", "je ne sais pas", "d'accord", "non", "En fait, plutôt les relecteurs"])
def test_une_question_forgee_sans_angle_ou_sans_groupe_ne_leve_jamais(root: Path, texte: str) -> None:
    ctx = make_ctx(root)
    rev = turn.demarrer(ctx)["rev"]
    _forger(root, angle="", phase="", section="")
    turn.recevoir(ctx, rev, texte)  # pas d'exception : au pire, le programme dit qu'il n'a rien retenu
    assert not lignes(root)


# ── pannes sur le chemin de correction d'un élément déjà confirmé ──────────────────────────────────────


def _etat_apres_contradiction(root: Path) -> tuple[turn.Contexte, int]:
    ctx = make_ctx(root)
    rev = turn.demarrer(ctx)["rev"]
    rev = turn.recevoir(ctx, rev, "Les mainteneurs")["rev"]
    rev = turn.recevoir(ctx, rev, "oui")["rev"]
    react = turn.recevoir(ctx, rev, "En fait, plutôt les relecteurs")  # la correction : un élément qui en remplace un autre
    assert react["etape"]["kind"] == "confirmer" and "Les mainteneurs" in docs(root)
    return ctx, react["rev"]


def _tout_confirmer(ctx: turn.Contexte, root: Path, rev: int) -> None:
    for _ in range(6):
        if not _conv(root).en_attente():
            return
        rev = turn.recevoir(ctx, rev, "oui")["rev"]
    raise AssertionError("des éléments restent en attente")


def _rien_de_perdu(root: Path, textes: list[str]) -> None:
    """Chaque ligne que l'humain avait confirmée est dans le document OU attend de nouveau son accord ; jamais un trou."""
    conv = _conv(root)
    for texte in textes:
        assert texte in docs(root) or any(e.texte == texte and e.statut == "propose" for e in conv.elements), texte


def test_une_panne_entre_le_retrait_de_l_ancien_et_l_ecriture_du_nouveau_ne_perd_rien(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, rev = _etat_apres_contradiction(root)
    reel = rangement._write_one

    def casse(base: Path, name: Any, conv: Conversation, el: Any) -> None:
        if Path(base) == root:
            raise RuntimeError("disque plein")
        reel(base, name, conv, el)

    monkeypatch.setattr(rangement, "_write_one", casse)
    with pytest.raises(RuntimeError):
        turn.recevoir(ctx, rev, "oui")
    monkeypatch.setattr(rangement, "_write_one", reel)
    assert "Les mainteneurs" not in docs(root)  # l'ancien a bien été retiré : c'est la fenêtre de la panne
    back = turn.demarrer(ctx)  # la reprise
    assert back["alertes"]
    _rien_de_perdu(root, ["Les mainteneurs"])
    _tout_confirmer(ctx, root, _conv(root).rev)
    final = docs(root)
    assert final.count("les relecteurs") == 1 and "Les mainteneurs" not in final  # ni doublon, ni ancien fantôme
    assert all(e.statut in ("confirme", "corrige", "ecarte") for e in _conv(root).elements)
    assert all(e.ecrit for e in _conv(root).ranges())  # chaque élément rangé l'est vraiment
    assert not [e for e in _conv(root).elements if e.intention]


def test_une_panne_pendant_la_correction_d_un_element_suivi_d_un_autre_ne_perd_aucune_ligne(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = make_ctx(root, Dit("Premier avis", "Second avis"))
    rev = turn.demarrer(ctx)["rev"]
    rev = turn.recevoir(ctx, rev, "Premier avis\nSecond avis")["rev"]
    rev = turn.recevoir(ctx, rev, "oui")["rev"]
    assert [e.ecrit for e in _conv(root).elements] == [True, True]
    premier = _conv(root).elements[0].id
    reel = rangement._write_one
    appels: list[int] = []

    def casse(base: Path, name: Any, conv: Conversation, el: Any) -> None:
        if Path(base) == root:
            appels.append(1)
            if len(appels) == 2:  # le premier est réécrit, la panne frappe celui qui le suivait
                raise RuntimeError("disque plein")
        reel(base, name, conv, el)

    monkeypatch.setattr(rangement, "_write_one", casse)
    with pytest.raises(RuntimeError):
        turn.decider(ctx, rev, premier, "corriger", "Premier avis corrigé")
    monkeypatch.setattr(rangement, "_write_one", reel)
    turn.demarrer(ctx)
    conv = _conv(root)
    for texte in ("Premier avis", "Second avis"):  # le texte d'origine (la correction n'a pas été enregistrée) ou sa reconfirmation
        assert texte in docs(root) or any(e.texte == texte and e.statut == "propose" for e in conv.elements), texte
    assert all(e.ecrit for e in conv.ranges()) and not [e for e in conv.elements if e.intention]
    _tout_confirmer(ctx, root, conv.rev)
    assert len(lignes(root)) == 2 and docs(root).count("Second avis") == 1 and docs(root).count("Premier avis") == 1, lignes(root)


def test_une_panne_entre_la_fin_de_l_ecriture_et_l_enregistrement_final_est_terminee_a_la_reprise(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, rev = _etat_apres_contradiction(root)
    reel = store.save
    appels: list[int] = []

    def casse(directory: Path, conv: Conversation) -> Any:
        if Path(directory) == root / "_grimoire/cadrage/un":
            appels.append(1)
            if len(appels) == 2:  # 1 : le journal d'intention, 2 : l'enregistrement final, après l'écriture du document
                raise RuntimeError("coupure")
        return reel(directory, conv)

    monkeypatch.setattr(store, "save", casse)
    with pytest.raises(RuntimeError):
        turn.recevoir(ctx, rev, "oui")
    monkeypatch.setattr(store, "save", reel)
    assert "les relecteurs" in docs(root) and "Les mainteneurs" not in docs(root)  # le document est fait
    back = turn.demarrer(ctx)
    assert back["rev"] >= rev
    conv = _conv(root)
    assert docs(root).count("les relecteurs") == 1 and not [e for e in conv.elements if e.intention]
    assert not conv.en_attente() and [e.statut for e in conv.elements] == ["ecarte", "confirme"]
    assert all(e.ecrit for e in conv.ranges())
