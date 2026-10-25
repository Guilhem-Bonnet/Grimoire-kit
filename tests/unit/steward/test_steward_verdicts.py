"""Les verdicts de l'intendant (#876, lot 5) : chaque preuve acceptée quand elle est vraie, rejetée quand elle est fausse,
et rattachée à l'élément jugé — un faux « livré » fait perdre du travail, dans le doute on rejette."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from steward_doubles import Recorder, Scene

from grimoire.steward import VERDICTS_SCHEMA
from grimoire.steward.sources import scratch_objects
from grimoire.steward.verdicts import VerdictsFileError, load_verdicts, validate


def _run(scene: Scene, *verdicts: Any, alive: bool = True, recorder: Recorder | None = None) -> dict[str, Any]:
    return validate(scene.sources(recorder, alive=alive), {"$schema": VERDICTS_SCHEMA, "verdicts": list(verdicts)})


def _one(scene: Scene, element: str, verdict: str, *proofs: dict[str, Any], alive: bool = True) -> tuple[bool, dict[str, Any]]:
    result = _run(scene, {"element": element, "verdict": verdict, "preuves": list(proofs)}, alive=alive)
    if result["etablis"]:
        return True, result["etablis"][0]
    return False, result["rejetes"][0]


def _reasons(entry: dict[str, Any]) -> str:
    return " | ".join(entry.get("raisons") or [])


# ── commit ──


def test_commit_sur_la_cible_qui_nomme_la_tache(scene: Scene) -> None:
    task = scene.tasks["citee"]
    ok, entry = _one(scene, f"tache:{task}", "livree", {"type": "commit", "sha": scene.shas["cite"][:9], "message_contient": task})
    assert ok, _reasons(entry)
    assert entry["preuves"][0]["mesure"]["sha"] == scene.shas["cite"]


def test_commit_qui_ne_nomme_pas_l_element_ne_livre_rien(scene: Scene) -> None:
    """B2 : un commit de la cible quelconque (ici le commit racine) ne prouve pas qu'une tâche est livrée."""
    ok, entry = _one(scene, f"tache:{scene.tasks['citee']}", "livree", {"type": "commit", "sha": scene.shas["init"]})
    assert not ok and "preuve qui nomme l'élément" in _reasons(entry)
    # Le texte annoncé doit être dans le message ET nommer la tâche.
    ok, entry = _one(
        scene, f"tache:{scene.tasks['citee']}", "livree", {"type": "commit", "sha": scene.shas["cite"], "message_contient": "travail"}
    )
    assert not ok and "preuve qui nomme l'élément" in _reasons(entry)


def test_commit_absent_hors_cible_ou_reference_homonyme(scene: Scene) -> None:
    element = f"tache:{scene.tasks['citee']}"
    ok, entry = _one(scene, element, "obsolete", {"type": "commit", "sha": "abcdef123"})
    assert not ok and "absent du dépôt local" in _reasons(entry)
    ok, entry = _one(scene, element, "obsolete", {"type": "commit", "sha": scene.shas["hors_cible"]})
    assert not ok and "pas sur la cible" in _reasons(entry)
    # « deadbeef1 » est une branche qui pointe sur un commit : un SHA ne se résout jamais par une référence.
    ok, entry = _one(scene, element, "obsolete", {"type": "commit", "sha": "deadbeef1"})
    assert not ok and "absent du dépôt local" in _reasons(entry)


def test_commit_dont_le_message_ne_cite_pas_ce_qu_on_dit(scene: Scene) -> None:
    proof = {"type": "commit", "sha": scene.shas["cite"], "message_contient": "GAO-homonyme-001"}
    ok, entry = _one(scene, f"tache:{scene.tasks['citee']}", "livree", proof)
    assert not ok and "ne contient pas" in _reasons(entry)


# ── fichier ──


def test_fichier_present_accepte_absent_rejete(scene: Scene) -> None:
    element = f"tache:{scene.tasks['citee']}"
    assert _one(scene, element, "obsolete", {"type": "fichier", "chemin": "c.txt"})[0]
    ok, entry = _one(scene, element, "obsolete", {"type": "fichier", "chemin": "jamais.txt"})
    assert not ok and "absent de la cible" in _reasons(entry)
    assert not _one(scene, element, "obsolete", {"type": "fichier", "chemin": "h.txt"})[0]  # sur une branche seulement


def test_un_dossier_n_est_pas_un_fichier(scene: Scene) -> None:
    ok, entry = _one(scene, f"tache:{scene.tasks['citee']}", "obsolete", {"type": "fichier", "chemin": "src"})
    assert not ok and "tree" in _reasons(entry)


def test_fichier_motif(scene: Scene) -> None:
    element = f"tache:{scene.tasks['citee']}"
    assert _one(scene, element, "obsolete", {"type": "fichier", "chemin": "src/app.py", "motif": "return 3"})[0]
    ok, entry = _one(scene, element, "obsolete", {"type": "fichier", "chemin": "src/app.py", "motif": "return 2"})
    assert not ok and "motif absent" in _reasons(entry)


def test_fichier_absent_seulement_apres_avoir_existe(scene: Scene) -> None:
    """B2 : un chemin mal tapé est « absent » — ce n'est pas la preuve d'un retrait."""
    element = f"tache:{scene.tasks['citee']}"
    assert _one(scene, element, "obsolete", {"type": "fichier", "chemin": "retire.txt", "absent": True})[0]
    ok, entry = _one(scene, element, "obsolete", {"type": "fichier", "chemin": "nexiste-pas.txt", "absent": True})
    assert not ok and "n'a jamais existé" in _reasons(entry)
    assert not _one(scene, element, "obsolete", {"type": "fichier", "chemin": "c.txt", "absent": True})[0]
    # Un motif retiré : il a existé (return 1), il n'y est plus.
    assert _one(scene, element, "obsolete", {"type": "fichier", "chemin": "src/app.py", "motif": "return 1", "absent": True})[0]
    assert not _one(scene, element, "obsolete", {"type": "fichier", "chemin": "src/app.py", "motif": "jamais écrit", "absent": True})[0]


@pytest.mark.parametrize("chemin", ["/etc/passwd", "../dehors", "-x", "a\\b"])
def test_fichier_chemin_invalide(scene: Scene, chemin: str) -> None:
    ok, entry = _one(scene, f"tache:{scene.tasks['citee']}", "obsolete", {"type": "fichier", "chemin": chemin})
    assert not ok and "invalide" in _reasons(entry)


# ── issue / pr ──


def test_issue_fermee_acceptee_ouverte_rejetee(scene: Scene) -> None:
    element = f"tache:{scene.tasks['issue_fermee']}"
    assert _one(scene, element, "obsolete", {"type": "issue", "numero": 12})[0]
    ok, entry = _one(scene, element, "obsolete", {"type": "issue", "numero": 50})
    assert not ok and "#50 est ouverte, attendu fermee" in _reasons(entry)
    ok, entry = _one(scene, element, "obsolete", {"type": "issue", "numero": 999})
    assert not ok and "non mesuré" in _reasons(entry)


def test_une_issue_fermee_ne_livre_que_l_element_qu_elle_nomme(scene: Scene) -> None:
    """B2 : l'issue 5 fermée ne prouve pas que l'issue 11 est livrée ; l'issue liée à la tâche, oui."""
    ok, entry = _one(scene, "issue:11", "livree", {"type": "issue", "numero": 5})
    assert not ok and "preuve qui nomme l'élément" in _reasons(entry)
    assert _one(scene, f"tache:{scene.tasks['issue_fermee']}", "livree", {"type": "issue", "numero": 12})[0]


def test_issue_fermee_sans_suite_ou_pr_non_fusionnee_ne_livre_rien(scene: Scene) -> None:
    """B2 : ``not_planned`` n'est pas une livraison ; un numéro de PR fermée sans fusion non plus."""
    ok, entry = _one(scene, "issue:60", "livree", {"type": "issue", "numero": 60})
    assert not ok and "preuve qui nomme l'élément" in _reasons(entry)
    assert entry["preuves"][0]["mesure"]["raison_fermeture"] == "NOT_PLANNED"
    ok, entry = _one(scene, "pr:43", "livree", {"type": "issue", "numero": 43})
    assert not ok and entry["preuves"][0]["mesure"]["est_pr"] is True
    # Obsolète, en revanche, peut s'appuyer sur une fermeture sans suite.
    assert _one(scene, "issue:60", "obsolete", {"type": "issue", "numero": 60})[0]


def test_pr_fusionnee_dans_la_cible_qui_nomme_l_element(scene: Scene) -> None:
    element = f"tache:{scene.tasks['issue_fermee']}"
    ok, entry = _one(scene, element, "livree", {"type": "pr", "numero": 40})
    assert ok, _reasons(entry)
    # PR 40 ne nomme pas la tâche citée : fusionnée, mais pas une preuve de CETTE livraison.
    ok, entry = _one(scene, f"tache:{scene.tasks['citee']}", "livree", {"type": "pr", "numero": 40})
    assert not ok and "preuve qui nomme l'élément" in _reasons(entry)
    ok, entry = _one(scene, element, "livree", {"type": "pr", "numero": 31})
    assert not ok and "PR #31 est ouverte, attendu fusionnee" in _reasons(entry)


def test_pr_fusionnee_hors_cible_rejetee(scene: Scene) -> None:
    """B1 : fusionnée dans une autre base, ou commit de fusion absent de la cible."""
    element = f"tache:{scene.tasks['citee']}"
    ok, entry = _one(scene, element, "obsolete", {"type": "pr", "numero": 41})
    assert not ok and "pas vérifiée dans la cible" in _reasons(entry)
    ok, entry = _one(scene, element, "obsolete", {"type": "pr", "numero": 42})
    assert not ok and "feat/other" in _reasons(entry)


# ── livree d'une branche ──


def test_livree_d_une_branche_exige_sa_fusion(scene: Scene) -> None:
    """B2 : un commit racine ou un fichier quelconque ne fait pas d'une branche une branche livrée."""
    ok, entry = _one(scene, "branche:feat/hors-cible", "livree", {"type": "commit", "sha": scene.shas["init"]})
    assert not ok and "branche fusionnée" in _reasons(entry)
    ok, entry = _one(scene, "branche:feat/empilee", "livree", {"type": "fichier", "chemin": "README.md"})
    assert not ok and "non_fusionnee" in _reasons(entry)
    ok, entry = _one(scene, "branche:feat/retour", "livree")
    assert not ok and "etat_anterieur" in _reasons(entry)
    ok, entry = _one(scene, "branche:feat/vide", "livree")
    assert not ok and "vide" in _reasons(entry)
    assert _one(scene, "branche:feat/pr-fusionnee", "livree")[0]
    assert _one(scene, "branche:origin/feat/squash", "livree")[0]


@pytest.mark.parametrize("name", ["main~1", "feat/a..feat/b", "HEAD@{1}", "main:README.md", "-x", "upstream/main"])
def test_reference_de_branche_confinee(scene: Scene, name: str) -> None:
    ok, entry = _one(scene, f"branche:{name}", "a_faire")
    assert not ok and ("refusé" in _reasons(entry) or "introuvable" in _reasons(entry))


# ── worktree sale ──


@pytest.mark.parametrize("verdict", ["livree", "obsolete", "abandonnee"])
def test_un_worktree_sale_n_est_jamais_retire(scene: Scene, verdict: str) -> None:
    proofs: list[dict[str, Any]] = [{"type": "inactivite", "heures": 48}] if verdict == "abandonnee" else []
    if verdict == "obsolete":
        proofs = [{"type": "fichier", "chemin": "README.md"}]
    ok, entry = _one(scene, "branche:feat/sale", verdict, *proofs)
    assert not ok and "travail non commité" in _reasons(entry)
    assert not _one(scene, "pr:35", "abandonnee", {"type": "inactivite", "heures": 48})[0]


# ── inactivite ──


def test_abandon_exige_une_session_morte(scene: Scene) -> None:
    assert _one(scene, "pr:33", "abandonnee", {"type": "inactivite", "heures": 48})[0]  # session terminée
    ok, entry = _one(scene, "pr:30", "abandonnee", {"type": "inactivite", "heures": 48})
    assert not ok and "vivant" in _reasons(entry)
    # Sans signe de la sonde, la session 30 est « inconnue » : ce n'est pas « morte ».
    ok, entry = _one(scene, "pr:30", "abandonnee", {"type": "inactivite", "heures": 48}, alive=False)
    assert not ok and "« inconnu » n'est pas « morte »" in _reasons(entry)
    ok, entry = _one(scene, f"tache:{scene.tasks['sans_session']}", "abandonnee", {"type": "inactivite", "heures": 1})
    assert not ok


def test_abandon_sans_proprietaire(scene: Scene) -> None:
    """Sans tâche liée : seulement sans travail non commité et avec un dernier commit au-delà du seuil."""
    assert _one(scene, "pr:31", "abandonnee", {"type": "inactivite", "heures": 48})[0]  # commits anciens
    ok, entry = _one(scene, "branche:feat/hors-cible", "abandonnee", {"type": "inactivite", "heures": 1})
    assert not ok  # commit d'aujourd'hui
    ok, entry = _one(scene, "pr:32", "abandonnee", {"type": "inactivite", "heures": 48})
    assert not ok and "moins que les 48 h" in _reasons(entry)


# ── doublon_de ──


def test_doublon_de(scene: Scene) -> None:
    element = f"tache:{scene.tasks['citee']}"
    assert _one(scene, element, "doublon", {"type": "doublon_de", "element": f"tache:{scene.tasks['tenue']}"})[0]
    ok, entry = _one(scene, element, "doublon", {"type": "doublon_de", "element": "tache:GAO-inexistante-001"})
    assert not ok and "absente du ledger" in _reasons(entry)
    assert not _one(scene, element, "doublon", {"type": "doublon_de", "element": element})[0]


def test_doublon_cycle_cible_fermee_ou_jugee(scene: Scene) -> None:
    """B3 : A doublon de B et B doublon de A ; une cible annulée ; une cible jugée obsolète."""
    result = _run(
        scene,
        {"element": "issue:50", "verdict": "doublon", "preuves": [{"type": "doublon_de", "element": "issue:61"}]},
        {"element": "issue:61", "verdict": "doublon", "preuves": [{"type": "doublon_de", "element": "issue:50"}]},
    )
    assert result["resume"]["etablis"] == 0
    assert "jugé doublon" in _reasons(result["rejetes"][0])
    ok, entry = _one(scene, f"tache:{scene.tasks['citee']}", "doublon", {"type": "doublon_de", "element": f"tache:{scene.tasks['annulee']}"})
    assert not ok and "n'est plus ouvert" in _reasons(entry)
    ok, entry = _one(scene, "issue:50", "doublon", {"type": "doublon_de", "element": "issue:12"})
    assert not ok and "n'est plus ouvert" in _reasons(entry)
    result = _run(
        scene,
        {"element": "issue:50", "verdict": "doublon", "preuves": [{"type": "doublon_de", "element": "issue:61"}]},
        {"element": "issue:61", "verdict": "obsolete", "preuves": [{"type": "issue", "numero": 60}]},
    )
    assert "jugé obsolete" in _reasons(result["rejetes"][0])


# ── note, suffisance ──


@pytest.mark.parametrize("verdict", ["livree", "obsolete", "doublon", "abandonnee"])
def test_une_note_seule_ne_suffit_jamais(scene: Scene, verdict: str) -> None:
    ok, entry = _one(scene, "pr:31", verdict, {"type": "note", "texte": "je l'ai vu passer"})
    assert not ok and ("ne suffit jamais" in _reasons(entry) or "nomme l'élément" in _reasons(entry))


def test_a_faire_et_humain_sans_preuve(scene: Scene) -> None:
    assert _one(scene, "issue:50", "a_faire")[0]
    assert _one(scene, "issue:50", "humain", {"type": "note", "texte": "secret à poser"})[0]


# ── conflits recalculés ──


def test_conflits_recalcules(scene: Scene) -> None:
    assert _one(scene, "pr:31", "conflit_simple")[0]
    ok, entry = _one(scene, "pr:32", "conflit_simple")
    assert not ok and "conflit recalculé : autre" in _reasons(entry)
    assert _one(scene, "branche:feat/semantique", "conflit_semantique")[0]
    assert not _one(scene, "branche:feat/hors-cible", "conflit_semantique")[0]
    assert not _one(scene, "issue:50", "conflit_simple")[0]
    ok, entry = _one(scene, "pr:34", "conflit_simple")
    assert not ok and "absente du dépôt local" in _reasons(entry)


# ── forme ──


def test_element_inconnu_verdict_inconnu_doublon_de_ligne(scene: Scene) -> None:
    result = _run(
        scene,
        {"element": "pr:999", "verdict": "a_faire"},
        {"element": "branche:nulle-part", "verdict": "a_faire"},
        {"element": "ticket:1", "verdict": "a_faire"},
        {"element": "issue:50", "verdict": "fini"},
        {"element": f"tache:{scene.tasks['citee']}", "verdict": "a_faire"},
        {"element": f"tache:{scene.tasks['citee']}", "verdict": "a_faire"},
        {"element": "issue:50", "verdict": "a_faire", "preuves": [{"type": "intuition"}]},
        "pas un objet",
    )
    assert result["resume"] == {"etablis": 1, "rejetes": 7, "par_verdict": {"a_faire": 1}}
    reasons = " | ".join(_reasons(e) for e in result["rejetes"])
    for expected in ("introuvable", "mal formé", "verdict « fini » inconnu", "déjà jugé", "type de preuve inconnu", "pas un objet"):
        assert expected in reasons


def test_fichier_de_verdicts_illisible(tmp_path: Path) -> None:
    bad = tmp_path / "v.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(VerdictsFileError):
        load_verdicts(bad)
    bad.write_text(json.dumps({"$schema": "autre/v1", "verdicts": []}), encoding="utf-8")
    with pytest.raises(VerdictsFileError, match="schema"):
        load_verdicts(bad)


def test_magasin_d_objets_propre_a_chaque_execution(scene: Scene) -> None:
    src = scene.sources()
    with scratch_objects(src) as first, scratch_objects(src) as second:
        assert first and second
        assert first["GIT_OBJECT_DIRECTORY"] != second["GIT_OBJECT_DIRECTORY"]
        assert Path(first["GIT_OBJECT_DIRECTORY"]).is_dir()
    assert not Path(first["GIT_OBJECT_DIRECTORY"]).exists()


def test_la_validation_ne_fait_que_lire(scene: Scene) -> None:
    recorder = Recorder(scene.gh)
    _run(
        scene,
        {"element": "pr:31", "verdict": "conflit_simple"},
        {"element": "pr:33", "verdict": "abandonnee", "preuves": [{"type": "inactivite", "heures": 2}]},
        {"element": "branche:feat/sale", "verdict": "abandonnee", "preuves": [{"type": "inactivite", "heures": 2}]},
        {"element": f"tache:{scene.tasks['issue_fermee']}", "verdict": "livree", "preuves": [{"type": "pr", "numero": 40}]},
        recorder=recorder,
    )
    recorder.assert_read_only()
