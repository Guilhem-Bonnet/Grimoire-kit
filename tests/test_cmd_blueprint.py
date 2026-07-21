"""Tests for grimoire.cli.cmd_blueprint — CLI blueprints (new / validate / compile)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from typer.testing import CliRunner

from grimoire.cli.cmd_blueprint import blueprint_app

runner = CliRunner()

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "registry" / "blueprints"


# ── new ───────────────────────────────────────────────────────────────────────


def test_new_minimal_then_validate_ok(tmp_path: Path) -> None:
    out = tmp_path / "demo.blueprint.json"
    result = runner.invoke(blueprint_app, ["new", "demo", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "Created" in result.output
    assert "grimoire blueprint validate" in result.output
    assert "grimoire ext publish" in result.output

    blueprint = json.loads(out.read_text(encoding="utf-8"))
    assert blueprint["blueprintVersion"] == 1
    assert blueprint["id"] == "demo"

    result = runner.invoke(blueprint_app, ["validate", str(out)])
    assert result.exit_code == 0, result.output
    assert "Valid" in result.output


def test_new_pipeline_template_validates(tmp_path: Path) -> None:
    out = tmp_path / "pipe.blueprint.json"
    result = runner.invoke(blueprint_app, ["new", "pipe", "--out", str(out), "--template", "pipeline"])
    assert result.exit_code == 0, result.output
    blueprint = json.loads(out.read_text(encoding="utf-8"))
    assert len(blueprint["nodes"]) == 3
    assert len(blueprint["edges"]) == 2

    result = runner.invoke(blueprint_app, ["validate", str(out)])
    assert result.exit_code == 0, result.output


def test_new_invalid_id_fails(tmp_path: Path) -> None:
    result = runner.invoke(blueprint_app, ["new", "Bad_Id", "--out", str(tmp_path / "x.json")])
    assert result.exit_code == 1
    assert "invalid blueprint id" in result.output


def test_new_unknown_template_fails(tmp_path: Path) -> None:
    result = runner.invoke(
        blueprint_app, ["new", "demo", "--out", str(tmp_path / "x.json"), "--template", "ghost"]
    )
    assert result.exit_code == 1
    assert "unknown template" in result.output


def test_new_refuses_overwrite_without_force(tmp_path: Path) -> None:
    out = tmp_path / "demo.blueprint.json"
    assert runner.invoke(blueprint_app, ["new", "demo", "--out", str(out)]).exit_code == 0
    result = runner.invoke(blueprint_app, ["new", "demo", "--out", str(out)])
    assert result.exit_code == 1
    assert "--force" in result.output
    assert runner.invoke(blueprint_app, ["new", "demo", "--out", str(out), "--force"]).exit_code == 0


# ── validate ──────────────────────────────────────────────────────────────────


def test_validate_broken_blueprint_reports_actionable_errors(tmp_path: Path) -> None:
    broken = {
        "blueprintVersion": 2,
        "id": "Bad Id",
        "nodes": [
            {"id": "a", "kind": "pattern", "ref": "not-a-pattern", "pins": []},
            {
                "id": "a",
                "kind": "ghost-kind",
                "ref": "x",
                "pins": [{"id": "out", "direction": "sideways", "contract": ""}],
            },
        ],
        "edges": [{"from": "a.out", "to": "ghost.in", "contract": "task-envelope"}],
    }
    path = tmp_path / "broken.blueprint.json"
    path.write_text(json.dumps(broken), encoding="utf-8")

    result = runner.invoke(blueprint_app, ["validate", str(path)])
    assert result.exit_code == 1
    # File-level checks (validate_blueprint_file) surface field + expectation.
    assert "blueprintVersion" in result.output
    assert "attendu : l'entier 1" in result.output
    # Structural checks carry a JSON path, an expectation and a fix per line.
    assert "$.nodes[1].id" in result.output
    assert "duplicate node id" in result.output
    assert "$.nodes[1].kind" in result.output
    assert "$.edges[0].to" in result.output
    assert "does not resolve to a declared pin" in result.output
    assert "fix:" in result.output


def test_validate_detects_contract_mismatch_and_cycle(tmp_path: Path) -> None:
    blueprint = {
        "blueprintVersion": 1,
        "id": "cyclic",
        "nodes": [
            {
                "id": "a",
                "kind": "pattern",
                "ref": "ORC-01",
                "pins": [
                    {"id": "in", "direction": "in", "contract": "task-envelope"},
                    {"id": "out", "direction": "out", "contract": "handoff-packet"},
                ],
            },
            {
                "id": "b",
                "kind": "pattern",
                "ref": "GOV-01",
                "pins": [
                    {"id": "in", "direction": "in", "contract": "task-envelope"},
                    {"id": "out", "direction": "out", "contract": "task-envelope"},
                ],
            },
        ],
        "edges": [
            {"from": "a.out", "to": "b.in"},
            {"from": "b.out", "to": "a.in"},
        ],
    }
    path = tmp_path / "cyclic.blueprint.json"
    path.write_text(json.dumps(blueprint), encoding="utf-8")

    result = runner.invoke(blueprint_app, ["validate", str(path)])
    assert result.exit_code == 1
    assert "pin contracts differ" in result.output
    assert "cycle detected" in result.output


def test_validate_warns_when_no_node_has_pins(tmp_path: Path) -> None:
    blueprint = {
        "blueprintVersion": 1,
        "id": "draft",
        "nodes": [{"id": "a", "kind": "pattern", "ref": "ORC-01"}],
        "edges": [],
    }
    path = tmp_path / "draft.blueprint.json"
    path.write_text(json.dumps(blueprint), encoding="utf-8")

    result = runner.invoke(blueprint_app, ["validate", str(path)])
    assert result.exit_code == 1
    assert "Studio draft" in result.output


def test_registry_examples_pass_validate() -> None:
    for name in ("minimal.blueprint.json", "web-pipeline.blueprint.json"):
        example = EXAMPLES / name
        assert example.is_file(), f"missing versioned example: {example}"
        result = runner.invoke(blueprint_app, ["validate", str(example)])
        assert result.exit_code == 0, f"{name}:\n{result.output}"
        assert "Valid" in result.output


def test_validate_falls_back_when_jsonschema_missing(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "demo.blueprint.json"
    assert runner.invoke(blueprint_app, ["new", "demo", "--out", str(out)]).exit_code == 0
    # `sys.modules[name] = None` makes `import jsonschema` raise ImportError.
    monkeypatch.setitem(sys.modules, "jsonschema", None)

    result = runner.invoke(blueprint_app, ["validate", str(out)])
    assert result.exit_code == 0, result.output
    assert "skipped (optional package jsonschema is not installed)" in result.output
    assert "Valid" in result.output


# ── compile ───────────────────────────────────────────────────────────────────


def test_compile_minimal_writes_mission_pack(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    result = runner.invoke(
        blueprint_app,
        ["compile", str(EXAMPLES / "minimal.blueprint.json"), "--project-root", str(project)],
    )
    assert result.exit_code == 0, result.output
    assert "Compiled: minimal" in result.output
    artifact = project / ".github" / "prompts" / "minimal.blueprint.prompt.md"
    assert artifact.is_file()
    saved = project / "_grimoire" / "blueprints" / "minimal.blueprint.json"
    assert saved.is_file()
    assert "compiled" in json.loads(saved.read_text(encoding="utf-8"))


def test_compile_blocked_lists_blockers_with_remediation(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    result = runner.invoke(
        blueprint_app,
        ["compile", str(EXAMPLES / "web-pipeline.blueprint.json"), "--project-root", str(project)],
    )
    # Fail-closed: crewai/langgraph are not installed in the empty project.
    assert result.exit_code == 1
    assert "Compilation blocked" in result.output
    assert "crewai" in result.output
    assert "grimoire ext add crewai" in result.output
    assert "grimoire ext add langgraph" in result.output
    # No mission pack written.
    assert not (project / ".github" / "prompts").exists()


def test_compile_invalid_file_fails_before_simulation(tmp_path: Path) -> None:
    path = tmp_path / "bad.blueprint.json"
    path.write_text(json.dumps({"blueprintVersion": 1, "id": "bad"}), encoding="utf-8")
    result = runner.invoke(blueprint_app, ["compile", str(path), "--project-root", str(tmp_path)])
    assert result.exit_code == 1
    assert "before compiling" in result.output


# ── ext_manager plumbing exposed to the CLI ───────────────────────────────────


def test_install_blueprint_reports_exact_install_commands(tmp_path: Path) -> None:
    from grimoire.tools.ext_manager import install_blueprint_from_registry, publish_blueprint

    registry = tmp_path / "registry"
    project = tmp_path / "project"
    project.mkdir()
    publish_blueprint(EXAMPLES / "web-pipeline.blueprint.json", registry)

    result = install_blueprint_from_registry("web-pipeline", registry, project)
    assert result["missingExtensions"] == ["crewai", "langgraph"]
    assert result["remediations"] == [
        f"grimoire ext add crewai --registry {registry.resolve()} --project-root {project.resolve()}",
        f"grimoire ext add langgraph --registry {registry.resolve()} --project-root {project.resolve()}",
    ]
