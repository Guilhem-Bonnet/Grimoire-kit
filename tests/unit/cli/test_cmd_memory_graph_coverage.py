"""``grimoire memory graph coverage`` (#275) — un appelant réel pour `CodeGraph.uncovered_nodes`.

Avant cette passe, `uncovered_nodes` n'avait ni appelant ni test. Ce test
échoue si la commande qui l'appelle disparaît.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.cli.cmd_memory import memory_app
from grimoire.cli.cmd_memory_projections import graph_app  # noqa: F401 -- enregistre "coverage" sur import

runner = CliRunner()


@pytest.fixture
def projet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "project-context.yaml").write_text("project:\n  name: demo\n", encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    (src / "lib.py").write_text(
        "def couverte():\n    return 1\n\n\ndef non_couverte():\n    return 2\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_graph_coverage_liste_les_noeuds_sans_tested_by(projet: Path) -> None:
    res = runner.invoke(memory_app, ["graph", "coverage", "--paths", "src"])

    assert res.exit_code == 0, res.output
    assert "non_couverte" in res.output


def test_graph_coverage_json(projet: Path) -> None:
    res = runner.invoke(app, ["--output", "json", "memory", "graph", "coverage", "--paths", "src"])

    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["total_nodes"] >= 2
    assert any("non_couverte" in node_id for node_id in data["uncovered"])
