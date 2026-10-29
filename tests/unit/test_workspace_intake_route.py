"""I2 : « Une idée ? » du cockpit — les routes d'intake et de création de `workspace_routes`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from grimoire.missions.service import TaskService
from grimoire.tools.workspace_routes import POST_ROUTES, PREFIX, workspace_post

INTAKE = f"{PREFIX}tasks/intake"
CREATE = f"{PREFIX}tasks/intake/create"
IDEE = "Corriger l'export CSV et ajouter un test"


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "ck"))
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)


def _project(root: Path) -> Path:
    (root / ".git").mkdir(parents=True)
    return root


def test_routes_are_post_routes() -> None:
    assert INTAKE in POST_ROUTES and CREATE in POST_ROUTES


def test_intake_returns_complete_proposals(tmp_path: Path) -> None:
    out = workspace_post(_project(tmp_path / "p"), INTAKE, {"text": IDEE})
    assert out["written"] is False
    assert len(out["proposals"]) >= 1
    for proposal in out["proposals"]:
        assert proposal["title"] and proposal["risk_profile"] and proposal["verifiability"]
        assert proposal["acceptance"] and all(proposal["acceptance"])
        assert proposal["verifiability"] in {"V0", "V1", "V2"}


def test_two_identical_calls_return_the_same_bytes(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    first = json.dumps(workspace_post(root, INTAKE, {"text": IDEE}), sort_keys=True)
    second = json.dumps(workspace_post(root, INTAKE, {"text": IDEE}), sort_keys=True)
    assert first == second


def test_intake_writes_nothing(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    workspace_post(root, INTAKE, {"text": IDEE})
    assert not (root / "_grimoire-runtime-output").exists()


def test_critical_request_is_never_checked_by_default(tmp_path: Path) -> None:
    out = workspace_post(_project(tmp_path / "p"), INTAKE, {"text": "drop table users"})
    first = out["proposals"][0]
    assert first["risk_profile"] == "critical"
    assert first["checked"] is False


def test_blank_text_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="texte de la demande"):
        workspace_post(_project(tmp_path / "p"), INTAKE, {"text": "   "})


def _tasks(root: Path) -> list[Any]:
    return TaskService(root.resolve()).ledger.list_tasks()


def test_create_makes_checked_tasks_ready_with_a_command(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    proposal = workspace_post(root, INTAKE, {"text": IDEE})["proposals"][0]
    out = workspace_post(root, CREATE, {"tasks": [{**proposal, "check": "pytest -q"}]})
    assert [c["status"] for c in out["created"]] == ["ready"]
    (task,) = _tasks(root)
    assert task.title == proposal["title"] and task.acceptance == tuple(proposal["acceptance"])
    assert [c.cmd for c in task.checks] == ["pytest -q"]


def test_create_without_command_leaves_the_task_proposed_and_says_why(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    proposal = workspace_post(root, INTAKE, {"text": IDEE})["proposals"][0]
    out = workspace_post(root, CREATE, {"tasks": [proposal]})
    (created,) = out["created"]
    assert created["status"] == "proposed" and "commande de vérification" in created["reason"]


def test_create_refuses_an_empty_selection(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    with pytest.raises(ValueError, match="au moins une proposition"):
        workspace_post(root, CREATE, {"tasks": []})
    assert not (root / "_grimoire-runtime-output").exists()
