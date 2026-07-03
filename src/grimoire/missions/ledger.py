"""Mission Ledger — append-only JSONL source of truth for missions and tasks.

Design principles (from CAHIER-DES-CHARGES §5.3):
  - Append-only event log: state is derived by replaying events, never mutated in place
  - Atomic writes: temp file + rename to avoid partial JSONL corruption
  - State machine: transitions validated before appending
  - Queries: ready / blocked / claimed / stale / needs_verification exposed as properties
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions.schemas import (
    Incident,
    IncidentSeverity,
    IncidentStatus,
    LedgerEvent,
    Mission,
    MissionState,
    MissionTask,
    RiskProfile,
    TaskClaim,
    TaskDependency,
    TaskState,
    TaskType,
)

# Valid state transitions for missions
_MISSION_TRANSITIONS: dict[MissionState, frozenset[MissionState]] = {
    MissionState.DRAFT: frozenset({MissionState.OPEN, MissionState.CANCELLED}),
    MissionState.OPEN: frozenset({MissionState.BLOCKED, MissionState.VERIFYING, MissionState.CLOSED, MissionState.CANCELLED}),
    MissionState.BLOCKED: frozenset({MissionState.OPEN, MissionState.CANCELLED}),
    MissionState.VERIFYING: frozenset({MissionState.OPEN, MissionState.CLOSED, MissionState.CANCELLED}),
    MissionState.CLOSED: frozenset(),
    MissionState.CANCELLED: frozenset(),
}

# Valid state transitions for tasks
_TASK_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.PROPOSED: frozenset({TaskState.READY, TaskState.CANCELLED}),
    TaskState.READY: frozenset({TaskState.CLAIMED, TaskState.BLOCKED, TaskState.CANCELLED}),
    TaskState.CLAIMED: frozenset({TaskState.RUNNING, TaskState.READY, TaskState.CANCELLED}),
    TaskState.RUNNING: frozenset({TaskState.NEEDS_VERIFICATION, TaskState.BLOCKED, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.BLOCKED: frozenset({TaskState.READY, TaskState.CANCELLED}),
    TaskState.NEEDS_VERIFICATION: frozenset({TaskState.CLOSED, TaskState.RUNNING, TaskState.FAILED}),
    TaskState.FAILED: frozenset({TaskState.READY, TaskState.CANCELLED}),
    TaskState.CLOSED: frozenset(),
    TaskState.CANCELLED: frozenset(),
}


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class MissionLedger:
    """Append-only JSONL ledger for missions, tasks, events, and incidents.

    All state is projected from the event log on read; writes append atomically.

    Usage::

        ledger = MissionLedger(Path("_grimoire-runtime-output/ledger"))
        mission = ledger.create_mission("Pack Registry", origin="user")
        task = ledger.create_task(mission.id, "Implement ledger", type=TaskType.IMPLEMENTATION)
        ledger.transition_task(task.id, TaskState.READY, actor_id="agent-dev")
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._events_path = root / "events.jsonl"
        self._incidents_path = root / "incidents.jsonl"
        # In-memory projection caches (rebuilt lazily)
        self._missions: dict[str, Mission] = {}
        self._tasks: dict[str, MissionTask] = {}
        self._events: list[LedgerEvent] = []
        self._incidents: dict[str, Incident] = {}
        self._loaded = False

    # ── Private helpers ────────────────────────────────────────────────────

    def _load(self) -> None:
        if self._loaded:
            return
        self._replay_events()
        self._replay_incidents()
        self._loaded = True

    def _replay_events(self) -> None:
        if not self._events_path.exists():
            return
        for line in self._events_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            evt_type = raw.get("event_type", "")
            payload = raw.get("payload", {})
            if evt_type == "mission.created":
                m = Mission.from_dict(payload)
                self._missions[m.id] = m
            elif evt_type == "mission.transitioned":
                mid = payload["mission_id"]
                if mid in self._missions:
                    old = self._missions[mid]
                    self._missions[mid] = Mission.from_dict({**old.to_dict(), "status": payload["to_state"]})
            elif evt_type == "task.created":
                t = MissionTask.from_dict(payload)
                self._tasks[t.id] = t
            elif evt_type == "task.transitioned":
                tid = payload["task_id"]
                if tid in self._tasks:
                    old = self._tasks[tid]
                    update: dict[str, Any] = {"status": payload["to_state"]}
                    if "claim" in payload:
                        update["claim"] = payload["claim"]
                    self._tasks[tid] = MissionTask.from_dict({**old.to_dict(), **update})
            self._events.append(LedgerEvent.from_dict(raw))

    def _replay_incidents(self) -> None:
        if not self._incidents_path.exists():
            return
        for line in self._incidents_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            inc = Incident.from_dict(raw)
            self._incidents[inc.id] = inc

    def _invalidate(self) -> None:
        self._missions = {}
        self._tasks = {}
        self._events = []
        self._incidents = {}
        self._loaded = False

    def append_event(self, event_type: str, entity_id: str, entity_kind: str, actor_id: str, payload: dict[str, Any]) -> LedgerEvent:
        """Record a custom event on any ledger entity (public API)."""
        return self._append_event(event_type, entity_id, entity_kind, actor_id, payload)

    def _append_event(self, event_type: str, entity_id: str, entity_kind: str, actor_id: str, payload: dict[str, Any]) -> LedgerEvent:
        evt = LedgerEvent(
            id=_new_id("evt"),
            event_type=event_type,
            entity_id=entity_id,
            entity_kind=entity_kind,
            actor_id=actor_id,
            created_at=_now_iso(),
            payload=payload,
        )
        self._atomic_append(self._events_path, evt.to_dict())
        self._invalidate()
        return evt

    def _atomic_append(self, path: Path, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False) + "\n"
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-ledger-")
        try:
            # Copy existing content + append new line
            existing = path.read_text(encoding="utf-8") if path.exists() else ""
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(existing)
                fh.write(line)
            Path(tmp).replace(path)
        except Exception:
            with contextlib.suppress(OSError):
                Path(tmp).unlink()
            raise

    def _next_seq(self, prefix: str, id_map: dict[str, Any]) -> str:
        existing = [k for k in id_map if k.startswith(prefix)]
        return f"{prefix}-{len(existing) + 1:03d}"

    # ── Public write API ───────────────────────────────────────────────────

    def create_mission(
        self,
        title: str,
        *,
        origin: str,
        description: str = "",
        risk_profile: RiskProfile = RiskProfile.STANDARD,
        created_by: str = "",
        scope_repos: tuple[str, ...] = (),
        scope_surfaces: tuple[str, ...] = (),
        scope_packs: tuple[str, ...] = (),
        mission_id: str | None = None,
    ) -> Mission:
        self._load()
        slug = title.lower().replace(" ", "-")[:20].strip("-")
        if mission_id is None:
            seq = len(self._missions) + 1
            mission_id = f"MIS-{slug}-{seq:03d}"
        mission = Mission(
            id=mission_id,
            title=title,
            status=MissionState.DRAFT,
            origin=origin,
            created_at=_now_iso(),
            description=description,
            risk_profile=risk_profile,
            created_by=created_by,
            scope_repos=scope_repos,
            scope_surfaces=scope_surfaces,
            scope_packs=scope_packs,
        )
        self._append_event("mission.created", mission.id, "mission", created_by or "system", mission.to_dict())
        return mission

    def transition_mission(self, mission_id: str, to_state: MissionState, *, actor_id: str = "system", reason: str = "") -> Mission:
        self._load()
        mission = self._missions.get(mission_id)
        if mission is None:
            raise GrimoireMissionError(f"Mission not found: {mission_id}")
        allowed = _MISSION_TRANSITIONS.get(mission.status, frozenset())
        if to_state not in allowed:
            raise GrimoireMissionError(
                f"Invalid mission transition {mission.status.value} → {to_state.value} for {mission_id}"
            )
        self._append_event(
            "mission.transitioned",
            mission_id,
            "mission",
            actor_id,
            {"mission_id": mission_id, "from_state": mission.status.value, "to_state": to_state.value, "reason": reason},
        )
        self._load()
        return self._missions[mission_id]

    def create_task(
        self,
        mission_id: str,
        title: str,
        *,
        type: TaskType = TaskType.IMPLEMENTATION,
        risk_profile: RiskProfile = RiskProfile.STANDARD,
        acceptance: tuple[str, ...] = (),
        description: str = "",
        surface: str = "",
        owner: str = "",
        expected_evidence: tuple[str, ...] = (),
        guardrails: tuple[str, ...] = (),
        dependencies: tuple[TaskDependency, ...] = (),
        task_id: str | None = None,
    ) -> MissionTask:
        self._load()
        if mission_id not in self._missions:
            raise GrimoireMissionError(f"Mission not found: {mission_id}")
        if not acceptance:
            raise GrimoireMissionError("At least one acceptance criterion is required")
        slug = title.lower().replace(" ", "-")[:12].strip("-")
        if task_id is None:
            area_tasks = [k for k in self._tasks if k.startswith(f"GAO-{slug}")]
            seq = len(area_tasks) + 1
            task_id = f"GAO-{slug}-{seq:03d}"
        task = MissionTask(
            id=task_id,
            mission_id=mission_id,
            title=title,
            status=TaskState.PROPOSED,
            type=type,
            risk_profile=risk_profile,
            acceptance=acceptance,
            created_at=_now_iso(),
            description=description,
            surface=surface,
            owner=owner,
            expected_evidence=expected_evidence,
            guardrails=guardrails,
            dependencies=dependencies,
        )
        self._append_event("task.created", task.id, "task", owner or "system", task.to_dict())
        return task

    def transition_task(
        self,
        task_id: str,
        to_state: TaskState,
        *,
        actor_id: str = "system",
        reason: str = "",
        claim: TaskClaim | None = None,
    ) -> MissionTask:
        self._load()
        task = self._tasks.get(task_id)
        if task is None:
            raise GrimoireMissionError(f"Task not found: {task_id}")
        allowed = _TASK_TRANSITIONS.get(task.status, frozenset())
        if to_state not in allowed:
            raise GrimoireMissionError(
                f"Invalid task transition {task.status.value} → {to_state.value} for {task_id}"
            )
        payload: dict[str, Any] = {
            "task_id": task_id,
            "from_state": task.status.value,
            "to_state": to_state.value,
            "reason": reason,
            "actor_id": actor_id,
        }
        if claim is not None:
            payload["claim"] = claim.to_dict()
        self._append_event("task.transitioned", task_id, "task", actor_id, payload)
        self._load()
        return self._tasks[task_id]

    def claim_task(self, task_id: str, actor_id: str, host_id: str, exclusive_files: tuple[str, ...] = ()) -> MissionTask:
        """Convenience: READY → CLAIMED."""
        claim = TaskClaim(actor_id=actor_id, host_id=host_id, exclusive_files=exclusive_files)
        return self.transition_task(task_id, TaskState.CLAIMED, actor_id=actor_id, claim=claim)

    def open_incident(
        self,
        mission_id: str,
        task_id: str,
        kind: str,
        summary: str,
        *,
        severity: IncidentSeverity = IncidentSeverity.MEDIUM,
        causes: tuple[str, ...] = (),
        recommended_actions: tuple[str, ...] = (),
        workflow_instance_id: str = "",
        created_by: str = "system",
    ) -> Incident:
        self._load()
        seq = sum(1 for i in self._incidents.values() if i.task_id == task_id) + 1
        inc_id = f"inc-{task_id}-{seq:03d}"
        incident = Incident(
            id=inc_id,
            mission_id=mission_id,
            task_id=task_id,
            kind=kind,
            severity=severity,
            status=IncidentStatus.OPEN,
            summary=summary,
            created_at=_now_iso(),
            workflow_instance_id=workflow_instance_id,
            causes=causes,
            recommended_actions=recommended_actions,
        )
        self._atomic_append(self._incidents_path, incident.to_dict())
        self._invalidate()
        return incident

    def resolve_incident(self, incident_id: str, *, actor_id: str = "system") -> Incident:
        self._load()
        inc = self._incidents.get(incident_id)
        if inc is None:
            raise GrimoireMissionError(f"Incident not found: {incident_id}")
        updated = Incident.from_dict({**inc.to_dict(), "status": IncidentStatus.RESOLVED.value})
        # Re-write the incidents file: replace matching line
        if self._incidents_path.exists():
            lines = self._incidents_path.read_text(encoding="utf-8").splitlines()
            new_lines = []
            for line in lines:
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                    if raw.get("id") == incident_id:
                        new_lines.append(json.dumps(updated.to_dict(), ensure_ascii=False))
                    else:
                        new_lines.append(line)
                except json.JSONDecodeError:
                    new_lines.append(line)
            self._incidents_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        self._invalidate()
        return updated

    # ── Queries ─────────────────────────────────────────────────────────────

    def get_mission(self, mission_id: str) -> Mission | None:
        self._load()
        return self._missions.get(mission_id)

    def get_task(self, task_id: str) -> MissionTask | None:
        self._load()
        return self._tasks.get(task_id)

    def list_missions(self) -> list[Mission]:
        self._load()
        return list(self._missions.values())

    def list_tasks(self, mission_id: str | None = None) -> list[MissionTask]:
        self._load()
        tasks = list(self._tasks.values())
        if mission_id is not None:
            tasks = [t for t in tasks if t.mission_id == mission_id]
        return tasks

    def list_events(self, entity_id: str | None = None) -> list[LedgerEvent]:
        """Return ledger events, optionally scoped to one entity."""
        self._load()
        events = list(self._events)
        if entity_id is not None:
            events = [e for e in events if e.entity_id == entity_id]
        return events

    def list_incidents(self, task_id: str | None = None) -> list[Incident]:
        """Return all incidents, optionally scoped to one task."""
        self._load()
        incidents = list(self._incidents.values())
        if task_id is not None:
            incidents = [i for i in incidents if i.task_id == task_id]
        return incidents

    def ready_tasks(self, mission_id: str | None = None) -> list[MissionTask]:
        return [t for t in self.list_tasks(mission_id) if t.status == TaskState.READY]

    def blocked_tasks(self, mission_id: str | None = None) -> list[MissionTask]:
        return [t for t in self.list_tasks(mission_id) if t.status == TaskState.BLOCKED]

    def claimed_tasks(self, mission_id: str | None = None) -> list[MissionTask]:
        return [t for t in self.list_tasks(mission_id) if t.status == TaskState.CLAIMED]

    def needs_verification_tasks(self, mission_id: str | None = None) -> list[MissionTask]:
        return [t for t in self.list_tasks(mission_id) if t.status == TaskState.NEEDS_VERIFICATION]

    def open_incidents(self, task_id: str | None = None) -> list[Incident]:
        self._load()
        incs = [i for i in self._incidents.values() if i.status == IncidentStatus.OPEN]
        if task_id is not None:
            incs = [i for i in incs if i.task_id == task_id]
        return incs

    def events_for(self, entity_id: str) -> list[LedgerEvent]:
        return self.list_events(entity_id)

    # ── Import / Export ──────────────────────────────────────────────────────

    def export_jsonl(self, path: Path) -> int:
        self._load()
        lines = [json.dumps(e.to_dict(), ensure_ascii=False) for e in self._events]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return len(lines)

    def import_jsonl(self, path: Path) -> int:
        """Append events from an external JSONL file into this ledger."""
        count = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._atomic_append(self._events_path, raw)
            count += 1
        self._invalidate()
        return count
