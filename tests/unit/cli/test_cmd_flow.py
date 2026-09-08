"""Tests CLI pour ``grimoire flow`` (#204, ``--executor dispatch`` #311)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from textwrap import dedent

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


def test_flow_resume_interactive_without_result_fails(tmp_path: Path) -> None:
    """``--result`` reste requis en mode interactif — seul ``dispatch`` s'en passe."""
    bp = _write_blueprint(tmp_path)
    result = runner.invoke(app, ["--output", "json", "flow", "run", str(bp), "--project-root", str(tmp_path)])
    run_id = json.loads(result.output)["run_id"]

    result = runner.invoke(app, ["--output", "json", "flow", "resume", run_id, "--project-root", str(tmp_path)])
    assert result.exit_code == 1
    assert "--result" in json.loads(result.output)["error"]


# ── ``--executor dispatch`` : la cascade au bout de la CLI (#311) ───────────

_WRITER_SCRIPT = """\
    import re, json, sys
    prompt = sys.argv[1]
    path = re.search(r"Écris ta sortie dans le fichier (\\S+)", prompt).group(1)
    pins = {
        m.group(1): {"contract": m.group(2)}
        for m in re.finditer(r"^  - (\\S+) : (\\S+)$", prompt, re.MULTILINE)
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"pins": pins}, fh)
    """


def _write_dispatch_registry(tmp_path: Path) -> None:
    script = tmp_path / "writer.py"
    script.write_text(dedent(_WRITER_SCRIPT), encoding="utf-8")
    standard = tmp_path / "_grimoire" / "standard"
    standard.mkdir(parents=True, exist_ok=True)
    (standard / "llm-provider-registry.yaml").write_text(
        f"""\
$schema: "grimoire-llm-provider-registry/v1"
metadata:
  project: "demo"
  owner: ""
  policy: "No provider/model call outside this registry."
providers:
  - id: "writer"
    enabled: true
    provider_type: "hosted"
    allowed_capabilities: ["chat", "code"]
    default_models: ["writer-model"]
    currency: "api"
    invocation: "{sys.executable} {script} {{prompt}} --model {{model}}"
    models:
      - id: "writer-model"
        tier: "cheap"
    fallback_order: []
routing:
  default_provider: ""
  default_fallback_chain: []
  require_capability_match: true
  require_data_policy_match: true
""",
        encoding="utf-8",
    )


def _write_dispatchable_blueprint(tmp_path: Path) -> Path:
    blueprint = {
        "blueprintVersion": 1,
        "id": "cli-flow-dispatch",
        "name": "CLI flow dispatch",
        "nodes": [
            {
                "id": "a",
                "kind": "pattern",
                "ref": "ORC-01",
                "acceptance": ["la suite de tests passe"],
                "pins": [{"id": "out", "direction": "out", "contract": "c1"}],
            },
            {
                "id": "b",
                "kind": "pattern",
                "ref": "QUA-04",
                "acceptance": ["la suite de tests passe"],
                "pins": [{"id": "in", "direction": "in", "contract": "c1"}],
            },
        ],
        "edges": [{"from": "a.out", "to": "b.in", "contract": "c1"}],
    }
    path = tmp_path / "cli-flow-dispatch.blueprint.json"
    path.write_text(json.dumps(blueprint), encoding="utf-8")
    return path


def test_flow_run_executor_dispatch_enchaine_puis_status_montre_le_detail(tmp_path: Path) -> None:
    _write_dispatch_registry(tmp_path)
    bp = _write_dispatchable_blueprint(tmp_path)

    result = runner.invoke(
        app, ["--output", "json", "flow", "run", str(bp), "--executor", "dispatch", "--project-root", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    outcome = json.loads(result.output)
    assert outcome["status"] == "finished"
    assert [n["node_id"] for n in outcome["nodes"]] == ["a", "b"]
    assert outcome["total_cost_usd"] is None
    assert outcome["escalations"] == 0

    status = runner.invoke(
        app, ["--output", "json", "flow", "status", outcome["run_id"], "--project-root", str(tmp_path)]
    )
    assert status.exit_code == 0, status.output
    body = json.loads(status.output)
    assert body["status"] == "completed"
    assert [row["node_id"] for row in body["dispatch"]] == ["a", "b"]
    assert all(row["verdict"] == "green" for row in body["dispatch"])


def test_flow_resume_executor_dispatch_reprend_sans_result(tmp_path: Path) -> None:
    _write_dispatch_registry(tmp_path)
    bp = _write_dispatchable_blueprint(tmp_path)

    result = runner.invoke(
        app, ["--output", "json", "flow", "run", str(bp), "--executor", "interactive", "--project-root", str(tmp_path)]
    )
    run_id = json.loads(result.output)["run_id"]

    result = runner.invoke(
        app,
        ["--output", "json", "flow", "resume", run_id, "--executor", "dispatch", "--project-root", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    outcome = json.loads(result.output)
    assert outcome["status"] == "finished"
    assert [n["node_id"] for n in outcome["nodes"]] == ["a", "b"]


def test_flow_run_executor_dispatch_unknown_name_fails(tmp_path: Path) -> None:
    bp = _write_dispatchable_blueprint(tmp_path)
    result = runner.invoke(
        app, ["--output", "json", "flow", "run", str(bp), "--executor", "bogus", "--project-root", str(tmp_path)]
    )
    assert result.exit_code == 1
    assert "bogus" in json.loads(result.output)["error"]
