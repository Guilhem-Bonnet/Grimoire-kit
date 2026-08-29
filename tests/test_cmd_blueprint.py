"""Tests for grimoire.cli.cmd_blueprint — CLI blueprints (new / validate / compile / evals)."""

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


def test_validate_reports_the_skip_without_claiming_a_pass(
    tmp_path: Path, monkeypatch
) -> None:
    """La couche absente se dit, et ne se conclut pas par « Valid ».

    Ce test affirmait l'inverse : sortie zéro et « Valid » alors que la couche
    schéma ne s'était pas exécutée. Il figeait le mode de panne qu'il aurait dû
    empêcher — une porte de CI qui dégrade à la moitié de ses contrôles en
    restant verte.
    """
    out = tmp_path / "demo.blueprint.json"
    assert runner.invoke(blueprint_app, ["new", "demo", "--out", str(out)]).exit_code == 0
    # `sys.modules[name] = None` makes `import jsonschema` raise ImportError.
    monkeypatch.setitem(sys.modules, "jsonschema", None)

    result = runner.invoke(blueprint_app, ["validate", str(out)])
    assert result.exit_code == 1, result.output
    assert "skipped (optional package jsonschema is not installed)" in result.output
    assert "Valid:" not in result.output


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


# ── evals ─────────────────────────────────────────────────────────────────────


def _flow(path: Path, cases: list[dict[str, object]]) -> Path:
    path.write_text(
        json.dumps({
            "id": "f", "version": "1.0.0",
            "nodes": [{"id": "crew", "config": {"evals": {"version": "1.0", "cases": cases}}}],
        }),
        encoding="utf-8",
    )
    return path


def _trace(path: Path, runs: dict[str, object]) -> Path:
    path.write_text(json.dumps({"recordVersion": 1, "runs": runs}), encoding="utf-8")
    return path


def test_evals_rejoue_une_trace_conforme(tmp_path: Path) -> None:
    flow = _flow(tmp_path / "f.blueprint.json", [
        {"id": "c1", "input": {}, "assert": [{"kind": "contract", "contract": "evidence-pack"}]}
    ])
    trace = _trace(tmp_path / "t.json", {"crew": {"c1": {"contract": "evidence-pack"}}})
    result = runner.invoke(blueprint_app, ["evals", str(flow), "--record", str(trace)])
    assert result.exit_code == 0, result.output
    assert "OK" in result.output
    assert "1/1 réussis sur 1 déclarés" in result.output


def test_evals_signale_l_echec_et_sa_raison(tmp_path: Path) -> None:
    flow = _flow(tmp_path / "f.blueprint.json", [
        {"id": "c1", "input": {}, "assert": [{"kind": "contract", "contract": "evidence-pack"}]}
    ])
    trace = _trace(tmp_path / "t.json", {"crew": {"c1": {"contract": "handoff-packet"}}})
    result = runner.invoke(blueprint_app, ["evals", str(flow), "--record", str(trace)])
    assert result.exit_code == 1
    assert "FAIL" in result.output
    assert "handoff-packet" in result.output


def test_evals_ne_confond_pas_non_execute_et_echoue(tmp_path: Path) -> None:
    """Un cas absent de la trace n'est pas réfuté : la commande sort en 0."""
    flow = _flow(tmp_path / "f.blueprint.json", [
        {"id": "jamais-joue", "input": {}, "assert": [{"kind": "no-refusal"}]}
    ])
    trace = _trace(tmp_path / "t.json", {})
    result = runner.invoke(blueprint_app, ["evals", str(flow), "--record", str(trace)])
    assert result.exit_code == 0, result.output
    assert "non exécuté" in result.output
    assert "0/0 réussis sur 1 déclarés" in result.output


def test_evals_refuse_une_trace_de_version_inconnue(tmp_path: Path) -> None:
    flow = _flow(tmp_path / "f.blueprint.json", [
        {"id": "c1", "input": {}, "assert": [{"kind": "no-refusal"}]}
    ])
    trace = tmp_path / "t.json"
    trace.write_text(json.dumps({"recordVersion": 99, "runs": {}}), encoding="utf-8")
    result = runner.invoke(blueprint_app, ["evals", str(flow), "--record", str(trace)])
    assert result.exit_code == 1
    assert "recordVersion" in result.output


def test_evals_sur_le_blueprint_de_reference(tmp_path: Path) -> None:
    """L'exemple livré déclare 5 cas sur 4 portées ; sans trace, aucun n'est
    exécuté et rien n'est compté comme échoué."""
    trace = _trace(tmp_path / "t.json", {})
    result = runner.invoke(
        blueprint_app,
        ["evals", str(EXAMPLES / "web-pipeline.blueprint.json"), "--record", str(trace)],
    )
    assert result.exit_code == 0, result.output
    assert "sur 5 déclarés" in result.output


# ── La couche schéma ne peut plus s'absenter en silence ────────────────────


def _without_jsonschema(monkeypatch) -> None:
    """Simuler une installation sans `jsonschema`, comme un `pip install` nu.

    `sys.modules[name] = None` fait lever ImportError à l'import — la même
    technique que le reste du fichier.
    """
    monkeypatch.setitem(sys.modules, "jsonschema", None)


def test_validate_refuses_when_the_schema_layer_cannot_run(tmp_path: Path, monkeypatch) -> None:
    """Une validation à moitié faite ne doit pas se conclure par « Valid ».

    Le mode de panne d'origine : sans `jsonschema`, la couche schéma
    s'annonçait `skipped` et la commande sortait quand même à zéro. Une porte
    de CI dégradait ainsi à la moitié de ses contrôles en restant verte.
    """
    out = tmp_path / "demo.blueprint.json"
    assert runner.invoke(blueprint_app, ["new", "demo", "--out", str(out)]).exit_code == 0

    _without_jsonschema(monkeypatch)
    result = runner.invoke(blueprint_app, ["validate", str(out)])
    assert result.exit_code == 1, result.output
    assert "has not been fully validated" in result.output
    assert "pip install jsonschema" in result.output
    assert "Valid:" not in result.output


def test_validate_accepts_a_partial_check_when_asked_explicitly(
    tmp_path: Path, monkeypatch
) -> None:
    """L'échappatoire existe, mais elle se voit dans la commande."""
    out = tmp_path / "demo.blueprint.json"
    assert runner.invoke(blueprint_app, ["new", "demo", "--out", str(out)]).exit_code == 0

    _without_jsonschema(monkeypatch)
    result = runner.invoke(
        blueprint_app, ["validate", str(out), "--allow-skipped-schema"]
    )
    assert result.exit_code == 0, result.output
    assert "Partial:" in result.output
    assert "passes both validation layers" not in result.output


def test_compile_refuses_a_partial_validation(tmp_path: Path, monkeypatch) -> None:
    """La compilation écrit des artefacts : elle exige la validation entière."""
    out = tmp_path / "demo.blueprint.json"
    assert runner.invoke(blueprint_app, ["new", "demo", "--out", str(out)]).exit_code == 0

    _without_jsonschema(monkeypatch)
    result = runner.invoke(
        blueprint_app, ["compile", str(out), "--project-root", str(tmp_path)]
    )
    assert result.exit_code == 1, result.output
    assert "has not been fully validated" in result.output
    assert not (tmp_path / ".github" / "prompts").exists()


def test_validate_still_passes_with_jsonschema_available(tmp_path: Path) -> None:
    """Sans dégradation, rien ne change pour qui a le paquet."""
    out = tmp_path / "demo.blueprint.json"
    assert runner.invoke(blueprint_app, ["new", "demo", "--out", str(out)]).exit_code == 0
    result = runner.invoke(blueprint_app, ["validate", str(out)])
    assert result.exit_code == 0, result.output
    assert "passes both validation layers" in result.output
