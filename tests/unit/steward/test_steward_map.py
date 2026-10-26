"""La carte de l'intendant (#876, lot 5) : déterministe, fusion par comparaison d'arbres, signaux, « non mesuré »."""

from __future__ import annotations

import json
from typing import Any

import pytest
from steward_doubles import Recorder, Scene

from grimoire.steward import MAP_SCHEMA, project_map
from grimoire.steward.project_map import build_map, ci_state, issue_refs
from grimoire.steward.sources import NON_MESURE


def _by_ref(carte: dict[str, Any], section: str) -> dict[str, dict[str, Any]]:
    return {e["ref"]: e for e in carte[section]["elements"]}


def _signals(carte: dict[str, Any], kind: str) -> set[str]:
    return {s["ref"] for s in carte["signaux"] if s["type"] == kind}


def test_la_carte_est_versionnee_et_stable(scene: Scene) -> None:
    first = build_map(scene.sources())
    second = build_map(scene.sources())
    assert first["$schema"] == MAP_SCHEMA
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    for section in ("taches", "branches", "worktrees", "prs", "issues"):
        assert first[section]["mesure"] is True, section
    assert first["resume"]["taches"] == 6
    assert first["resume"]["taches_ouvertes"] == 5


def test_taches_du_ledger(scene: Scene) -> None:
    taches = _by_ref(build_map(scene.sources()), "taches")
    tenue = taches[f"tache:{scene.tasks['tenue']}"]
    assert tenue["etat"] == "claimed"
    assert tenue["claim"]["session"] == "S1"
    assert tenue["session"]["vivacite"]["state"] == "vivant"
    assert taches[f"tache:{scene.tasks['issue_fermee']}"]["liens"]["issues"] == [12]
    assert taches[f"tache:{scene.tasks['annulee']}"]["ouverte"] is False


def test_fusion_par_comparaison_d_arbres(scene: Scene) -> None:
    """Une fusion squash n'est pas ancêtre de la cible : seule la comparaison d'arbres la voit."""
    branches = _by_ref(build_map(scene.sources()), "branches")
    squash = branches["branche:feat/squash"]
    assert squash["en_avance"] == 1
    assert squash["fusion"] == {"etat": "fusionnee", "par": "fusion_sans_apport"}
    assert branches["branche:feat/pr-fusionnee"]["fusion"] == {"etat": "fusionnee", "par": "pr #40"}
    assert branches["branche:feat/hors-cible"]["fusion"]["etat"] == "non_fusionnee"
    assert branches["branche:feat/hors-cible"]["conflit"]["etat"] == "aucun"
    assert "branche:origin/feat/squash" in branches  # les distantes du remote de la cible


def test_une_branche_sans_commit_d_avance_est_vide_pas_fusionnee(scene: Scene) -> None:
    branches = _by_ref(build_map(scene.sources()), "branches")
    assert branches["branche:feat/vide"]["fusion"]["etat"] == "vide"
    assert branches["branche:feat/ancetre"]["fusion"]["etat"] == "vide"


def test_pr_empilee_ou_fusionnee_hors_cible_n_est_pas_une_fusion(scene: Scene) -> None:
    """B1 : une PR fusionnée vers une autre base, ou dont le commit de fusion n'est pas sur la cible, ne fusionne rien."""
    branches = _by_ref(build_map(scene.sources()), "branches")
    assert branches["branche:feat/empilee"]["fusion"]["etat"] == "non_fusionnee"
    assert branches["branche:feat/fusion-ailleurs"]["fusion"]["etat"] == "non_fusionnee"


def test_un_arbre_ancien_de_la_cible_revient_en_arriere(scene: Scene) -> None:
    carte = build_map(scene.sources())
    retour = _by_ref(carte, "branches")["branche:feat/retour"]["fusion"]
    assert retour["etat"] == "etat_anterieur"
    assert retour["par"].startswith(f"revient à l'état de {scene.shas['init'][:12]}")
    assert "branche:feat/retour" in _signals(carte, "branche_etat_anterieur")


def test_worktree_sale(scene: Scene) -> None:
    carte = build_map(scene.sources())
    worktrees = _by_ref(carte, "worktrees")
    assert worktrees[f"worktree:{scene.worktree.as_posix()}"]["sale"] is True  # git rend des chemins en « / »
    assert worktrees[f"worktree:{scene.root.as_posix()}"]["sale"] is False
    assert _by_ref(carte, "branches")["branche:feat/sale"]["worktree_sale"] is True
    sale = [s for s in carte["signaux"] if s["type"] == "worktree_sale"]
    assert [s["branche"] for s in sale] == ["branche:feat/sale"]


def test_conflit_d_une_pr_sur_sa_tete_locale(scene: Scene) -> None:
    prs = _by_ref(build_map(scene.sources()), "prs")
    assert prs["pr:31"]["conflit"]["sha"] == scene.shas["changelog"]
    assert prs["pr:34"]["conflit"]["etat"] == NON_MESURE
    assert "absente du dépôt local" in prs["pr:34"]["conflit"]["raison"]


def test_listes_tronquees_partielles(scene: Scene, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(project_map, "PR_LIMIT", 2)
    monkeypatch.setattr(project_map, "ISSUE_LIMIT", 1)
    monkeypatch.setattr(project_map, "MERGED_LIMIT", 3)
    carte = build_map(scene.sources())
    assert carte["prs"]["mesure"] == "partielle" and "limite" in carte["prs"]["raison"]
    assert carte["issues"]["mesure"] == "partielle"
    assert any("PR fusionnées" in t for t in carte["troncatures"])


def test_conflit_changelog_seul_ou_autre(scene: Scene) -> None:
    carte = build_map(scene.sources())
    branches = _by_ref(carte, "branches")
    assert branches["branche:feat/changelog"]["conflit"] == {"etat": "changelog_seul", "fichiers": ["CHANGELOG.md"]}
    assert branches["branche:feat/semantique"]["conflit"] == {"etat": "autre", "fichiers": ["src/app.py"]}
    assert _signals(carte, "conflit_changelog_seul") >= {"branche:feat/changelog", "pr:31"}
    assert _signals(carte, "conflit_autre") >= {"branche:feat/semantique", "pr:32"}


def test_branche_orpheline(scene: Scene) -> None:
    carte = build_map(scene.sources())
    branches = _by_ref(carte, "branches")
    assert branches[f"branche:task/{scene.tasks['annulee']}"]["orpheline"] is True
    assert branches[f"branche:task/{scene.tasks['tenue']}"]["orpheline"] is False
    assert f"branche:task/{scene.tasks['annulee']}" in _signals(carte, "branche_orpheline")


def test_pr_inactive_sans_session_vivante(scene: Scene) -> None:
    carte = build_map(scene.sources())
    prs = _by_ref(carte, "prs")
    assert prs["pr:30"]["taches"] == [scene.tasks["tenue"]]
    assert prs["pr:33"]["session_vivante"] is False
    assert prs["pr:30"]["session_vivante"] is True
    assert prs["pr:31"]["session_vivante"] is False
    assert prs["pr:31"]["ci"] == "succes"
    # 30 : vieille mais sa session est vivante ; 32 : récente ; 31 : vieille et sans session.
    assert _signals(carte, "pr_inactive_sans_session_vivante") == {"pr:31", "pr:33", "pr:34", "pr:35"}
    # Sans signe de vie de la sonde, la session de 30 est « inconnue » : plus vivante, la PR monte.
    dead = build_map(scene.sources(alive=False))
    assert "pr:30" in _signals(dead, "pr_inactive_sans_session_vivante")


def test_seuil_d_inactivite_reglable(scene: Scene) -> None:
    carte = build_map(scene.sources(alive=False), inactivity_hours=100_000)
    assert _signals(carte, "pr_inactive_sans_session_vivante") == set()


def test_signaux_des_taches(scene: Scene) -> None:
    carte = build_map(scene.sources())
    assert _signals(carte, "tache_ouverte_issue_fermee") == {f"tache:{scene.tasks['issue_fermee']}"}
    assert _signals(carte, "tache_ouverte_citee_par_commit") == {f"tache:{scene.tasks['citee']}"}
    cited = _by_ref(carte, "taches")[f"tache:{scene.tasks['citee']}"]
    assert [c["sha"] for c in cited["commits_citant"]] == [scene.shas["cite"]]


def test_sans_gh_la_carte_dit_non_mesure(scene: Scene) -> None:
    carte = build_map(scene.sources(Recorder(scene.gh, gh_missing=True)))
    assert carte["prs"]["mesure"] is False
    assert "introuvable" in carte["prs"]["raison"]
    assert carte["issues"]["mesure"] is False
    assert carte["resume"]["prs"] == NON_MESURE
    assert carte["branches"]["mesure"] is True  # git, lui, répond
    tache = _by_ref(carte, "taches")[f"tache:{scene.tasks['issue_fermee']}"]
    assert tache["liens"]["etats"] == {"12": {"etat": NON_MESURE}}


def test_sans_ledger_ni_cible(scene: Scene) -> None:
    src = scene.sources()
    src.target = "origin/inexistante"
    carte = build_map(src)
    assert carte["cible"]["sha"] == NON_MESURE
    assert carte["branches"]["mesure"] is False


def test_aides_pures() -> None:
    assert issue_refs("Refs #12, voir https://github.com/o/r/pull/7 et a#3") == [7, 12]
    assert ci_state([]) == "aucune"
    assert ci_state([{"status": "IN_PROGRESS"}]) == "en_cours"
    assert ci_state([{"status": "COMPLETED", "conclusion": "FAILURE"}, {"status": "QUEUED"}]) == "echec"
    assert ci_state([{"__typename": "StatusContext", "state": "SUCCESS"}]) == "succes"


def test_la_carte_n_ecrit_rien_hors_du_dossier_steward(scene: Scene) -> None:
    recorder = Recorder(scene.gh)
    build_map(scene.sources(recorder))
    recorder.assert_read_only()
    assert not (scene.root / "_grimoire-output" / "steward" / ".objets-git").exists()
