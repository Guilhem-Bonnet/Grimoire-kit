"""Tests du diff structurel de blueprints (P3.4)."""

from __future__ import annotations

from typing import Any

import pytest

from grimoire.tools.blueprint_diff import diff_blueprints, edge_key, summarize


def bp(nodes: list[dict[str, Any]], edges: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"nodes": nodes, "edges": edges or []}


def test_deplacer_un_node_n_est_pas_un_changement() -> None:
    """Ranger le graphe ne doit rien faire apparaître : c'est tout l'enjeu."""
    avant = bp([{"id": "a", "ref": "QUA-04", "x": 10, "y": 20}])
    apres = bp([{"id": "a", "ref": "QUA-04", "x": 900, "y": 640}])
    d = diff_blueprints(avant, apres)
    assert d["changed"] is False
    assert d["summary"] == "aucun changement de structure"


def test_node_ajoute_et_retire() -> None:
    avant = bp([{"id": "a", "ref": "QUA-04"}])
    apres = bp([{"id": "b", "ref": "QUA-05"}])
    d = diff_blueprints(avant, apres)
    assert [n["id"] for n in d["nodes_added"]] == ["b"]
    assert [n["id"] for n in d["nodes_removed"]] == ["a"]
    assert d["changed"] is True


def test_node_modifie_liste_les_champs_porteurs_de_sens() -> None:
    avant = bp([{"id": "a", "ref": "QUA-04", "name": "preuve", "x": 0}])
    apres = bp([{"id": "a", "ref": "QUA-05", "name": "porte", "x": 500}])
    d = diff_blueprints(avant, apres)
    assert d["nodes_changed"][0]["fields"] == ["name", "ref"]  # x exclu


def test_lien_retire_est_vu_meme_sans_identifiant() -> None:
    """Le schéma ne donne pas d'id aux liens : l'identité est le quadruplet."""
    e = {"from": "a", "to": "b", "contract": "evidence-pack"}
    d = diff_blueprints(bp([], [e]), bp([], []))
    assert d["edges_removed"][0]["contract"] == "evidence-pack"
    assert d["edges_added"] == []


def test_changer_le_canal_d_un_lien_est_un_ajout_et_un_retrait() -> None:
    """Passer un lien de `happy` à `failure` change le chemin, pas l'étiquette."""
    avant = bp([], [{"from": "a", "to": "b", "contract": "x", "channel": "happy"}])
    apres = bp([], [{"from": "a", "to": "b", "contract": "x", "channel": "failure"}])
    d = diff_blueprints(avant, apres)
    assert d["edges_removed"][0]["channel"] == "happy"
    assert d["edges_added"][0]["channel"] == "failure"


def test_canal_absent_vaut_happy() -> None:
    assert edge_key({"from": "a", "to": "b", "contract": "c"})[3] == "happy"


def test_descend_dans_les_sous_flows() -> None:
    avant = {"nodes": [{"id": "g", "kind": "group", "sub": {"nodes": [], "edges": []}}], "edges": []}
    apres = {
        "nodes": [{"id": "g", "kind": "group", "sub": {"nodes": [{"id": "in", "ref": "QUA-05"}], "edges": []}}],
        "edges": [],
    }
    d = diff_blueprints(avant, apres)
    assert d["nodes_added"][0]["id"] == "in"
    assert d["nodes_added"][0]["path"] == ["g"]


def test_un_groupe_n_est_pas_modifie_par_le_contenu_de_son_sous_flow() -> None:
    """Sinon le vrai changement serait noyé sous un « groupe modifié »."""
    avant = {"nodes": [{"id": "g", "kind": "group", "sub": {"nodes": [], "edges": []}}], "edges": []}
    apres = {
        "nodes": [{"id": "g", "kind": "group", "sub": {"nodes": [{"id": "in"}], "edges": []}}],
        "edges": [],
    }
    d = diff_blueprints(avant, apres)
    assert d["nodes_changed"] == []


def test_resume_lisible() -> None:
    d = diff_blueprints(bp([{"id": "a"}]), bp([{"id": "a"}, {"id": "b"}, {"id": "c"}]))
    assert summarize(d) == "2 nodes ajoutés"


@pytest.mark.parametrize("valeur", [None, [], "texte", 42])
def test_entrees_malformees_ne_font_pas_tomber(valeur: Any) -> None:
    """Un blueprint importé peut être n'importe quoi ; le diff doit tenir."""
    d = diff_blueprints({"nodes": valeur, "edges": valeur}, {"nodes": valeur, "edges": valeur})
    assert d["changed"] is False


def test_blueprint_vide_contre_blueprint_peuple() -> None:
    d = diff_blueprints({}, bp([{"id": "a", "ref": "ORC-02"}], [{"from": "a", "to": "b", "contract": "t"}]))
    assert len(d["nodes_added"]) == 1
    assert len(d["edges_added"]) == 1


def test_diff_contre_une_reference_git(tmp_path: Any) -> None:
    """Bout en bout : un blueprint commité, modifié, puis comparé à HEAD."""
    import json
    import subprocess

    from grimoire.tools.blueprint_diff import diff_against_ref

    root = tmp_path
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "t"],
    ):
        subprocess.run(cmd, cwd=root, check=True, capture_output=True)
    bp_path = root / "flow.blueprint.json"
    bp_path.write_text(json.dumps(bp([{"id": "a", "ref": "QUA-04"}])), encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True, capture_output=True)

    # déplacer ne change rien…
    bp_path.write_text(
        json.dumps(bp([{"id": "a", "ref": "QUA-04", "x": 800}])), encoding="utf-8"
    )
    assert diff_against_ref(root, bp_path)["changed"] is False

    # …ajouter une porte, si.
    bp_path.write_text(
        json.dumps(bp([{"id": "a", "ref": "QUA-04"}, {"id": "b", "ref": "QUA-05"}])),
        encoding="utf-8",
    )
    d = diff_against_ref(root, bp_path)
    assert d["tracked"] is True
    assert [n["id"] for n in d["nodes_added"]] == ["b"]


def test_blueprint_jamais_commite_n_est_pas_un_ajout_massif(tmp_path: Any) -> None:
    """Sans version de référence, on ne prétend pas que tout vient d'être créé."""
    import json
    import subprocess

    from grimoire.tools.blueprint_diff import diff_against_ref

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    p = tmp_path / "neuf.blueprint.json"
    p.write_text(json.dumps(bp([{"id": "a"}])), encoding="utf-8")
    d = diff_against_ref(tmp_path, p)
    assert d["tracked"] is False
    assert d["summary"] == "jamais commité — rien à comparer"
