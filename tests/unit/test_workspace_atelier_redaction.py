"""Les routes du document à la fin (épique #1477, lot E) : lire, proposer, accepter, écarter, reprendre ; seule l'acceptation écrit."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from grimoire.core import cadrage_conversation_redaction as red
from grimoire.core import cadrage_conversation_store as store
from grimoire.core import cadrage_interview as interview
from grimoire.core.chantiers import new_chantier
from grimoire.tools import workspace_atelier as wa
from grimoire.tools import workspace_atelier_conversation as conv_routes
from grimoire.tools import workspace_atelier_loop as loop

A = "/api/workspace/atelier/"
P = "chantiers/un/conversation"
RUB = "brief/L'utilisateur"


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


def _brief(root: Path) -> str:
    return (root / "_grimoire/cadrage/un/01-brief.md").read_text(encoding="utf-8")


def _confirmed(root: Path) -> int:
    """Démarre, répond, confirme : « Les mainteneurs du kit » est rangé ; rend la version vue."""
    rev = ok(root, f"{P}/demarrer")["conversation"]["rev"]
    turned = ok(root, f"{P}/tour", {"rev": rev, "texte": "Les mainteneurs du kit"})
    return int(ok(root, f"{P}/tour", {"rev": turned["reaction"]["rev"], "texte": "oui"})["reaction"]["rev"])


def test_le_parcours_proposer_relire_accepter(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    touched: list[str] = []
    monkeypatch.setattr(loop, "after_write", lambda root, slug: touched.append(slug))
    rev = _confirmed(project)
    touched.clear()
    assert "- *" in _brief(project) and "Les mainteneurs du kit" in _brief(project)
    vue = wa.get(project, A + f"{P}/redaction", {})
    assert vue["propositions"] == [] and [r["cle"] for r in vue["rubriques"]] == [RUB]
    before = _brief(project)
    proposed = ok(project, f"{P}/redaction/proposer", {"rev": rev, "rubrique": RUB})
    prop = proposed["reaction"]["proposition"]
    assert prop["texte"] == "- Les mainteneurs du kit" and _brief(project) == before and touched == []
    assert proposed["redaction"]["propositions"][0]["id"] == prop["id"]
    assert proposed["conversation"]["rev"] == proposed["reaction"]["rev"]
    one = wa.get(project, A + f"{P}/redaction/{prop['id']}", {})
    assert one["statut"] == "attente"
    accepted = ok(project, f"{P}/redaction/{prop['id']}/accepter", {"rev": proposed["reaction"]["rev"], "texte": prop["texte"]})
    assert accepted["reaction"]["document_modifie"] is True and touched == ["un"]
    assert "- Les mainteneurs du kit" in _brief(project).splitlines() and "- *" not in _brief(project).split("## L'utilisateur")[1].split("## ")[0]
    passages = wa.get(project, A + f"{P}/redaction", {})["passages"]
    assert passages[0]["fondements"][0]["texte"] == "Les mainteneurs du kit" and passages[0]["tours"]


def test_ecarter_et_reprendre_n_ecrivent_rien(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    touched: list[str] = []
    monkeypatch.setattr(loop, "after_write", lambda root, slug: touched.append(slug))
    rev = _confirmed(project)
    touched.clear()
    before = _brief(project)
    first = ok(project, f"{P}/redaction/proposer", {"rev": rev, "rubrique": RUB})["reaction"]
    dropped = ok(project, f"{P}/redaction/{first['proposition']['id']}/ecarter", {"rev": first["rev"]})["reaction"]
    second = ok(project, f"{P}/redaction/proposer", {"rev": dropped["rev"], "rubrique": RUB})["reaction"]
    back = ok(project, f"{P}/redaction/{second['proposition']['id']}/reprendre", {"rev": second["rev"]})
    assert back["conversation"]["question"]["section"] == "L'utilisateur"
    assert _brief(project) == before and touched == []


@pytest.mark.parametrize("body", [
    {"rubrique": RUB, "texte": "## Pirate"},
    {"rubrique": RUB, "phase": "brief"},
    {"rubrique": RUB, "section": "L'utilisateur"},
    {"rubrique": RUB, "chemin": "../../x"},
    {"rubrique": RUB, "phrases": [{"texte": "x", "fondements": ["e1"]}]},
])
def test_proposer_n_accepte_ni_texte_ni_chemin(project: Path, body: dict[str, Any]) -> None:
    rev = _confirmed(project)
    saved, before = (project / "_grimoire/cadrage/un" / store.FILENAME).read_bytes(), _brief(project)
    code, out = post(project, f"{P}/redaction/proposer", {**body, "rev": rev})
    assert code == 400 and "refusé" in out["error"]
    assert (project / "_grimoire/cadrage/un" / store.FILENAME).read_bytes() == saved and _brief(project) == before


def test_les_gestes_de_decision_n_acceptent_que_leurs_champs(project: Path) -> None:
    rev = _confirmed(project)
    prop = ok(project, f"{P}/redaction/proposer", {"rev": rev, "rubrique": RUB})["reaction"]
    pid, before = prop["proposition"]["id"], _brief(project)
    assert post(project, f"{P}/redaction/{pid}/accepter", {"rev": prop["rev"], "texte": prop["proposition"]["texte"],
                                                         "lignes": ["- pirate"]})[0] == 400
    assert post(project, f"{P}/redaction/{pid}/ecarter", {"rev": prop["rev"], "texte": "x"})[0] == 400
    assert post(project, f"{P}/redaction/{pid}/reprendre", {"rev": prop["rev"], "rubrique": RUB})[0] == 400
    code, out = post(project, f"{P}/redaction/{pid}/accepter", {"rev": prop["rev"], "texte": "- autre chose"})
    assert code == 409 and out["reason"] == "proposition-differente"
    assert _brief(project) == before


@pytest.mark.parametrize("rev", [None, "1", True, -1])
def test_la_version_vue_est_obligatoire(project: Path, rev: Any) -> None:
    _confirmed(project)
    body: dict[str, Any] = {"rubrique": RUB} if rev is None else {"rubrique": RUB, "rev": rev}
    code, out = post(project, f"{P}/redaction/proposer", body)
    assert code == 400 and "rev" in out["error"]


def test_une_version_perimee_donne_409(project: Path) -> None:
    rev = _confirmed(project)
    code, out = post(project, f"{P}/redaction/proposer", {"rev": rev - 1, "rubrique": RUB})
    assert code == 409 and out["reason"] == "conversation-changee"


@pytest.mark.parametrize("rubrique", ["brainstorm/Questions ouvertes", "exigences/Fonctionnelles", "../../etc", 3])
def test_une_rubrique_hors_cadre_donne_400(project: Path, rubrique: Any) -> None:
    rev = _confirmed(project)
    code, out = post(project, f"{P}/redaction/proposer", {"rev": rev, "rubrique": rubrique})
    assert code == 400 and "rubrique" in out["error"]


@pytest.mark.parametrize("path", [f"{P}/redaction", f"{P}/redaction/r1", f"{P}/redaction/r1/publier",
                                  f"{P}/redaction/r1/accepter/x", f"{P}/redaction/proposer/x"])
def test_les_routes_inconnues_donnent_404(project: Path, path: str) -> None:
    rev = _confirmed(project)
    assert post(project, path, {"rev": rev})[0] == 404


def test_le_drapeau_eteint_ferme_les_routes(project: Path) -> None:
    rev = _confirmed(project)
    wa.set_atelier(project, False)
    assert post(project, f"{P}/redaction/proposer", {"rev": rev, "rubrique": RUB})[0] == 404
    with pytest.raises(FileNotFoundError):
        wa.get(project, A + f"{P}/redaction", {})


def test_un_modele_injecte_passe_par_le_meme_gardien(project: Path) -> None:
    class Hostile:
        id = "hostile"
        libelle = "Modèle hostile"

        def proposer_redaction(self, entree: red.EntreeRedaction) -> red.Redaction:
            return red.Redaction("exigences/Fonctionnelles", (red.PhraseRedigee("REQ pirate", ("e1",)),))

    rev = _confirmed(project)
    conv_routes.set_moteur(lambda root, slug: Hostile())
    out = ok(project, f"{P}/redaction/proposer", {"rev": rev, "rubrique": RUB})["reaction"]
    assert out["proposition"]["moteur"] == "sans-modele" and out["moteur"]["degrade"] is True
    assert "pirate" not in _brief(project)


def test_la_lecture_sans_conversation(project: Path) -> None:
    assert wa.get(project, A + f"{P}/redaction", {}) == {"existe": False}
