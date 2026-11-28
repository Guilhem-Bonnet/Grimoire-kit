"""#702 — le registre d'outils n'est jamais perdu en silence.

- `standard init --force` remet le gabarit du kit (le kit reprend l'autorité),
  mais sauvegarde d'abord le registre à côté et liste ce qui n'est pas repris.
- `up` fusionne (la valeur du projet reste) ; un conflit sur un champ de sûreté
  passe l'étape en avertissement et nomme le champ et les deux valeurs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ruamel.yaml import YAML

NEEDS = "multi-agent-orchestration,hooks-skills-governance,observability-cockpit"
REGISTRY = Path("_grimoire/standard/tool-registry.yaml")
BASE = Path("_grimoire/standard/.generated-base/tool-registry.yaml")


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("GRIMOIRE_TASK_ID", raising=False)
    monkeypatch.setenv("GRIMOIRE_NO_COCKPIT", "1")
    monkeypatch.setattr("grimoire.cli.cmd_up.run_env_checks", lambda target: [])


def _load(path: Path) -> dict:
    return YAML(typ="safe").load(path.read_text(encoding="utf-8"))


def _dump(path: Path, data: dict) -> None:
    with path.open("w", encoding="utf-8") as fh:
        YAML().dump(data, fh)


def _invoke(*args: str):
    from typer.testing import CliRunner

    from grimoire.cli.app import app

    return CliRunner().invoke(app, list(args))


def _project(tmp_path: Path) -> Path:
    target = tmp_path / "proj"
    result = _invoke("up", str(target), "--backend", "local", "--needs", NEEDS)
    assert result.exit_code == 0, result.output
    return target


def _backups(target: Path) -> list[Path]:
    return sorted((target / REGISTRY).parent.glob("tool-registry.yaml.backup-*"))


def test_force_overwrites_backs_up_and_lists_lost_settings(tmp_path: Path) -> None:
    target = _project(tmp_path)
    kit = _load(target / REGISTRY)
    data = _load(target / REGISTRY)
    data["mcp_servers"] = [{
        "id": "MCP-001", "server": "grimoire", "out_of_scope": True,
        "out_of_scope_reason": "outil de l'atelier",
    }]
    data["error_capture"]["note_projet"] = "à garder"
    _dump(target / REGISTRY, data)
    edited = (target / REGISTRY).read_text(encoding="utf-8")

    result = _invoke("standard", "init", str(target), "--needs", NEEDS, "--force")

    assert result.exit_code == 0, result.output
    assert _load(target / REGISTRY) == kit  # le kit a repris l'autorité
    backups = _backups(target)
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == edited
    out = " ".join(result.output.split())
    assert "mcp_servers[MCP-001]" in out
    assert "error_capture.note_projet" in out
    assert "à garder" in out
    assert backups[0].name in out
    assert "réglages du projet conservés" not in out


def test_force_with_unreadable_registry_backs_up_then_writes_template(tmp_path: Path) -> None:
    target = _project(tmp_path)
    kit = _load(target / REGISTRY)
    (target / REGISTRY).write_text("tools: [unclosed\n  - {\n", encoding="utf-8")

    result = _invoke("standard", "init", str(target), "--needs", NEEDS, "--force")

    assert result.exit_code == 0, result.output
    assert _load(target / REGISTRY) == kit
    backups = _backups(target)
    assert len(backups) == 1
    assert "unclosed" in backups[0].read_text(encoding="utf-8")
    assert backups[0].name in " ".join(result.output.split())


def test_force_dry_run_touches_nothing(tmp_path: Path) -> None:
    target = _project(tmp_path)
    data = _load(target / REGISTRY)
    data["error_capture"]["note_projet"] = "x"
    _dump(target / REGISTRY, data)
    before = (target / REGISTRY).read_text(encoding="utf-8")

    result = _invoke("standard", "init", str(target), "--needs", NEEDS, "--force", "--dry-run")

    assert result.exit_code == 0, result.output
    assert (target / REGISTRY).read_text(encoding="utf-8") == before
    assert _backups(target) == []


def test_force_help_describes_the_backup() -> None:
    result = _invoke("standard", "init", "--help")
    assert "tool-registry" in " ".join(result.output.split())


def _conflict_project(tmp_path: Path) -> Path:
    """Projet dont le registre a un conflit réel : risk changé par le projet ET par le kit."""
    target = _project(tmp_path)
    base = _load(target / BASE)
    proj = _load(target / REGISTRY)
    base["tools"][0]["risk"] = "ancien-kit"  # le kit livrait autre chose à la dernière synchro
    proj["tools"][0]["risk"] = "choix-projet"
    _dump(target / BASE, base)
    _dump(target / REGISTRY, proj)
    return target


def test_up_safety_conflict_is_a_warning_naming_field_and_values(tmp_path: Path) -> None:
    target = _conflict_project(tmp_path)
    tid = _load(target / REGISTRY)["tools"][0]["id"]
    kit_value = _load(target / REGISTRY)["tools"][0]["risk"]  # encore la valeur projet ici

    result = _invoke("-o", "json", "up", str(target), "--backend", "local", "--needs", NEEDS)

    assert result.exit_code == 0, result.output
    steps = {s["step"]: s for s in json.loads(result.output)["steps"]}
    std = steps["standard"]
    assert std["status"] == "warning"
    assert f"tools[{tid}].risk" in std["detail"]
    assert "choix-projet" in std["detail"]
    assert "grimoire standard init --force" in std["detail"]
    assert kit_value == "choix-projet"
    assert _load(target / REGISTRY)["tools"][0]["risk"] == "choix-projet"  # rien de changé en silence


def test_up_without_conflict_stays_done(tmp_path: Path) -> None:
    target = _project(tmp_path)
    proj = _load(target / REGISTRY)
    proj["tools"][0]["risk"] = "choix-projet"  # changement du projet seul : pas un conflit
    _dump(target / REGISTRY, proj)

    result = _invoke("-o", "json", "up", str(target), "--backend", "local", "--needs", NEEDS)

    assert result.exit_code == 0, result.output
    steps = {s["step"]: s for s in json.loads(result.output)["steps"]}
    assert steps["standard"]["status"] == "done"


def test_non_safety_conflict_is_noted_but_not_a_warning(tmp_path: Path) -> None:
    from grimoire.core.standard_merge import base_path, merge_project_registry

    dest = Path("_grimoire/standard/tool-registry.yaml")
    (tmp_path / dest).parent.mkdir(parents=True)
    (tmp_path / dest).write_text('$schema: "s/v1"\nmode: projet\n', encoding="utf-8")
    base_path(tmp_path, dest).parent.mkdir(parents=True)
    base_path(tmp_path, dest).write_text('$schema: "s/v1"\nmode: base\n', encoding="utf-8")
    out = merge_project_registry(tmp_path, dest, '$schema: "s/v1"\nmode: kit\n')
    assert _load(tmp_path / dest)["mode"] == "projet"
    assert any("conflit" in n and "mode" in n for n in out.notices)
    assert out.conflicts == []
