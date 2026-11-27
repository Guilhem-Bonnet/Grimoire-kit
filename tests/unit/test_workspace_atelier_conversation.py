"""Les routes de la conversation de cadrage : mêmes gardes que les écritures voisines de l'Atelier, aucun contenu de document accepté."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from grimoire.core import cadrage_conversation_hygiene as hygiene
from grimoire.core import cadrage_conversation_store as store
from grimoire.core import cadrage_interview as interview
from grimoire.core.cadrage_conversation_engine import ElementExtrait, MoteurSansModele
from grimoire.core.chantiers import new_chantier
from grimoire.tools import workspace_atelier as wa
from grimoire.tools import workspace_atelier_conversation as conv_routes
from grimoire.tools import workspace_atelier_loop as loop

A = "/api/workspace/atelier/"


@pytest.fixture(autouse=True)
def _reset_engine() -> Iterator[None]:
    conv_routes.set_moteur(None)
    yield
    conv_routes.set_moteur(None)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "project-context.yaml").write_text("project:\n  name: demo\n", encoding="utf-8")
    wa.set_atelier(tmp_path, True)
    new_chantier(tmp_path, "Un chantier", slug="un")
    interview.interview_plan(tmp_path, "un", depth="standard")
    return tmp_path


def post(root: Path, path: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    return wa.post_http(root, A + path, body or {})


def ok(root: Path, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    code, out = post(root, path, body)
    assert code == 200, out
    return out


def get(root: Path, path: str) -> Any:
    return wa.get(root, A + path, {})


def _doc(root: Path) -> dict[str, str]:
    folder = root / "_grimoire/cadrage/un"
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(folder.glob("0*.md"))}


def test_le_parcours_complet_par_les_routes(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    touched: list[str] = []
    monkeypatch.setattr(loop, "after_write", lambda root, slug: touched.append(slug))
    assert get(project, "chantiers/un/conversation") == {"existe": False, "moteur": get(project, "chantiers/un/conversation")["moteur"]}
    started = ok(project, "chantiers/un/conversation/demarrer")
    assert started["reaction"]["cree"] is True and started["conversation"]["existe"] is True
    rev = started["conversation"]["rev"]
    turned = ok(project, "chantiers/un/conversation/tour", {"rev": rev, "texte": "Les mainteneurs du kit"})
    assert set(turned["reaction"]) >= {"accuse", "message", "etape", "lourd", "moteur", "rev", "question"}
    assert turned["reaction"]["lourd"] is False and turned["reaction"]["etape"]["kind"] == "confirmer"
    assert touched == [] and "mainteneurs" not in _doc(project)["01-brief.md"]
    element = turned["conversation"]["elements"][0]
    decided = ok(project, f"chantiers/un/conversation/elements/{element['id']}",
                 {"rev": turned["reaction"]["rev"], "decision": "confirmer"})
    assert "Les mainteneurs du kit" in _doc(project)["01-brief.md"] and touched == ["un"]
    assert decided["conversation"]["elements"][0]["statut"] == "confirme"
    seen = get(project, "chantiers/un/conversation/compris")
    assert seen["rubriques"][0]["elements"][0]["texte"] == "Les mainteneurs du kit"
    fixed = ok(project, f"chantiers/un/conversation/elements/{element['id']}",
               {"rev": decided["reaction"]["rev"], "decision": "corriger", "texte": "Les mainteneurs ET les relecteurs"})
    assert "ET les relecteurs" in _doc(project)["01-brief.md"] and touched == ["un", "un"]
    paused = ok(project, "chantiers/un/conversation/pause", {"rev": fixed["reaction"]["rev"]})
    assert paused["conversation"]["mode"] == "pause"
    resumed = ok(project, "chantiers/un/conversation/demarrer")
    assert resumed["reaction"]["resume"].count("\n") == 1 and resumed["conversation"]["mode"] == "cadrage"
    ended = ok(project, "chantiers/un/conversation/terminer", {"rev": resumed["conversation"]["rev"]})
    assert ended["conversation"]["mode"] == "termine"
    removed = ok(project, f"chantiers/un/conversation/elements/{element['id']}", {"rev": ended["reaction"]["rev"], "decision": "ecarter"})
    assert "mainteneurs" not in _doc(project)["01-brief.md"] and removed["conversation"]["elements"][0]["statut"] == "ecarte"


def test_le_drapeau_eteint_ferme_toutes_les_routes(project: Path) -> None:
    ok(project, "chantiers/un/conversation/demarrer")
    wa.set_atelier(project, False)
    saved = (project / "_grimoire/cadrage/un" / store.FILENAME).read_bytes()
    for path in ("chantiers/un/conversation/demarrer", "chantiers/un/conversation/tour", "chantiers/un/conversation/pause",
                 "chantiers/un/conversation/elements/e1"):
        assert post(project, path, {"rev": 1, "texte": "x", "decision": "confirmer"})[0] == 404
    for path in ("chantiers/un/conversation", "chantiers/un/conversation/compris", "conversations"):
        with pytest.raises(FileNotFoundError):
            get(project, path)
    assert (project / "_grimoire/cadrage/un" / store.FILENAME).read_bytes() == saved


@pytest.mark.parametrize(
    "body",
    [
        {"rev": 1, "texte": "x", "phase": "01-brief.md"},
        {"rev": 1, "texte": "x", "section": "L'utilisateur"},
        {"rev": 1, "texte": "x", "contenu": "## Pirate"},
        {"rev": 1, "texte": "x", "chemin": "../../etc/passwd"},
        {"rev": 1, "texte": "x", "slug": "autre"},
    ],
)
def test_aucune_route_n_accepte_du_contenu_de_document(project: Path, body: dict[str, Any]) -> None:
    started = ok(project, "chantiers/un/conversation/demarrer")
    before, saved = _doc(project), (project / "_grimoire/cadrage/un" / store.FILENAME).read_bytes()
    code, out = post(project, "chantiers/un/conversation/tour", {**body, "rev": started["conversation"]["rev"]})
    assert code == 400 and "refusé" in out["error"]
    assert post(project, "chantiers/un/conversation/demarrer", {"texte": "x"})[0] == 400
    assert _doc(project) == before and (project / "_grimoire/cadrage/un" / store.FILENAME).read_bytes() == saved


@pytest.mark.parametrize("rev", [None, "1", True, -1, 1.5, [1]])
def test_la_version_vue_est_obligatoire(project: Path, rev: Any) -> None:
    ok(project, "chantiers/un/conversation/demarrer")
    for action in ("tour", "pause", "terminer", "classer", "rouvrir"):
        body = {"texte": "x"} if action == "tour" else {}
        if rev is not None:
            body["rev"] = rev
        code, out = post(project, f"chantiers/un/conversation/{action}", body)
        assert code == 400 and "rev" in out["error"]


def test_ecrire_sur_une_conversation_qui_a_change_donne_409_avec_sa_raison(project: Path) -> None:
    rev = ok(project, "chantiers/un/conversation/demarrer")["conversation"]["rev"]
    ok(project, "chantiers/un/conversation/tour", {"rev": rev, "texte": "Les mainteneurs"})
    code, out = post(project, "chantiers/un/conversation/tour", {"rev": rev, "texte": "autre onglet"})
    assert code == 409 and out["conflict"] is True and out["reason"] == "conversation-changee"
    code, out = post(project, "chantiers/un/conversation/pause", {"rev": rev})
    assert code == 409
    current = get(project, "chantiers/un/conversation")["rev"]
    code, out = post(project, "chantiers/un/conversation/elements/e1", {"rev": current, "decision": "confirmer"})
    assert code == 200
    code, out = post(project, "chantiers/un/conversation/elements/e1", {"rev": out["conversation"]["rev"], "decision": "confirmer"})
    assert code == 409 and out["reason"] == "deja-tranche"
    ok(project, "chantiers/un/conversation/pause", {"rev": get(project, "chantiers/un/conversation")["rev"]})
    code, out = post(project, "chantiers/un/conversation/tour", {"rev": get(project, "chantiers/un/conversation")["rev"], "texte": "x"})
    assert code == 409 and out["reason"] == "en-pause"


def test_deux_ecritures_concurrentes_sur_la_meme_version_une_seule_passe(project: Path) -> None:
    rev = ok(project, "chantiers/un/conversation/demarrer")["conversation"]["rev"]
    codes: list[int] = []
    barrier = threading.Barrier(2)

    def go(text: str) -> None:
        barrier.wait()
        codes.append(post(project, "chantiers/un/conversation/tour", {"rev": rev, "texte": text})[0])

    threads = [threading.Thread(target=go, args=(t,)) for t in ("Premier onglet", "Second onglet")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(codes) == [200, 409]
    state = get(project, "chantiers/un/conversation")
    assert state["rev"] == rev + 1 and sum(1 for t in state["tours"] if t["auteur"] == "utilisateur") == 1


def test_un_verrou_qui_ne_vient_pas_donne_503(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.core import cadrage_conversation_turn as turn

    def refuse(directory: Path) -> Any:
        raise TimeoutError("verrou")

    monkeypatch.setattr(turn.store, "locked_store", refuse)
    assert post(project, "chantiers/un/conversation/demarrer")[0] == 503


@pytest.mark.parametrize(
    "path",
    [
        "chantiers/un/conversation",  # POST sans geste
        "chantiers/un/conversation/inconnu",
        "chantiers/un/conversation/demarrer/e1",
        "chantiers/un/conversation/elements",
        "chantiers/un/conversation/elements/e1/x",
        "chantiers/un/conversation/tour/e1",
        "conversations",
    ],
)
def test_une_route_inconnue_est_un_404(project: Path, path: str) -> None:
    assert post(project, path, {"rev": 1})[0] == 404


def test_les_lectures_inconnues_sont_un_404(project: Path) -> None:
    for path in ("chantiers/un/conversation/inconnu", "chantiers/un/conversation/compris/x"):
        with pytest.raises(FileNotFoundError):
            get(project, path)


@pytest.mark.parametrize("slug", ["inconnu", "..", "UN", "un/../un", "a b", ""])
def test_un_chantier_inconnu_ou_hostile_est_un_404_sans_rien_ecrire(project: Path, slug: str) -> None:
    code, _ = wa.post_http(project, f"{A}chantiers/{slug}/conversation/demarrer", {})
    assert code == 404
    with pytest.raises(FileNotFoundError):
        wa.get(project, f"{A}chantiers/{slug}/conversation", {})
    assert not list((project / "_grimoire").rglob(store.FILENAME))


def test_un_dossier_de_chantier_en_lien_symbolique_est_refuse(project: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    outside = tmp_path_factory.mktemp("dehors")
    link = project / "_grimoire/cadrage/lien"
    link.symlink_to(outside)
    code, _ = wa.post_http(project, f"{A}chantiers/lien/conversation/demarrer", {})
    assert code == 404 and list(outside.iterdir()) == []
    assert all(r["chantier"] != "lien" for r in get(project, "conversations")["actives"])


def test_le_moteur_vient_d_un_seul_point_d_injection(project: Path) -> None:
    calls: list[tuple[Path, str]] = []

    class Mien(MoteurSansModele):
        id = "mien"
        libelle = "Le mien"

        def formuler_question(self, entree: Any) -> str:
            return "Question du moteur injecté ?"

        def est_tour_lourd(self, texte: str, etape: str) -> bool:
            return True

    def factory(root: Path, slug: str) -> Any:
        calls.append((root, slug))
        return Mien()

    conv_routes.set_moteur(factory)
    out = ok(project, "chantiers/un/conversation/demarrer")
    assert "Question du moteur injecté ?" in out["reaction"]["message"] and out["conversation"]["moteur"]["id"] == "mien"
    assert out["conversation"]["moteur"] == {"id": "mien", "libelle": "Le mien", "sans_modele": False, "limites": list(MoteurSansModele().limites())}
    turned = ok(project, "chantiers/un/conversation/tour", {"rev": out["conversation"]["rev"], "texte": "Les mainteneurs"})
    assert turned["reaction"]["lourd"] is True and calls and calls[0] == (project, "un")
    conv_routes.set_moteur(None)
    assert get(project, "chantiers/un/conversation")["moteur"]["id"] == "sans-modele"


def test_un_moteur_hostile_par_les_routes_ne_peut_ni_sortir_du_cadre_ni_ecrire_ailleurs(project: Path) -> None:
    class Pirate(MoteurSansModele):
        id = "pirate"

        def extraire_elements(self, entree: Any) -> Any:
            return [ElementExtrait("pirate", "exigences/Fonctionnelles"), ElementExtrait("pirate", "../../../etc/cron.d/x")]

        def classer_reponse(self, entree: Any) -> Any:
            return "ecrire-le-document"

        def formuler_question(self, entree: Any) -> Any:
            raise RuntimeError("boum")

    conv_routes.set_moteur(lambda root, slug: Pirate())
    started = ok(project, "chantiers/un/conversation/demarrer")
    assert started["reaction"]["moteur"]["degrade"] is True and started["reaction"]["question"]["kind"] == "amorce"
    before = {p.name: p.read_bytes() for p in (project / "_grimoire/cadrage/un").glob("0*.md")}
    turned = ok(project, "chantiers/un/conversation/tour", {"rev": started["conversation"]["rev"], "texte": "Les mainteneurs"})
    assert [e["texte"] for e in turned["conversation"]["elements"]] == ["Les mainteneurs"]
    assert {p.name: p.read_bytes() for p in (project / "_grimoire/cadrage/un").glob("0*.md")} == before
    assert not (project / "etc").exists() and not list(project.rglob("cron.d"))


def test_la_liste_des_conversations_actives_et_classees(project: Path) -> None:
    new_chantier(project, "Deux", slug="deux")
    new_chantier(project, "Trois", slug="trois")
    for slug in ("un", "deux"):
        interview.interview_plan(project, slug, depth="standard")
        ok(project, f"chantiers/{slug}/conversation/demarrer")
    assert [r["chantier"] for r in get(project, "conversations")["actives"]] == ["deux", "un"]
    rev = get(project, "chantiers/un/conversation")["rev"]
    classed = ok(project, "chantiers/un/conversation/classer", {"rev": rev})
    assert classed["reaction"]["statut"] == "classee"
    listing = get(project, "conversations")
    assert [r["chantier"] for r in listing["actives"]] == ["deux"] and [r["chantier"] for r in listing["classees"]] == ["un"]
    assert listing["erreurs"] == [] and listing["reglages"]["max_actives"] == 5
    assert post(project, "chantiers/un/conversation/tour", {"rev": rev + 1, "texte": "x"})[0] == 400  # classée : refusé
    assert post(project, "chantiers/un/conversation/classer", {"rev": rev + 1})[0] == 400  # déjà classée
    again = ok(project, "chantiers/un/conversation/rouvrir", {"rev": rev + 1})
    assert again["conversation"]["statut"] == "active" and len(get(project, "conversations")["actives"]) == 2
    assert (project / "_grimoire/cadrage/un" / store.FILENAME).exists()  # jamais supprimée


def test_les_reglages_se_lisent_dans_le_contexte_du_projet_et_sont_bornes(project: Path) -> None:
    assert conv_routes.reglages(project) == hygiene.Reglages()
    (project / "project-context.yaml").write_text(
        "project:\n  name: demo\ncockpit:\n  test_features:\n    atelier: true\n  atelier_conversation:\n"
        "    sommeil_jours: 1\n    classee_jours: 2\n    max_actives: 1\n", encoding="utf-8")
    assert conv_routes.reglages(project) == hygiene.Reglages(1, 2, 1)
    (project / "project-context.yaml").write_text(
        "project:\n  name: demo\ncockpit:\n  test_features:\n    atelier: true\n  atelier_conversation: 12\n", encoding="utf-8")
    assert conv_routes.reglages(project) == hygiene.Reglages()
    (project / "project-context.yaml").write_text("project:\n  name: demo\n", encoding="utf-8")
    assert conv_routes.reglages(project) == hygiene.Reglages()


def test_une_conversation_sans_activite_passe_en_sommeil_puis_est_classee_sans_etre_supprimee(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ok(project, "chantiers/un/conversation/demarrer")
    now = datetime.now(UTC)
    monkeypatch.setattr(hygiene, "maintenant", lambda: now + timedelta(days=5))
    assert get(project, "conversations")["actives"][0]["statut"] == "en-sommeil"
    monkeypatch.setattr(hygiene, "maintenant", lambda: now + timedelta(days=30))
    listing = get(project, "conversations")
    assert listing["actives"] == [] and listing["classees"][0]["classee_auto"] is True
    assert get(project, "chantiers/un/conversation")["statut"] == "classee"
    assert (project / "_grimoire/cadrage/un" / store.FILENAME).exists()


def test_un_fichier_de_conversation_hostile_ne_plante_rien_et_n_ecrit_pas_hors_du_chantier(project: Path) -> None:
    path = project / "_grimoire/cadrage/un" / store.FILENAME
    hostile = {"schema": 1, "chantier": "un", "mode": "cadrage", "elements": [
        {"id": "e1", "genre": "element", "texte": "pirate", "phase": "../../../tmp", "section": "x", "angle": "utilisateurs",
         "source": 1, "statut": "confirme"}]}
    path.write_text(json.dumps(hostile), encoding="utf-8")
    before = {p.name: p.read_bytes() for p in (project / "_grimoire/cadrage/un").glob("0*.md")}
    assert "erreur" in get(project, "chantiers/un/conversation")
    assert get(project, "conversations")["erreurs"][0]["chantier"] == "un"
    for action, body in (("demarrer", {}), ("tour", {"rev": 0, "texte": "x"}), ("pause", {"rev": 0})):
        code, out = post(project, f"chantiers/un/conversation/{action}", body)
        assert code == 400 and "conversation" in out["error"]
    code, _ = post(project, "chantiers/un/conversation/elements/e1", {"rev": 0, "decision": "confirmer"})
    assert code == 400
    assert {p.name: p.read_bytes() for p in (project / "_grimoire/cadrage/un").glob("0*.md")} == before
    assert path.read_text(encoding="utf-8") == json.dumps(hostile)
    assert not list(project.parent.rglob("tmp/x"))


def test_un_document_accepte_n_est_jamais_ecrit_par_les_routes(project: Path) -> None:
    started = ok(project, "chantiers/un/conversation/demarrer")
    turned = ok(project, "chantiers/un/conversation/tour", {"rev": started["conversation"]["rev"], "texte": "Les mainteneurs"})
    brief = project / "_grimoire/cadrage/un/01-brief.md"
    brief.write_text(brief.read_text(encoding="utf-8").replace("status: draft", "status: accepted"), encoding="utf-8")
    before = brief.read_bytes()
    code, out = post(project, f"chantiers/un/conversation/elements/{turned['conversation']['elements'][0]['id']}",
                     {"rev": turned["reaction"]["rev"], "decision": "confirmer"})
    assert code == 400 and "accepté" in out["error"] and brief.read_bytes() == before


def test_la_decision_inconnue_ou_l_element_inconnu_sont_refuses(project: Path) -> None:
    rev = ok(project, "chantiers/un/conversation/demarrer")["conversation"]["rev"]
    assert post(project, "chantiers/un/conversation/elements/e9", {"rev": rev, "decision": "confirmer"})[0] == 404
    assert post(project, "chantiers/un/conversation/elements/e9", {"rev": rev})[0] in (400, 404)
    assert post(project, "chantiers/un/conversation/elements/e1", {"rev": rev, "decision": "pirater"})[0] in (400, 404)
