"""Le cadrage guidé SANS agent, joué dans la page : mêmes écritures que `grimoire cadrage interview answer`, mêmes gardes.

Aucun agent, aucun modèle : le geste passe par la route d'écriture existante de l'assistant (`POST …/assistant`), qui est
gardée comme les autres (drapeau, refus d'une dictée pour tout geste sauf la réponse libre, chantier retrouvé dans la liste).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from grimoire.core import cadrage_interview as interview
from grimoire.core.chantiers import new_chantier
from grimoire.tools import workspace_atelier as wa
from grimoire.tools import workspace_atelier_assistant as asst
from grimoire.tools import workspace_atelier_guide as guide

P = "/api/workspace/"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "project-context.yaml").write_text("project:\n  name: demo\n", encoding="utf-8")
    wa.set_atelier(tmp_path, True)
    new_chantier(tmp_path, "Un chantier", slug="un")
    monkeypatch.setattr(asst, "agent_status", lambda root: {"available": False, "mode": "kanban", "reason": "aucun hôte", "name": ""})
    return tmp_path


def _act(root: Path, geste: str, **body: Any) -> dict[str, Any]:
    return asst.act(root, "un", {"geste": geste, **body})


def _step(root: Path) -> dict[str, Any]:
    current = guide.view(root, "un")["current"]
    assert current is not None
    return {"angle": current["angle"], "index": current["index"]}


def _read(root: Path, name: str) -> str:
    return (root / "_grimoire" / "cadrage" / "un" / name).read_text(encoding="utf-8")


def test_sans_demarrage_le_guide_est_lisible_et_n_ecrit_rien(project: Path) -> None:
    view = asst.entretien_view(project, "un")
    assert view["guide"]["available"] is True and view["guide"]["started"] is False
    assert view["moteur"] == {"id": "guide", "label": "Questions guidées, sans IA"}
    assert not (project / "_grimoire/cadrage/un/entretien.json").exists()  # une lecture n'écrit pas


def test_avec_un_agent_le_moteur_est_nomme(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asst, "agent_status", lambda root: {"available": True, "mode": "acp", "reason": "", "name": "claude"})
    assert asst.entretien_view(project, "un")["moteur"] == {"id": "agent", "label": "Avec l’agent claude"}


def test_demarrer_pose_la_premiere_question(project: Path) -> None:
    out = _act(project, "guide-demarrer")
    shown = out["guide"]
    assert shown["started"] is True and shown["finished"] is False
    assert shown["position"] == 0 and shown["total"] >= 3
    assert shown["current"]["question"] and shown["previous"] is None
    assert (project / "_grimoire/cadrage/un/entretien.json").is_file()


def test_la_reponse_est_consignee_exactement_comme_la_commande(project: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    twin = tmp_path_factory.mktemp("twin")
    new_chantier(twin, "Un chantier", slug="un")
    _act(project, "guide-demarrer")
    step = _step(project)
    _act(project, "guide-repondre", text="les  mainteneurs\ndu kit", **step)
    interview.interview_plan(twin, "un")
    interview.answer(twin, "un", step["angle"], "les  mainteneurs\ndu kit")
    for name in ("01-brief.md", "02-brainstorm.md", "03-comprehension.md", "05-cahier-des-charges.md", "entretien.json"):
        assert _read(project, name) == (twin / "_grimoire" / "cadrage" / "un" / name).read_text(encoding="utf-8"), name
    assert "les mainteneurs du kit" in _read(project, "01-brief.md")  # le bon bloc, texte replié sur une ligne


def test_les_questions_se_suivent_une_a_une(project: Path) -> None:
    _act(project, "guide-demarrer")
    seen = []
    for _ in range(40):
        shown = guide.view(project, "un")
        if shown["finished"]:
            break
        seen.append((shown["current"]["angle"], shown["current"]["index"]))
        assert shown["position"] == len(seen) - 1
        _act(project, "guide-repondre", text=f"réponse {len(seen)}", **_step(project))
    assert len(seen) == len(set(seen)) >= 3  # chaque question est posée une seule fois
    assert guide.view(project, "un")["finished"] is True
    assert interview.view(project, "un")["complete"] is True  # le gate de la commande est satisfait


def test_je_ne_sais_pas_ecrit_l_hypothese_et_ferme_l_angle(project: Path) -> None:
    _act(project, "guide-demarrer")
    step = _step(project)
    out = _act(project, "guide-ne-sais-pas", **step)
    assert out["guide"]["current"]["angle"] != step["angle"]  # l'angle entier est clos, pas seulement la question
    asked = next(a for a in interview.ANGLES if a.id == step["angle"]).seeds[step["index"]]
    assert f"À préciser : {asked}" in _read(project, "02-brainstorm.md")  # la question, pas le titre de l'angle


def test_sans_objet_ecarte_l_angle_avec_sa_raison(project: Path) -> None:
    _act(project, "guide-demarrer")
    step = _step(project)
    _act(project, "guide-sans-objet", **step)
    assert "> À compléter — sans objet d'après l'entretien" in _read(project, "01-brief.md").split("## L'utilisateur")[1].split("##")[0]  # à sa place
    assert "Angle «" not in _read(project, "02-brainstorm.md")  # le brainstorm n'est pas pollué
    rec = next(a for a in interview.view(project, "un")["angles"] if a["id"] == step["angle"])
    assert rec["state"] == "ecarte" and rec["reason"] == "sans objet"


def test_retour_retire_la_reponse_et_la_rend_pour_correction(project: Path) -> None:
    _act(project, "guide-demarrer")
    first = _step(project)
    before = _read(project, "01-brief.md")
    _act(project, "guide-repondre", text="ma première réponse", **first)
    assert "ma première réponse" in _read(project, "01-brief.md")
    back = _act(project, "guide-retour", index=first["index"])
    assert back["retracted"]["text"] == "ma première réponse" and back["retracted"]["removed"] is True
    assert _read(project, "01-brief.md") == before  # le gabarit est revenu, rien d'autre n'a bougé
    assert _step(project) == first
    _act(project, "guide-repondre", text="ma réponse corrigée", **first)
    text = _read(project, "01-brief.md")
    assert "ma réponse corrigée" in text and "ma première réponse" not in text


def test_retour_apres_je_ne_sais_pas_et_sans_objet(project: Path) -> None:
    _act(project, "guide-demarrer")
    one = _step(project)
    _act(project, "guide-ne-sais-pas", **one)
    out = _act(project, "guide-retour", index=0)
    assert out["retracted"]["kind"] == "unknown"
    assert interview.UNKNOWN_PREFIX not in _read(project, "03-comprehension.md")
    assert _step(project) == one
    _act(project, "guide-sans-objet", **one)
    out = _act(project, "guide-retour", index=0)
    assert out["retracted"]["kind"] == "skip" and "Angle «" not in _read(project, "02-brainstorm.md")
    assert _step(project) == one


def test_retour_sur_une_ligne_retouchee_a_la_main_la_laisse(project: Path) -> None:
    _act(project, "guide-demarrer")
    first = _step(project)
    _act(project, "guide-repondre", text="écrit par le guide", **first)
    brief = project / "_grimoire/cadrage/un/01-brief.md"
    brief.write_text(brief.read_text(encoding="utf-8").replace("écrit par le guide", "retouché par la personne"), encoding="utf-8")
    out = _act(project, "guide-retour", index=first["index"])
    assert out["retracted"]["removed"] is False
    assert "retouché par la personne" in brief.read_text(encoding="utf-8")


def test_retour_sur_la_premiere_question_est_refuse(project: Path) -> None:
    _act(project, "guide-demarrer")
    with pytest.raises(ValueError, match="première question"):
        _act(project, "guide-retour", index=0)


def test_reprise_apres_rechargement_le_curseur_se_deduit_du_fichier(project: Path) -> None:
    _act(project, "guide-demarrer")
    _act(project, "guide-repondre", text="une", **_step(project))
    _act(project, "guide-repondre", text="deux", **_step(project))
    saved = guide.view(project, "un")
    # Un autre processus, un rechargement de page : rien en mémoire, tout vient de entretien.json et des fichiers de phase.
    assert guide.view(project, "un") == saved
    assert saved["position"] == 2 and saved["previous"]["answer"] == "deux"


def test_la_question_changee_entre_temps_est_refusee_en_409(project: Path) -> None:
    _act(project, "guide-demarrer")
    step = _step(project)
    _act(project, "guide-repondre", text="déjà répondu ailleurs", **step)
    with pytest.raises(wa.ConflictError) as raised:
        _act(project, "guide-repondre", text="réponse périmée", **step)
    assert raised.value.reason == "question-changee"
    status, payload = wa.post_http(project, f"{P}atelier/chantiers/un/assistant", {"geste": "guide-repondre", "text": "x", **step})
    assert status == 409 and payload["reason"] == "question-changee"
    assert "réponse périmée" not in _read(project, "01-brief.md")


@pytest.mark.parametrize("body", [{"text": ""}, {"text": "   "}, {"text": "x" * 4001}])
def test_une_reponse_vide_ou_trop_longue_est_refusee(project: Path, body: dict[str, Any]) -> None:
    _act(project, "guide-demarrer")
    with pytest.raises(ValueError):
        _act(project, "guide-repondre", **body, **_step(project))


def test_sans_question_en_cours_rien_ne_s_ecrit(project: Path) -> None:
    with pytest.raises(ValueError, match="aucune question"):
        _act(project, "guide-repondre", text="x", angle="utilisateurs", index=0)


def test_la_garde_d_une_dictee_vaut_pour_les_gestes_guides_sauf_la_reponse(project: Path) -> None:
    _act(project, "guide-demarrer")
    step = _step(project)
    for geste in ("guide-demarrer", "guide-ne-sais-pas", "guide-sans-objet", "guide-retour"):
        with pytest.raises(ValueError, match="dictée"):
            _act(project, geste, dictation=True, **step)
    out = _act(project, "guide-repondre", text="dicté puis relu", dictation=True, **step)  # texte libre de l'humain : permis
    assert out["ok"] is True


def test_un_drapeau_eteint_ou_un_chantier_inconnu_refusent(project: Path) -> None:
    with pytest.raises(FileNotFoundError):
        asst.act(project, "inconnu", {"geste": "guide-demarrer"})
    wa.set_atelier(project, False)
    with pytest.raises(FileNotFoundError):
        _act(project, "guide-demarrer")


def test_un_lien_symbolique_dans_le_dossier_du_chantier_est_refuse(project: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    outside = tmp_path_factory.mktemp("dehors") / "02-brainstorm.md"
    outside.write_text("dehors\n", encoding="utf-8")
    target = project / "_grimoire/cadrage/un/02-brainstorm.md"
    target.unlink()
    target.symlink_to(outside)
    with pytest.raises(FileNotFoundError):
        _act(project, "guide-demarrer")
    assert outside.read_text(encoding="utf-8") == "dehors\n"


def test_une_nature_sans_entretien_bloquant_n_offre_pas_de_guide(project: Path) -> None:
    (project / "_grimoire/cadrage/un/entretien.json").write_text(
        '{"depth": "standard", "kind": "question", "angles": {}}', encoding="utf-8")
    shown = guide.view(project, "un")
    assert shown["available"] is False and shown["finished"] is True


def test_un_entretien_abime_est_dit(project: Path) -> None:
    (project / "_grimoire/cadrage/un/entretien.json").write_text("{pas du json", encoding="utf-8")
    shown = guide.view(project, "un")
    assert shown["available"] is False and "illisible" in shown["error"]
