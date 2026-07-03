"""Tests for mission CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from grimoire.cli.app import app

runner = CliRunner()


def test_missions_import_task_flow_json(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text(
        json.dumps({
            "timestamp": "2026-05-09T10:00:00Z",
            "event": "task-finish",
            "task": "grimoire: memory:gate",
            "flow": "memory",
            "kind": "task",
            "status": "success",
            "exitCode": 0,
            "startedAt": "2026-05-09T10:00:00Z",
            "finishedAt": "2026-05-09T10:00:01Z",
            "cwd": "/repo",
            "command": "grimoire memory gate",
        }) + "\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "-o",
            "json",
            "missions",
            "import-task-flow",
            "--events",
            str(events),
            "--ledger",
            str(tmp_path / "ledger"),
        ],
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["mission_id"] == "MIS-task-flow-001"
    assert data["events_imported"] == 1
    assert data["tasks_created"] == 1
