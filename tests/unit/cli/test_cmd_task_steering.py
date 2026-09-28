"""``grimoire task prioritize / comment / cancel / ack`` — le pilotage humain au CLI (issue #638, lot B)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.cmd_task import task_app
from grimoire.missions.gates import GATES_FILE

runner = CliRunner()

STANDARD = Path("_grimoire/standard")
ACCEPTATION = "le CLI pilote la tache"

GATES = """\
$schema: "grimoire-agentic-standard-evidence-gates/v1"
transitions:
  - id: proposed_to_ready
    from: proposed
    to: ready
    required_evidence: ["acceptance_criteria", "owner_or_agent_role"]
profile_strictness:
  governed: hard_fail
"""


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    (tmp_path / STANDARD).mkdir(parents=True)
    (tmp_path / GATES_FILE).write_text(GATES, encoding="utf-8")
    (tmp_path / STANDARD / "standard-profile.yaml").write_text("profile: governed\n", encoding="utf-8")
    return tmp_path


def run(projet: Path, *args: str, fmt: str = "text"):
    argv = [*args, "--project-root", str(projet)]
    return runner.invoke(task_app, argv, obj={"output": fmt})


def reclame(projet: Path) -> str:
    res = run(projet, "add", "Ajouter /health", "-a", ACCEPTATION, "--owner", "amelia")
    assert res.exit_code == 0, res.output
    tid = next(mot for mot in res.output.split() if mot.startswith("GAO-"))
    assert run(projet, "move", tid, "--to", "ready").exit_code == 0
    assert run(projet, "claim", tid, "--actor", "claude-session").exit_code == 0
    return tid


def test_prioritize_puis_show_montre_la_priorite_et_les_consignes(projet: Path) -> None:
    tid = reclame(projet)
    res = run(projet, "prioritize", tid, "--to", "high", "--reason", "bloque la release", fmt="json")
    assert res.exit_code == 0, res.output
    assert json.loads(res.stdout)["priority"] == "high"
    assert run(projet, "prioritize", tid, "--to", "urgent").exit_code == 1

    res = run(projet, "comment", tid, "relis le CHANGELOG", "--kind", "directive", "--actor", "guilhem", fmt="json")
    assert res.exit_code == 0, res.output
    directive_id = json.loads(res.stdout)["directive"]["id"]
    assert directive_id.startswith("dir-")

    shown = run(projet, "show", tid)
    assert shown.exit_code == 0, shown.output
    assert "relis le CHANGELOG" in shown.output and "non lue" in shown.output

    assert run(projet, "ack", tid, directive_id, "--actor", "claude-session").exit_code == 0
    assert "accusée" in run(projet, "show", tid).output
    assert run(projet, "ack", tid, "dir-inconnue").exit_code == 1


def test_cancel_exige_une_raison_et_force_pour_une_tache_tenue_ailleurs(projet: Path) -> None:
    tid = reclame(projet)
    sans_raison = run(projet, "cancel", tid)
    assert sans_raison.exit_code != 0, "l'option --reason est obligatoire"

    refus = run(projet, "cancel", tid, "--reason", "issue close", "--actor", "guilhem")
    assert refus.exit_code == 1
    assert "claude-session" in refus.output

    force = run(projet, "cancel", tid, "--reason", "issue close", "--actor", "guilhem", "--force")
    assert force.exit_code == 0, force.output
    assert "claimed → cancelled" in force.output
