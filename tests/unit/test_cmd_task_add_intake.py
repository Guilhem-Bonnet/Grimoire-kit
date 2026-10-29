"""I2 : `grimoire task add --intake "<texte>" --create [--json]`."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app

runner = CliRunner()
IDEE = "Corriger l'export CSV et ajouter un test"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)


def _events(root: Path) -> list[Path]:
    return sorted((root / "_grimoire-runtime-output").rglob("events.jsonl")) if (root / "_grimoire-runtime-output").exists() else []


def _digest(root: Path) -> list[str]:
    return [hashlib.sha256(p.read_bytes()).hexdigest() for p in _events(root)]


def _task(root: Path, *args: str):
    return runner.invoke(app, ["task", "add", *args, "--project-root", str(root)])


def test_create_json_returns_the_created_ids(tmp_path: Path) -> None:
    result = runner.invoke(app, ["task", "add", "--intake", IDEE, "--create", "--json", "--check", "pytest -q",
                                 "--project-root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["created"] and all(c["id"] for c in body["created"])
    assert {c["status"] for c in body["created"]} == {"ready"}


def test_create_without_check_leaves_v0_tasks_proposed(tmp_path: Path) -> None:
    result = runner.invoke(app, ["task", "add", "--intake", IDEE, "--create", "--json", "--project-root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert {c["status"] for c in json.loads(result.output)["created"]} == {"proposed"}


def test_critical_proposals_are_not_created(tmp_path: Path) -> None:
    result = runner.invoke(app, ["task", "add", "--intake", "drop table users", "--create", "--json",
                                 "--project-root", str(tmp_path)])
    body = json.loads(result.output)
    assert body["skipped_critical"] == ["drop table users"]
    assert all(c["title"] != "drop table users" for c in body["created"])


def test_without_create_the_events_log_is_untouched(tmp_path: Path) -> None:
    seed = runner.invoke(app, ["task", "add", "Amorce", "-a", "ok", "--project-root", str(tmp_path)])
    assert seed.exit_code == 0, seed.output
    before = _digest(tmp_path)
    assert before
    result = runner.invoke(app, ["task", "add", "--intake", IDEE, "--project-root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["written"] is False
    assert _digest(tmp_path) == before


def test_create_refuses_a_title(tmp_path: Path) -> None:
    result = runner.invoke(app, ["task", "add", "x", "--intake", IDEE, "--create", "--project-root", str(tmp_path)])
    assert result.exit_code == 2
