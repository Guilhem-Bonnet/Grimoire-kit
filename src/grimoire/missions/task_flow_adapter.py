"""Import Grimoire task-flow runtime events into MissionLedger."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import IncidentSeverity, MissionState, RiskProfile, TaskState, TaskType

__all__ = ["TaskFlowImportReport", "import_task_flow_events"]

_DEFAULT_MISSION_ID = "MIS-task-flow-001"
_DEFAULT_MISSION_TITLE = "Grimoire task-flow runtime"


@dataclass(frozen=True, slots=True)
class TaskFlowImportReport:
    """Summary returned after importing task-flow events."""

    mission_id: str
    events_read: int = 0
    events_imported: int = 0
    events_skipped: int = 0
    mission_created: bool = False
    tasks_created: int = 0
    tasks_updated: int = 0
    incidents_created: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "events_read": self.events_read,
            "events_imported": self.events_imported,
            "events_skipped": self.events_skipped,
            "mission_created": self.mission_created,
            "tasks_created": self.tasks_created,
            "tasks_updated": self.tasks_updated,
            "incidents_created": self.incidents_created,
        }


def _slug(value: str, *, max_len: int = 20) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (slug or "task")[:max_len].strip("-") or "task"


def _event_fingerprint(raw: dict[str, Any]) -> str:
    payload = json.dumps(raw, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _task_id(label: str) -> str:
    digest = hashlib.sha256(label.encode("utf-8")).hexdigest()[:8]
    return f"GAO-taskflow-{_slug(label, max_len=18)}-{digest}"


def _task_type(flow: str, kind: str) -> TaskType:
    raw = f"{flow} {kind}".lower()
    if "doc" in raw:
        return TaskType.DOCUMENTATION
    if "test" in raw or "quality" in raw:
        return TaskType.TEST
    if "migration" in raw:
        return TaskType.MIGRATION
    if "security" in raw or "hook" in raw:
        return TaskType.OPERATION
    return TaskType.OPERATION


def _read_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not path.exists():
        return events
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(raw, dict) and raw.get("task") and raw.get("event"):
            events.append(raw)
    return events


def _ensure_mission(ledger: MissionLedger, mission_id: str, mission_title: str) -> bool:
    mission = ledger.get_mission(mission_id)
    if mission is not None:
        return False
    mission = ledger.create_mission(
        mission_title,
        origin="task-flow",
        description="Runtime tasks imported from _grimoire-runtime-output/task-flow/events.jsonl.",
        risk_profile=RiskProfile.STANDARD,
        created_by="task-flow",
        scope_surfaces=("task-flow", "hooks", "vscode-tasks"),
        mission_id=mission_id,
    )
    ledger.transition_mission(mission.id, MissionState.OPEN, actor_id="task-flow", reason="task-flow import")
    return True


def _ensure_task(ledger: MissionLedger, mission_id: str, raw: dict[str, Any]) -> tuple[str, bool]:
    label = str(raw.get("task") or "task")
    task_id = _task_id(label)
    if ledger.get_task(task_id) is not None:
        return task_id, False
    flow = str(raw.get("flow") or "")
    kind = str(raw.get("kind") or "")
    command = str(raw.get("command") or "")
    ledger.create_task(
        mission_id,
        f"Task-flow: {label}",
        type=_task_type(flow, kind),
        risk_profile=RiskProfile.STANDARD,
        acceptance=("task-flow event imported", "result requires evidence before closure"),
        description=f"Imported from task-flow `{flow}` with command `{command}`.",
        surface=flow or "task-flow",
        owner="task-flow",
        expected_evidence=("task-flow event", "exit code", "command"),
        guardrails=("Do not auto-close imported runtime tasks.",),
        task_id=task_id,
    )
    return task_id, True


def _source_event_imported(ledger: MissionLedger, task_id: str, source_event_id: str) -> bool:
    for event in ledger.list_events(task_id):
        if event.event_type == "task_flow.imported" and event.payload.get("source_event_id") == source_event_id:
            return True
    return False


def _transition_task(ledger: MissionLedger, task_id: str, target: TaskState, *, reason: str) -> bool:
    task = ledger.get_task(task_id)
    if task is None or task.status == target or task.status in {TaskState.CLOSED, TaskState.CANCELLED}:
        return False
    changed = False

    def current() -> TaskState:
        refreshed = ledger.get_task(task_id)
        return refreshed.status if refreshed is not None else target

    while current() != target:
        state = current()
        if state == TaskState.PROPOSED:
            ledger.transition_task(task_id, TaskState.READY, actor_id="task-flow", reason=reason)
        elif state == TaskState.READY:
            ledger.claim_task(task_id, actor_id="task-flow", host_id="host-task-flow")
        elif state == TaskState.CLAIMED:
            ledger.transition_task(task_id, TaskState.RUNNING, actor_id="task-flow", reason=reason)
        elif state == TaskState.RUNNING and target in {TaskState.NEEDS_VERIFICATION, TaskState.FAILED}:
            ledger.transition_task(task_id, target, actor_id="task-flow", reason=reason)
        elif state == TaskState.FAILED and target != TaskState.FAILED:
            ledger.transition_task(task_id, TaskState.READY, actor_id="task-flow", reason=reason)
        elif state == TaskState.NEEDS_VERIFICATION and target == TaskState.RUNNING:
            ledger.transition_task(task_id, TaskState.RUNNING, actor_id="task-flow", reason=reason)
        else:
            break
        changed = True
    return changed


def _target_state(raw: dict[str, Any]) -> TaskState:
    if raw.get("event") == "task-start":
        return TaskState.RUNNING
    if raw.get("status") == "failed" or int(raw.get("exitCode") or 0) != 0:
        return TaskState.FAILED
    return TaskState.NEEDS_VERIFICATION


def _maybe_open_failure_incident(ledger: MissionLedger, mission_id: str, task_id: str, raw: dict[str, Any]) -> bool:
    if _target_state(raw) != TaskState.FAILED:
        return False
    source_event_id = _event_fingerprint(raw)
    for incident in ledger.list_incidents(task_id):
        if source_event_id in incident.causes:
            return False
    ledger.open_incident(
        mission_id,
        task_id,
        "task_flow_failure",
        f"Task-flow command failed: {raw.get('task')}",
        severity=IncidentSeverity.MEDIUM,
        causes=(source_event_id, str(raw.get("command") or "")),
        recommended_actions=("Inspect task-flow command output.", "Attach evidence before retry or closure."),
        workflow_instance_id=str(raw.get("timestamp") or ""),
        created_by="task-flow",
    )
    return True


def import_task_flow_events(
    ledger: MissionLedger,
    events_path: Path,
    *,
    mission_id: str = _DEFAULT_MISSION_ID,
    mission_title: str = _DEFAULT_MISSION_TITLE,
) -> TaskFlowImportReport:
    """Import task-flow JSONL events into a MissionLedger idempotently."""
    events = _read_events(events_path)
    mission_created = _ensure_mission(ledger, mission_id, mission_title)

    events_imported = 0
    events_skipped = 0
    tasks_created = 0
    tasks_updated = 0
    incidents_created = 0

    for raw in events:
        source_event_id = _event_fingerprint(raw)
        task_id, created = _ensure_task(ledger, mission_id, raw)
        tasks_created += int(created)
        if _source_event_imported(ledger, task_id, source_event_id):
            events_skipped += 1
            continue

        changed = _transition_task(
            ledger,
            task_id,
            _target_state(raw),
            reason=f"task-flow import {source_event_id[:12]}",
        )
        tasks_updated += int(changed)
        incidents_created += int(_maybe_open_failure_incident(ledger, mission_id, task_id, raw))
        ledger.append_event(
            "task_flow.imported",
            task_id,
            "task",
            "task-flow",
            {
                "source_event_id": source_event_id,
                "task": raw.get("task", ""),
                "flow": raw.get("flow", ""),
                "kind": raw.get("kind", ""),
                "status": raw.get("status", ""),
                "event": raw.get("event", ""),
                "exit_code": raw.get("exitCode", 0),
                "started_at": raw.get("startedAt", ""),
                "finished_at": raw.get("finishedAt", ""),
                "cwd": raw.get("cwd", ""),
                "command": raw.get("command", ""),
            },
        )
        events_imported += 1

    return TaskFlowImportReport(
        mission_id=mission_id,
        events_read=len(events),
        events_imported=events_imported,
        events_skipped=events_skipped,
        mission_created=mission_created,
        tasks_created=tasks_created,
        tasks_updated=tasks_updated,
        incidents_created=incidents_created,
    )
