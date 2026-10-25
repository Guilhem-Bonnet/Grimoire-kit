"""R04 : `grimoire blueprint import --from …` rend les trois adaptateurs atteignables."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.cli.cmd_blueprint import blueprint_app

runner = CliRunner()

_SCHEMA = {"type": "object"}

DEFINITIONS: dict[str, dict[str, Any]] = {
    "crewai": {
        "name": "mon flow",
        "tasks": [
            {"id": "t1", "name": "Recherche", "description": "chercher", "output_schema": {"type": "string"}},
            {"id": "t2", "name": "Rédaction", "depends_on": ["t1"], "output_schema": {"type": "string"}},
        ],
        "output_schema": _SCHEMA,
    },
    "langgraph": {
        "name": "mon graphe",
        "nodes": [{"id": "n1", "name": "Un"}, {"id": "n2", "name": "Deux"}],
        "edges": [{"from": "n1", "to": "n2"}],
        "output_schema": _SCHEMA,
    },
    "gascity": {
        "name": "ma formule",
        "molecules": [{"id": "m1", "name": "Un"}, {"id": "m2", "name": "Deux"}],
        "output_schema": _SCHEMA,
    },
}


@pytest.mark.parametrize("source", sorted(DEFINITIONS))
def test_l_import_produit_un_blueprint_experimental_que_validate_relit(tmp_path: Path, source: str) -> None:
    entree = tmp_path / "ext.json"
    entree.write_text(json.dumps(DEFINITIONS[source]), encoding="utf-8")
    sortie = tmp_path / "out.blueprint.json"

    res = runner.invoke(blueprint_app, ["import", str(entree), "--from", source, "--out", str(sortie)])
    assert res.exit_code == 0, res.output

    blueprint = json.loads(sortie.read_text(encoding="utf-8"))
    assert blueprint["status"] == "experimental"
    assert len(blueprint["nodes"]) == 2
    assert blueprint["meta"]["importedFrom"] == source

    verdict = runner.invoke(blueprint_app, ["validate", str(sortie), "--allow-skipped-schema"])
    assert verdict.exit_code == 0, verdict.output


def test_un_import_sans_schema_de_sortie_est_refuse_et_n_ecrit_rien(tmp_path: Path) -> None:
    entree = tmp_path / "ext.json"
    entree.write_text(json.dumps({"name": "x", "tasks": [{"id": "t1", "name": "T"}]}), encoding="utf-8")
    sortie = tmp_path / "out.blueprint.json"
    res = runner.invoke(blueprint_app, ["import", str(entree), "--from", "crewai", "--out", str(sortie)])
    assert res.exit_code == 1
    assert not sortie.exists()


def test_une_origine_inconnue_est_refusee(tmp_path: Path) -> None:
    entree = tmp_path / "ext.json"
    entree.write_text("{}", encoding="utf-8")
    res = runner.invoke(blueprint_app, ["import", str(entree), "--from", "autre"])
    assert res.exit_code == 2


def test_on_n_ecrase_pas_sans_force(tmp_path: Path) -> None:
    entree = tmp_path / "ext.json"
    entree.write_text(json.dumps(DEFINITIONS["gascity"]), encoding="utf-8")
    sortie = tmp_path / "out.blueprint.json"
    sortie.write_text("{}", encoding="utf-8")
    res = runner.invoke(blueprint_app, ["import", str(entree), "--from", "gascity", "--out", str(sortie)])
    assert res.exit_code == 1
    assert sortie.read_text(encoding="utf-8") == "{}"
