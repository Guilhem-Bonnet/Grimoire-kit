"""Les verdicts de l'intendant (#876, lot 5) : chaque preuve acceptée quand elle est vraie, rejetée quand elle est fausse."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from steward_doubles import Recorder, Scene

from grimoire.steward import VERDICTS_SCHEMA
from grimoire.steward.verdicts import VerdictsFileError, load_verdicts, validate


def _run(scene: Scene, *verdicts: dict[str, Any], alive: bool = True, recorder: Recorder | None = None) -> dict[str, Any]:
    return validate(scene.sources(recorder, alive=alive), {"$schema": VERDICTS_SCHEMA, "verdicts": list(verdicts)})


def _one(scene: Scene, element: str, verdict: str, *proofs: dict[str, Any], alive: bool = True) -> tuple[bool, dict[str, Any]]:
    result = _run(scene, {"element": element, "verdict": verdict, "preuves": list(proofs)}, alive=alive)
    if result["etablis"]:
        return True, result["etablis"][0]
    return False, result["rejetes"][0]


def _reasons(entry: dict[str, Any]) -> str:
    return " | ".join(entry.get("raisons") or [])


# ── commit ──


def test_commit_sur_la_cible_accepte(scene: Scene) -> None:
    ok, entry = _one(scene, f"tache:{scene.tasks['citee']}", "livree", {"type": "commit", "sha": scene.shas["cite"][:9]})
    assert ok, _reasons(entry)
    assert entry["preuves"][0]["mesure"]["sha"] == scene.shas["cite"]


def test_commit_absent_rejete(scene: Scene) -> None:
    ok, entry = _one(scene, f"tache:{scene.tasks['citee']}", "livree", {"type": "commit", "sha": "deadbeef1"})
    assert not ok and "absent du dépôt local" in _reasons(entry)


def test_commit_hors_cible_rejete(scene: Scene) -> None:
    ok, entry = _one(scene, f"tache:{scene.tasks['citee']}", "livree", {"type": "commit", "sha": scene.shas["hors_cible"]})
    assert not ok and "pas sur la cible" in _reasons(entry)


def test_commit_dont_le_message_ne_cite_pas_ce_qu_on_dit(scene: Scene) -> None:
    proof = {"type": "commit", "sha": scene.shas["cite"], "message_contient": "GAO-homonyme-001"}
    ok, entry = _one(scene, f"tache:{scene.tasks['citee']}", "livree", proof)
    assert not ok and "ne contient pas" in _reasons(entry)


# ── fichier ──


def test_fichier_present_accepte_absent_rejete(scene: Scene) -> None:
    element = f"tache:{scene.tasks['citee']}"
    assert _one(scene, element, "livree", {"type": "fichier", "chemin": "c.txt"})[0]
    ok, entry = _one(scene, element, "livree", {"type": "fichier", "chemin": "jamais.txt"})
    assert not ok and "absent de la cible" in _reasons(entry)
    # Un fichier qui n'existe que sur une branche n'est pas sur la cible.
    assert not _one(scene, element, "livree", {"type": "fichier", "chemin": "h.txt"})[0]


def test_fichier_motif(scene: Scene) -> None:
    element = f"tache:{scene.tasks['citee']}"
    assert _one(scene, element, "livree", {"type": "fichier", "chemin": "src/app.py", "motif": "return 3"})[0]
    ok, entry = _one(scene, element, "livree", {"type": "fichier", "chemin": "src/app.py", "motif": "return 2"})
    assert not ok and "motif absent" in _reasons(entry)


def test_fichier_absent_attendu(scene: Scene) -> None:
    element = f"tache:{scene.tasks['citee']}"
    assert _one(scene, element, "obsolete", {"type": "fichier", "chemin": "retire.py", "absent": True})[0]
    assert not _one(scene, element, "obsolete", {"type": "fichier", "chemin": "c.txt", "absent": True})[0]


@pytest.mark.parametrize("chemin", ["/etc/passwd", "../dehors", "-x", "a\\b"])
def test_fichier_chemin_invalide(scene: Scene, chemin: str) -> None:
    ok, entry = _one(scene, f"tache:{scene.tasks['citee']}", "livree", {"type": "fichier", "chemin": chemin})
    assert not ok and "invalide" in _reasons(entry)


# ── issue / pr ──


def test_issue_fermee_acceptee_ouverte_rejetee(scene: Scene) -> None:
    element = f"tache:{scene.tasks['issue_fermee']}"
    assert _one(scene, element, "obsolete", {"type": "issue", "numero": 12})[0]
    ok, entry = _one(scene, element, "obsolete", {"type": "issue", "numero": 50})
    assert not ok and "#50 est ouverte, attendu fermee" in _reasons(entry)
    ok, entry = _one(scene, element, "obsolete", {"type": "issue", "numero": 999})
    assert not ok and "non mesuré" in _reasons(entry)


def test_pr_fusionnee_sur_la_cible(scene: Scene) -> None:
    element = f"tache:{scene.tasks['citee']}"
    ok, entry = _one(scene, element, "livree", {"type": "pr", "numero": 40})
    assert ok, _reasons(entry)
    ok, entry = _one(scene, element, "livree", {"type": "pr", "numero": 31})
    assert not ok and "PR #31 est ouverte, attendu fusionnee" in _reasons(entry)
    # Fusionnée selon gh, mais son commit de fusion n'est pas sur la cible : rejetée.
    ok, entry = _one(scene, element, "livree", {"type": "pr", "numero": 41})
    assert not ok and "pas vérifié sur la cible" in _reasons(entry)


# ── inactivite ──


def test_inactivite_recalculee(scene: Scene) -> None:
    assert _one(scene, "pr:31", "abandonnee", {"type": "inactivite", "heures": 48})[0]
    ok, entry = _one(scene, "pr:32", "abandonnee", {"type": "inactivite", "heures": 48})
    assert not ok and "moins que les 48 h" in _reasons(entry)
    # PR 30 : vieille, mais la session de sa tâche est vivante.
    ok, entry = _one(scene, "pr:30", "abandonnee", {"type": "inactivite", "heures": 48})
    assert not ok and "encore vivante" in _reasons(entry)
    assert _one(scene, "pr:30", "abandonnee", {"type": "inactivite", "heures": 48}, alive=False)[0]


def test_inactivite_d_une_branche_et_d_une_tache(scene: Scene) -> None:
    assert _one(scene, "branche:feat/hors-cible", "abandonnee", {"type": "inactivite", "heures": 1})[0] is False  # commit d'aujourd'hui
    ok, entry = _one(scene, f"tache:{scene.tasks['tenue']}", "abandonnee", {"type": "inactivite", "heures": 1})
    assert not ok and "encore vivante" in _reasons(entry)


# ── doublon_de, note, suffisance ──


def test_doublon_de(scene: Scene) -> None:
    element = f"tache:{scene.tasks['citee']}"
    assert _one(scene, element, "doublon", {"type": "doublon_de", "element": f"tache:{scene.tasks['tenue']}"})[0]
    ok, entry = _one(scene, element, "doublon", {"type": "doublon_de", "element": "tache:GAO-inexistante-001"})
    assert not ok and "absente du ledger" in _reasons(entry)
    assert not _one(scene, element, "doublon", {"type": "doublon_de", "element": element})[0]


@pytest.mark.parametrize("verdict", ["livree", "obsolete", "doublon", "abandonnee"])
def test_une_note_seule_ne_suffit_jamais(scene: Scene, verdict: str) -> None:
    ok, entry = _one(scene, "pr:31", verdict, {"type": "note", "texte": "je l'ai vu passer"})
    assert not ok and "une note seule ne suffit jamais" in _reasons(entry)


def test_a_faire_et_humain_sans_preuve(scene: Scene) -> None:
    assert _one(scene, "issue:50", "a_faire")[0]
    assert _one(scene, "issue:50", "humain", {"type": "note", "texte": "secret à poser"})[0]


def test_livree_sur_une_issue_ouverte_exige_l_etat_ferme(scene: Scene) -> None:
    ok, entry = _one(scene, "issue:50", "livree", {"type": "issue", "numero": 50, "etat": "ouverte"})
    assert not ok and "exige une preuve" in _reasons(entry)


# ── conflits recalculés ──


def test_conflits_recalcules(scene: Scene) -> None:
    assert _one(scene, "pr:31", "conflit_simple")[0]
    ok, entry = _one(scene, "pr:32", "conflit_simple")
    assert not ok and "conflit recalculé : autre" in _reasons(entry)
    assert _one(scene, "branche:feat/semantique", "conflit_semantique")[0]
    assert not _one(scene, "branche:feat/hors-cible", "conflit_semantique")[0]
    assert not _one(scene, "issue:50", "conflit_simple")[0]


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


def test_la_validation_ne_fait_que_lire(scene: Scene) -> None:
    recorder = Recorder(scene.gh)
    _run(
        scene,
        {"element": "pr:31", "verdict": "conflit_simple"},
        {"element": "pr:31", "verdict": "abandonnee", "preuves": [{"type": "inactivite", "heures": 2}]},
        {"element": f"tache:{scene.tasks['citee']}", "verdict": "livree", "preuves": [{"type": "pr", "numero": 40}]},
        recorder=recorder,
    )
    recorder.assert_read_only()
