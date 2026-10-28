"""`grimoire phase set` écrit la phase ; le hook SessionStart l'annonce."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.claude_activation import activation_context_text
from grimoire.missions.finition import project_phase

runner = CliRunner()


def test_phase_set_writes_and_hook_announces(tmp_path: Path) -> None:
    (tmp_path / "project-context.yaml").write_text("# garde-moi\nproject:\n  name: x\n", encoding="utf-8")
    assert "Projet en" not in activation_context_text(tmp_path)
    result = runner.invoke(app, ["phase", "set", "exploitation", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert project_phase(tmp_path) == "exploitation"
    assert "# garde-moi" in (tmp_path / "project-context.yaml").read_text(encoding="utf-8")
    assert "Projet en `exploitation`" in activation_context_text(tmp_path)


def test_phase_set_rejects_unknown(tmp_path: Path) -> None:
    (tmp_path / "project-context.yaml").write_text("project:\n  name: x\n", encoding="utf-8")
    result = runner.invoke(app, ["phase", "set", "nope", "--path", str(tmp_path)])
    assert result.exit_code == 1
    assert project_phase(tmp_path) == ""
