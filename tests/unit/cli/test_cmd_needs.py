"""``grimoire needs resolve`` — la vue humaine sur la résolution des besoins (issue #205).

Même résolution que ``flow run``/``resume`` appliquent en silence pour une
acceptance ``run_need`` — cette commande ne fait que l'afficher, avec sa
source.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from grimoire.cli.app import app

runner = CliRunner()


def test_needs_resolve_json_reports_detected_and_unresolved(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    result = runner.invoke(app, ["needs", "resolve", "--project-root", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["test-runner"] == {
        "command": "pytest -q",
        "source": "detected",
        "evidence": "pyproject.toml",
    }
    assert payload["migration-tool"]["source"] == "unresolved"
    assert payload["migration-tool"]["command"] is None


def test_needs_resolve_json_declared_wins_over_detected(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    (tmp_path / "project-context.yaml").write_text(
        "project:\n  name: x\nneeds:\n  commands:\n    test-runner: tox -e py312\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["needs", "resolve", "--project-root", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["test-runner"] == {
        "command": "tox -e py312",
        "source": "declared",
        "evidence": "project-context.yaml",
    }


def test_needs_resolve_text_output_lists_every_catalog_need(tmp_path: Path) -> None:
    result = runner.invoke(app, ["needs", "resolve", "--project-root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    for need_id in ("test-runner", "lint", "typecheck", "build", "migration-tool", "format"):
        assert need_id in result.output
