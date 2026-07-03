"""Tests for grimoire.cli.cmd_ext — CLI extensions."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from grimoire.cli.app import app

runner = CliRunner()


def make_extension(tmp_path: Path) -> Path:
    ext = tmp_path / "demo-ext"
    (ext / "artifacts").mkdir(parents=True)
    (ext / "artifacts" / "demo.agent.md").write_text("# Demo\n", encoding="utf-8")
    manifest = {
        "manifestVersion": 1,
        "id": "demo-ext",
        "name": "Demo Extension",
        "version": "0.1.0",
        "description": "Extension de test.",
        "license": "MIT",
        "authors": [{"name": "Test"}],
        "compat": {"kit": ">=3.11", "manifest": 1},
        "provides": {"agents": ["artifacts/demo.agent.md"]},
        "patterns": {"implements": ["ORC-01"]},
        "permissions": {
            "filesystem": "artifacts",
            "network": False,
            "hooks": [],
            "memory": "none",
        },
        "install": {
            "steps": [
                {
                    "kind": "copy",
                    "from": "artifacts/demo.agent.md",
                    "to": ".github/agents/demo.agent.md",
                }
            ]
        },
    }
    (ext / "extension.json").write_text(json.dumps(manifest), encoding="utf-8")
    return ext


def test_ext_add_list_remove_cycle(tmp_path: Path) -> None:
    ext = make_extension(tmp_path)
    project = tmp_path / "project"
    project.mkdir()

    result = runner.invoke(
        app, ["ext", "add", str(ext), "--project-root", str(project)]
    )
    assert result.exit_code == 0, result.output
    assert "Installé : demo-ext v0.1.0" in result.output
    assert (project / ".github" / "agents" / "demo.agent.md").is_file()

    result = runner.invoke(app, ["ext", "list", "--project-root", str(project)])
    assert result.exit_code == 0
    assert "demo-ext v0.1.0" in result.output
    assert "ORC-01" in result.output

    result = runner.invoke(
        app, ["ext", "remove", "demo-ext", "--project-root", str(project)]
    )
    assert result.exit_code == 0
    assert not (project / ".github" / "agents" / "demo.agent.md").exists()


def test_ext_add_invalid_manifest_fails(tmp_path: Path) -> None:
    ext = make_extension(tmp_path)
    manifest = json.loads((ext / "extension.json").read_text(encoding="utf-8"))
    manifest["patterns"]["implements"] = []
    (ext / "extension.json").write_text(json.dumps(manifest), encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()

    result = runner.invoke(
        app, ["ext", "add", str(ext), "--project-root", str(project)]
    )
    assert result.exit_code == 1


def test_ext_remove_unknown_fails(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["ext", "remove", "ghost", "--project-root", str(tmp_path)]
    )
    assert result.exit_code == 1


def test_ext_publish_then_add_from_registry(tmp_path: Path) -> None:
    ext = make_extension(tmp_path)
    registry = tmp_path / "registry"
    project = tmp_path / "project"
    project.mkdir()

    result = runner.invoke(app, ["ext", "publish", str(ext), "--registry", str(registry)])
    assert result.exit_code == 0, result.output
    assert "Publié : Demo Extension 0.1.0" in result.output

    result = runner.invoke(
        app,
        ["ext", "add", "demo-ext", "--registry", str(registry), "--project-root", str(project)],
    )
    assert result.exit_code == 0, result.output
    assert (project / ".github" / "agents" / "demo.agent.md").is_file()

    result = runner.invoke(app, ["ext", "verify", "demo-ext", "--project-root", str(project)])
    assert result.exit_code == 0, result.output


def test_ext_publish_invalid_manifest_fails(tmp_path: Path) -> None:
    ext = make_extension(tmp_path)
    manifest = json.loads((ext / "extension.json").read_text(encoding="utf-8"))
    manifest["version"] = "pas-semver"
    (ext / "extension.json").write_text(json.dumps(manifest), encoding="utf-8")
    result = runner.invoke(app, ["ext", "publish", str(ext), "--registry", str(tmp_path / "r")])
    assert result.exit_code == 1


def test_ext_verify_unknown_fails(tmp_path: Path) -> None:
    result = runner.invoke(app, ["ext", "verify", "ghost", "--project-root", str(tmp_path)])
    assert result.exit_code == 1
