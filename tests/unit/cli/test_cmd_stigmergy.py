"""Tests for grimoire.cli.cmd_stigmergy — CLI coordination stigmergique."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from grimoire.cli.app import app

runner = CliRunner()


def _emit(project: Path, ptype: str, location: str, agent: str, text: str = "t") -> None:
    result = runner.invoke(app, [
        "stigmergy", "emit", "--type", ptype, "--location", location,
        "--text", text, "--agent", agent, "--project-root", str(project),
    ])
    assert result.exit_code == 0, result.output


def test_emit_then_sense(tmp_path: Path) -> None:
    _emit(tmp_path, "NEED", "src/auth", "dev", "review requise")
    result = runner.invoke(app, ["stigmergy", "sense", "--project-root", str(tmp_path)])
    assert result.exit_code == 0
    assert "NEED" in result.output
    assert "src/auth" in result.output


def test_emit_writes_board(tmp_path: Path) -> None:
    _emit(tmp_path, "ALERT", "src/db", "architect", "breaking change")
    board = tmp_path / "_grimoire-output" / "pheromone-board.json"
    assert board.is_file()
    data = json.loads(board.read_text(encoding="utf-8"))
    assert data["pheromones"][0]["pheromone_type"] == "ALERT"


def test_invalid_type_exits_nonzero(tmp_path: Path) -> None:
    result = runner.invoke(app, [
        "stigmergy", "emit", "--type", "FOO", "--location", "x",
        "--agent", "z", "--project-root", str(tmp_path),
    ])
    assert result.exit_code == 2
    assert "Type inconnu" in result.output


def test_sense_json(tmp_path: Path) -> None:
    _emit(tmp_path, "PROGRESS", "src/api", "dev", "wip")
    result = runner.invoke(app, [
        "stigmergy", "sense", "--json", "--project-root", str(tmp_path),
    ])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload[0]["pheromone_type"] == "PROGRESS"
    assert "current_intensity" in payload[0]


def test_trails_detects_convergence(tmp_path: Path) -> None:
    _emit(tmp_path, "NEED", "src/auth", "dev")
    _emit(tmp_path, "ALERT", "src/auth", "qa")
    result = runner.invoke(app, ["stigmergy", "trails", "--project-root", str(tmp_path)])
    assert result.exit_code == 0
    assert "convergence" in result.output


def test_amplify_resolve_evaporate(tmp_path: Path) -> None:
    _emit(tmp_path, "BLOCK", "src/build", "ops", "pipeline cassé")
    sense = runner.invoke(app, [
        "stigmergy", "sense", "--json", "--project-root", str(tmp_path),
    ])
    ph_id = json.loads(sense.output)[0]["pheromone_id"]

    amp = runner.invoke(app, [
        "stigmergy", "amplify", "--id", ph_id, "--agent", "lead",
        "--project-root", str(tmp_path),
    ])
    assert amp.exit_code == 0

    res = runner.invoke(app, [
        "stigmergy", "resolve", "--id", ph_id, "--agent", "lead",
        "--project-root", str(tmp_path),
    ])
    assert res.exit_code == 0

    evap = runner.invoke(app, ["stigmergy", "evaporate", "--project-root", str(tmp_path)])
    assert evap.exit_code == 0
    # le signal résolu est purgé
    sense2 = runner.invoke(app, [
        "stigmergy", "sense", "--json", "--project-root", str(tmp_path),
    ])
    assert json.loads(sense2.output or "[]") == []


def test_amplify_unknown_id_exits_one(tmp_path: Path) -> None:
    result = runner.invoke(app, [
        "stigmergy", "amplify", "--id", "PH-nope", "--agent", "x",
        "--project-root", str(tmp_path),
    ])
    assert result.exit_code == 1


def test_stats(tmp_path: Path) -> None:
    _emit(tmp_path, "NEED", "src/a", "dev")
    result = runner.invoke(app, ["stigmergy", "stats", "--project-root", str(tmp_path)])
    assert result.exit_code == 0
    assert "Actifs" in result.output
