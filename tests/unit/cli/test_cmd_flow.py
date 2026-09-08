"""Tests CLI pour ``grimoire flow`` (#204)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from grimoire.cli.app import app

runner = CliRunner()


def _write_blueprint(tmp_path: Path) -> Path:
    blueprint = {
        "blueprintVersion": 1,
        "id": "cli-flow",
        "name": "CLI flow",
        "nodes": [
            {
                "id": "a",
                "kind": "pattern",
                "ref": "ORC-01",
                "pins": [{"id": "out", "direction": "out", "contract": "c1"}],
            },
            {
                "id": "b",
                "kind": "pattern",
                "ref": "QUA-04",
                "pins": [{"id": "in", "direction": "in", "contract": "c1"}],
            },
        ],
        "edges": [{"from": "a.out", "to": "b.in", "contract": "c1"}],
    }
    path = tmp_path / "cli-flow.blueprint.json"
    path.write_text(json.dumps(blueprint), encoding="utf-8")
    return path


def test_flow_run_then_resume_then_status_json(tmp_path: Path) -> None:
    bp = _write_blueprint(tmp_path)

    result = runner.invoke(app, ["--output", "json", "flow", "run", str(bp), "--project-root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    started = json.loads(result.output)
    run_id = started["run_id"]
    assert started["contract"]["node_id"] == "a"

    output_file = tmp_path / "a-output.json"
    output_file.write_text(json.dumps({"pins": {"out": {"contract": "c1"}}}), encoding="utf-8")
    result = runner.invoke(
        app,
        ["--output", "json", "flow", "resume", run_id, "--result", str(output_file), "--project-root", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    resumed = json.loads(result.output)
    assert resumed["finished"] is False
    assert resumed["node_id"] == "b"

    # 'b' ne porte aucune pin de sortie : une sortie vide la satisfait, et
    # c'est le dernier node — le run se termine.
    b_output_file = tmp_path / "b-output.json"
    b_output_file.write_text(json.dumps({"pins": {}}), encoding="utf-8")
    result = runner.invoke(
        app,
        ["--output", "json", "flow", "resume", run_id, "--result", str(b_output_file), "--project-root", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    resumed = json.loads(result.output)
    assert resumed["finished"] is True

    result = runner.invoke(app, ["--output", "json", "flow", "status", run_id, "--project-root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    status = json.loads(result.output)
    assert status["status"] == "completed"
    assert status["completed_nodes"] == ["a", "b"]


def test_flow_resume_refuses_non_conforming_output_with_nonzero_exit(tmp_path: Path) -> None:
    bp = _write_blueprint(tmp_path)
    result = runner.invoke(app, ["--output", "json", "flow", "run", str(bp), "--project-root", str(tmp_path)])
    run_id = json.loads(result.output)["run_id"]

    bad_output = tmp_path / "bad-output.json"
    bad_output.write_text(json.dumps({"pins": {"out": {"contract": "faux"}}}), encoding="utf-8")
    result = runner.invoke(
        app,
        ["--output", "json", "flow", "resume", run_id, "--result", str(bad_output), "--project-root", str(tmp_path)],
    )
    assert result.exit_code == 1
    body = json.loads(result.output)
    assert any("pin=out" in f for f in body["faults"])


def test_flow_abort(tmp_path: Path) -> None:
    bp = _write_blueprint(tmp_path)
    result = runner.invoke(app, ["--output", "json", "flow", "run", str(bp), "--project-root", str(tmp_path)])
    run_id = json.loads(result.output)["run_id"]

    result = runner.invoke(
        app, ["--output", "json", "flow", "abort", run_id, "--reason", "test", "--project-root", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["status"] == "aborted"


def test_flow_run_without_argument_lists_runs(tmp_path: Path) -> None:
    bp = _write_blueprint(tmp_path)
    runner.invoke(app, ["--output", "json", "flow", "run", str(bp), "--project-root", str(tmp_path)])

    result = runner.invoke(app, ["--output", "json", "flow", "run", "--project-root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    runs = json.loads(result.output)
    assert len(runs) == 1
    assert runs[0]["blueprint_id"] == "cli-flow"
