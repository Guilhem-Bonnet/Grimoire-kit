"""#702 — `grimoire standard init --force` ne détruit pas les réglages du registre d'outils.

`--force` réécrit les artefacts du standard depuis le gabarit. Pour
`tool-registry.yaml`, que le projet complète (MCP `out_of_scope`,
`error_capture`), l'écrasement était silencieux : la version du projet est
désormais fusionnée avec le gabarit (le projet gagne), jamais perdue.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ruamel.yaml import YAML

NEEDS = "multi-agent-orchestration,hooks-skills-governance,observability-cockpit"
REGISTRY = Path("_grimoire/standard/tool-registry.yaml")


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


def _customised_project(tmp_path: Path) -> Path:
    from typer.testing import CliRunner

    from grimoire.cli.app import app

    target = tmp_path / "proj"
    result = CliRunner().invoke(app, ["up", str(target), "--backend", "local", "--needs", NEEDS])
    assert result.exit_code == 0, result.output
    data = _load(target / REGISTRY)
    data["mcp_servers"] = [{
        "id": "MCP-001", "server": "grimoire", "out_of_scope": True,
        "out_of_scope_reason": "outil de l'atelier",
    }]
    data["error_capture"]["note_projet"] = "à garder"
    yaml = YAML()
    with (target / REGISTRY).open("w", encoding="utf-8") as fh:
        yaml.dump(data, fh)
    return target


def test_force_keeps_project_registry_settings(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from grimoire.cli.app import app

    target = _customised_project(tmp_path)
    result = CliRunner().invoke(
        app, ["standard", "init", str(target), "--needs", NEEDS, "--force"],
    )
    assert result.exit_code == 0, result.output
    data = _load(target / REGISTRY)
    assert data["mcp_servers"][0]["out_of_scope"] is True
    assert data["error_capture"]["note_projet"] == "à garder"
    assert "--force : réglages du projet conservés" in result.output


def test_both_sides_changed_project_wins_and_says_so(tmp_path: Path) -> None:
    from grimoire.core.standard_merge import base_path, merge_project_registry

    dest = Path("_grimoire/standard/tool-registry.yaml")
    (tmp_path / dest).parent.mkdir(parents=True)
    (tmp_path / dest).write_text('$schema: "s/v1"\nmode: projet\n', encoding="utf-8")
    base_path(tmp_path, dest).parent.mkdir(parents=True)
    base_path(tmp_path, dest).write_text('$schema: "s/v1"\nmode: base\n', encoding="utf-8")
    out = merge_project_registry(tmp_path, dest, '$schema: "s/v1"\nmode: kit\n')
    assert _load(tmp_path / dest)["mode"] == "projet"
    assert any("conflit" in n and "mode" in n for n in out.notices)
