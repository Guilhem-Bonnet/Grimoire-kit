"""``grimoire upgrade-flow review`` — contexte de revue en lecture seule (issue #520, PR 1).

`grimoire upgrade-flow run` s'arrête volontairement, non décidé, au
checkpoint `destructive` ou sur une proposition V1 (issues #490/#506/#510).
Rien avant cette commande ne rassemblait ce contexte pour un humain ou un
skill sans reconstituer l'état à la main depuis plusieurs commandes. Cette
commande ne fait que lire — jamais accepter, rejeter, ni lancer quoi que ce
soit elle-même.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app

runner = CliRunner()


def test_review_refuses_when_the_tool_is_older_than_the_project_kit(
    cli_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #515 : un outil en retard peut ignorer des catégories de proposition récentes."""
    monkeypatch.setattr(
        "grimoire.tools.project_health.tool_version_gap",
        lambda _root: {"installed": "3.40.0", "aligned": "3.51.1", "outdated": True},
    )

    result = runner.invoke(app, ["upgrade-flow", "review", "--project-root", str(cli_project), "--json"])

    assert result.exit_code == 1, result.output
    payload = json.loads(result.output.strip().splitlines()[-1])
    assert payload["ok"] is False
    assert "3.40.0" in payload["error"]
    assert "3.51.1" in payload["error"]


def test_review_runs_when_the_tool_is_aligned_with_no_run_and_no_proposal(cli_project: Path) -> None:
    result = runner.invoke(app, ["upgrade-flow", "review", "--project-root", str(cli_project), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip().splitlines()[-1])
    assert payload["ok"] is True
    assert payload["last_run"] is None
    assert payload["checkpoint_pending"] is False
    assert payload["pending_proposals"] == []


def test_review_reports_the_pending_checkpoint_and_proposals(
    cli_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from grimoire.proposals import create_manual_proposal

    monkeypatch.setattr(
        "grimoire.tools.flow_runs.list_flow_runs",
        lambda _root, **_kw: [
            {"runId": "r-upgrade-1", "blueprintId": "project-upgrade", "status": "checkpointed", "currentNode": "destructive"}
        ],
    )
    create_manual_proposal(
        cli_project,
        slug="override-migration-agent-x",
        specialty="override en dérive : agent-x",
        artifact_type="override-migration",
        category="override-drift",
    )

    result = runner.invoke(app, ["upgrade-flow", "review", "--project-root", str(cli_project), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip().splitlines()[-1])
    assert payload["last_run"]["runId"] == "r-upgrade-1"
    assert payload["checkpoint_pending"] is True
    slugs = [p["slug"] for p in payload["pending_proposals"]]
    assert "override-migration-agent-x" in slugs


def test_review_text_output_never_applies_a_decision(
    cli_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lecture seule : la sortie texte ne doit jamais écrire de proposition acceptée/rejetée."""
    from grimoire.proposals import list_proposals

    monkeypatch.setattr(
        "grimoire.tools.flow_runs.list_flow_runs",
        lambda _root, **_kw: [
            {"runId": "r-upgrade-2", "blueprintId": "project-upgrade", "status": "checkpointed", "currentNode": "destructive"}
        ],
    )

    result = runner.invoke(app, ["upgrade-flow", "review", "--project-root", str(cli_project)])

    assert result.exit_code == 0, result.output
    assert "checkpoint destructif en attente" in result.output
    assert "grimoire flow resume r-upgrade-2" in result.output
    assert list_proposals(cli_project, sync=False) == []
