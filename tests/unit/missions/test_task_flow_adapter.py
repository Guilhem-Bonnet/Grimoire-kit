"""Tests for task-flow to MissionLedger import."""

from __future__ import annotations

import json
from pathlib import Path

from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import MissionState, TaskState
from grimoire.missions.task_flow_adapter import import_task_flow_events


def _write_events(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_import_task_flow_creates_mission_task_and_events(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    _write_events(
        events,
        [
            {
                "timestamp": "2026-05-09T10:00:00Z",
                "event": "task-start",
                "task": "grimoire: memory:gate",
                "flow": "memory",
                "kind": "task",
                "status": "running",
                "exitCode": 0,
                "startedAt": "2026-05-09T10:00:00Z",
                "finishedAt": "2026-05-09T10:00:00Z",
                "cwd": "/repo/grimoire-kit",
                "command": "grimoire memory gate",
            },
            {
                "timestamp": "2026-05-09T10:00:03Z",
                "event": "task-finish",
                "task": "grimoire: memory:gate",
                "flow": "memory",
                "kind": "task",
                "status": "success",
                "exitCode": 0,
                "startedAt": "2026-05-09T10:00:00Z",
                "finishedAt": "2026-05-09T10:00:03Z",
                "cwd": "/repo/grimoire-kit",
                "command": "grimoire memory gate",
            },
        ],
    )
    ledger = MissionLedger(tmp_path / "ledger")

    report = import_task_flow_events(ledger, events)

    assert report.to_dict() == {
        "mission_id": "MIS-task-flow-001",
        "events_read": 2,
        "events_imported": 2,
        "events_skipped": 0,
        "mission_created": True,
        "tasks_created": 1,
        "tasks_updated": 2,
        "incidents_created": 0,
    }
    mission = ledger.get_mission("MIS-task-flow-001")
    assert mission is not None
    assert mission.status == MissionState.OPEN
    tasks = ledger.list_tasks(mission.id)
    assert len(tasks) == 1
    assert tasks[0].status == TaskState.NEEDS_VERIFICATION
    imported = [event for event in ledger.list_events(tasks[0].id) if event.event_type == "task_flow.imported"]
    assert len(imported) == 2
    assert imported[0].payload["command"] == "grimoire memory gate"


def test_import_task_flow_is_idempotent_and_creates_failure_incident(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    _write_events(
        events,
        [
            {
                "timestamp": "2026-05-09T10:00:00Z",
                "event": "task-finish",
                "task": "grimoire: hooks-smoke",
                "flow": "hooks",
                "kind": "task",
                "status": "failed",
                "exitCode": 1,
                "startedAt": "2026-05-09T10:00:00Z",
                "finishedAt": "2026-05-09T10:00:01Z",
                "cwd": "/repo",
                "command": "grimoire-hooks-smoke.sh",
            }
        ],
    )
    ledger = MissionLedger(tmp_path / "ledger")

    first = import_task_flow_events(ledger, events)
    second = import_task_flow_events(ledger, events)

    assert first.events_imported == 1
    assert first.incidents_created == 1
    assert second.events_imported == 0
    assert second.events_skipped == 1
    assert second.tasks_created == 0
    tasks = ledger.list_tasks("MIS-task-flow-001")
    assert len(tasks) == 1
    assert tasks[0].status == TaskState.FAILED
    assert len(ledger.list_incidents(tasks[0].id)) == 1
