"""J3 — Agent-to-Agent (A2A) protocol adapter for Grimoire.

Maps Google A2A protocol primitives (Task, Message, Artifact) to
Grimoire Mission Ledger and Evidence Service.

A2A protocol reference: https://google.github.io/A2A/

Guardrails:
- External tasks are always subordinated to the ledger (no external closure).
- Completed A2A tasks land in NEEDS_VERIFICATION, not CLOSED.
- Secrets are never stored in artifacts (digest-only storage).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from grimoire.evidence.schemas import EvidenceItem, EvidenceKind, EvidenceProfile
from grimoire.evidence.service import EvidenceService
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import MissionState, MissionTask, TaskState, TaskType
from grimoire.traces.ledger import TraceLedger
from grimoire.traces.schemas import TraceOutcome

__all__ = [
    "A2AAdapter",
    "A2AArtifact",
    "A2AImportReport",
    "A2AMessage",
    "A2ATask",
    "A2ATaskState",
]


# ── A2A protocol schemas (minimal, protocol-compatible) ───────────────────────

class A2ATaskState(str):
    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


@dataclass(frozen=True, slots=True)
class A2AMessagePart:
    type: str  # "text" | "data" | "file"
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    mime_type: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type}
        if self.text:
            d["text"] = self.text
        if self.data:
            d["data"] = self.data
        if self.mime_type:
            d["mimeType"] = self.mime_type
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> A2AMessagePart:
        return cls(
            type=str(d.get("type", "text")),
            text=str(d.get("text", "")),
            data=dict(d.get("data") or {}),
            mime_type=str(d.get("mimeType", "")),
        )


@dataclass(frozen=True, slots=True)
class A2AMessage:
    role: str  # "user" | "agent"
    parts: tuple[A2AMessagePart, ...]

    @property
    def text(self) -> str:
        return " ".join(p.text for p in self.parts if p.type == "text" and p.text)

    def to_dict(self) -> dict[str, Any]:
        return {"role": self.role, "parts": [p.to_dict() for p in self.parts]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> A2AMessage:
        return cls(
            role=str(d.get("role", "agent")),
            parts=tuple(A2AMessagePart.from_dict(p) for p in d.get("parts", [])),
        )


@dataclass(frozen=True, slots=True)
class A2AArtifact:
    index: int
    parts: tuple[A2AMessagePart, ...]
    name: str = ""
    description: str = ""

    def text_content(self) -> str:
        return " ".join(p.text for p in self.parts if p.type == "text")

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "index": self.index,
            "parts": [p.to_dict() for p in self.parts],
        }
        if self.name:
            d["name"] = self.name
        if self.description:
            d["description"] = self.description
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> A2AArtifact:
        return cls(
            index=int(d.get("index", 0)),
            parts=tuple(A2AMessagePart.from_dict(p) for p in d.get("parts", [])),
            name=str(d.get("name", "")),
            description=str(d.get("description", "")),
        )


@dataclass(frozen=True, slots=True)
class A2ATask:
    id: str
    status_state: str
    session_id: str = ""
    input_message: A2AMessage | None = None
    artifacts: tuple[A2AArtifact, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.status_state in (
            A2ATaskState.COMPLETED, A2ATaskState.FAILED, A2ATaskState.CANCELED
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "sessionId": self.session_id,
            "status": {"state": self.status_state},
            "artifacts": [a.to_dict() for a in self.artifacts],
            "metadata": self.metadata,
        }
        if self.input_message is not None:
            d["input"] = self.input_message.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> A2ATask:
        status = d.get("status") or {}
        input_msg = A2AMessage.from_dict(d["input"]) if d.get("input") else None
        return cls(
            id=str(d["id"]),
            status_state=str(status.get("state", A2ATaskState.SUBMITTED)),
            session_id=str(d.get("sessionId", "")),
            input_message=input_msg,
            artifacts=tuple(A2AArtifact.from_dict(a) for a in d.get("artifacts", [])),
            metadata=dict(d.get("metadata") or {}),
        )


# ── Status mapping ────────────────────────────────────────────────────────────

# Fast-forward chains: from PROPOSED, the minimal sequence of transitions needed
# to reach each target state. Steps already passed are skipped on re-import.
_FAST_FORWARD_CHAINS: dict[TaskState, tuple[TaskState, ...]] = {
    TaskState.PROPOSED: (),
    TaskState.CANCELLED: (TaskState.CANCELLED,),
    TaskState.BLOCKED: (TaskState.READY, TaskState.BLOCKED),
    TaskState.RUNNING: (TaskState.READY, TaskState.CLAIMED, TaskState.RUNNING),
    TaskState.NEEDS_VERIFICATION: (
        TaskState.READY, TaskState.CLAIMED, TaskState.RUNNING, TaskState.NEEDS_VERIFICATION,
    ),
    TaskState.FAILED: (
        TaskState.READY, TaskState.CLAIMED, TaskState.RUNNING, TaskState.FAILED,
    ),
}

_A2A_TO_GRIMOIRE: dict[str, TaskState] = {
    A2ATaskState.SUBMITTED: TaskState.PROPOSED,
    A2ATaskState.WORKING: TaskState.RUNNING,
    A2ATaskState.INPUT_REQUIRED: TaskState.BLOCKED,
    A2ATaskState.COMPLETED: TaskState.NEEDS_VERIFICATION,  # guardrail: no external closure
    A2ATaskState.FAILED: TaskState.FAILED,
    A2ATaskState.CANCELED: TaskState.CANCELLED,
}

_GRIMOIRE_TO_A2A: dict[TaskState, str] = {
    TaskState.PROPOSED: A2ATaskState.SUBMITTED,
    TaskState.READY: A2ATaskState.SUBMITTED,
    TaskState.CLAIMED: A2ATaskState.WORKING,
    TaskState.RUNNING: A2ATaskState.WORKING,
    TaskState.BLOCKED: A2ATaskState.INPUT_REQUIRED,
    TaskState.NEEDS_VERIFICATION: A2ATaskState.COMPLETED,
    TaskState.CLOSED: A2ATaskState.COMPLETED,
    TaskState.FAILED: A2ATaskState.FAILED,
    TaskState.CANCELLED: A2ATaskState.CANCELED,
}


# ── Import report ─────────────────────────────────────────────────────────────

@dataclass
class A2AImportReport:
    a2a_task_id: str
    grimoire_task_id: str
    mission_id: str
    state_mapped: str = ""
    artifacts_imported: int = 0
    messages_recorded: int = 0
    errors: list[str] = field(default_factory=list)
    trace_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "a2a_task_id": self.a2a_task_id,
            "grimoire_task_id": self.grimoire_task_id,
            "mission_id": self.mission_id,
            "state_mapped": self.state_mapped,
            "artifacts_imported": self.artifacts_imported,
            "messages_recorded": self.messages_recorded,
            "errors": self.errors,
            "trace_id": self.trace_id,
        }


# ── Adapter ───────────────────────────────────────────────────────────────────

class A2AAdapter:
    """Adapter between A2A protocol tasks and the Grimoire Mission Ledger.

    Usage::

        adapter = A2AAdapter(ledger, evidence_svc)
        report = adapter.import_task(a2a_task, mission_id="MIS-001")

        # Export a Grimoire task as A2A status response
        a2a_status = adapter.export_task_status(grimoire_task)
    """

    def __init__(
        self,
        ledger: MissionLedger,
        evidence: EvidenceService | None = None,
        trace_ledger: TraceLedger | None = None,
    ) -> None:
        self._ledger = ledger
        self._evidence = evidence
        self._trace = trace_ledger

    def _stable_task_id(self, a2a_id: str) -> str:
        slug = a2a_id.lower().replace("/", "-").replace(":", "-")[:20]
        return f"GAO-a2a-{slug}"

    def import_task(
        self,
        a2a_task: A2ATask,
        *,
        mission_id: str,
        actor_id: str = "a2a-adapter",
        task_type: TaskType = TaskType.IMPLEMENTATION,
    ) -> A2AImportReport:
        """Import an A2A Task into the Grimoire Mission Ledger.

        - Maps status to the closest TaskState (no external closure guardrail).
        - Imports artifacts as EvidenceItems (if EvidenceService provided).
        - Records input message as a LedgerEvent.
        - Idempotent: existing task_id is updated, not duplicated.
        """
        stable_id = self._stable_task_id(a2a_task.id)
        report = A2AImportReport(
            a2a_task_id=a2a_task.id,
            grimoire_task_id=stable_id,
            mission_id=mission_id,
        )

        # Derive title from input message or fall back to A2A id
        title = a2a_task.id
        if a2a_task.input_message:
            msg_text = a2a_task.input_message.text
            if msg_text:
                title = msg_text[:80]

        # Create or reuse task
        existing = self._ledger.get_task(stable_id)
        if existing is None:
            mission = self._ledger.get_mission(mission_id)
            if mission is None:
                mission = self._ledger.create_mission(
                    f"A2A Session {a2a_task.session_id or a2a_task.id[:8]}",
                    origin="a2a-adapter",
                )
                self._ledger.transition_mission(mission.id, MissionState.OPEN, actor_id=actor_id)
                report.mission_id = mission.id
                mission_id = mission.id

            description_parts = [f"a2a_task_id: {a2a_task.id}"]
            if a2a_task.session_id:
                description_parts.append(f"session: {a2a_task.session_id}")
            existing = self._ledger.create_task(
                mission_id,
                title,
                type=task_type,
                description=" | ".join(description_parts),
                acceptance=(f"A2A task {a2a_task.id} verified",),
                task_id=stable_id,
            )

        # Map and apply status transitions (may require multi-step fast-forward)
        if a2a_task.status_state not in _A2A_TO_GRIMOIRE:
            report.errors.append(f"Unknown A2A task state: {a2a_task.status_state!r} — defaulting to PROPOSED")
        target_state = _A2A_TO_GRIMOIRE.get(a2a_task.status_state, TaskState.PROPOSED)
        if existing.status != target_state:
            chain = _FAST_FORWARD_CHAINS.get(target_state, (target_state,))
            # Skip steps the task has already passed
            start_idx = 0
            for idx, step in enumerate(chain):
                if step == existing.status:
                    start_idx = idx + 1
                    break
            for step in chain[start_idx:]:
                try:
                    self._ledger.transition_task(stable_id, step, actor_id=actor_id)
                except Exception as exc:
                    report.errors.append(f"State transition →{step}: {exc}")
                    break
        # Read back actual status after transitions (may differ from target if chain was partial)
        refreshed = self._ledger.get_task(stable_id)
        report.state_mapped = (refreshed.status if refreshed is not None else target_state).value

        # Record input message
        if a2a_task.input_message:
            msg_text = a2a_task.input_message.text
            self._ledger.append_event(
                "a2a.message",
                stable_id,
                "task",
                actor_id,
                {
                    "a2a_task_id": a2a_task.id,
                    "role": a2a_task.input_message.role,
                    "text": msg_text[:500] if msg_text else "",
                },
            )
            report.messages_recorded += 1

        # Import artifacts as evidence
        if self._evidence and a2a_task.artifacts:
            items: list[EvidenceItem] = []
            for artifact in a2a_task.artifacts:
                content = artifact.text_content()
                if not content:
                    content = json.dumps(artifact.to_dict(), ensure_ascii=False)[:500]
                item_id = f"a2a-art-{a2a_task.id[:8]}-{artifact.index}"
                items.append(EvidenceItem.from_text(
                    item_id,
                    EvidenceKind.REPORT,
                    content,
                    uri=f"a2a://{a2a_task.id}/artifacts/{artifact.index}",
                    summary=artifact.name or artifact.description or f"A2A artifact {artifact.index}",
                ))
            if items:
                try:
                    self._evidence.create_pack(
                        stable_id,
                        EvidenceProfile.LIGHT,
                        items,
                        workflow_instance_id=a2a_task.session_id or "",
                    )
                    report.artifacts_imported = len(items)
                except Exception as exc:
                    report.errors.append(f"Evidence pack creation: {exc}")

        # Emit normalized trace
        if self._trace:
            try:
                run_id = f"a2a-{a2a_task.id[:12]}"
                self._trace.record(
                    run_id=run_id,
                    workflow_instance_id=a2a_task.session_id or run_id,
                    mission_id=mission_id,
                    task_id=stable_id,
                    recipe_id="a2a.import",
                    outcome=TraceOutcome.SUCCESS,
                    started_at=datetime.now(tz=UTC).isoformat(),
                    tags=["a2a", a2a_task.status_state],
                )
                report.trace_id = run_id
            except Exception as exc:
                report.errors.append(f"Trace record: {exc}")

        return report

    def export_task_status(self, task: MissionTask) -> dict[str, Any]:
        """Export a Grimoire task as an A2A-compatible status response."""
        a2a_state = _GRIMOIRE_TO_A2A.get(task.status, A2ATaskState.WORKING)
        return {
            "id": task.id,
            "status": {
                "state": a2a_state,
                "timestamp": datetime.now(tz=UTC).isoformat(),
            },
            "metadata": {
                "grimoire_mission_id": task.mission_id,
                "grimoire_status": task.status.value,
                "grimoire_type": task.type.value,
            },
        }

    @staticmethod
    def normalize_trace(raw_a2a_payload: dict[str, Any]) -> dict[str, Any]:
        """Normalize a raw A2A webhook/event payload for trace storage.

        Strips any field that could contain PII or secrets, keeps only
        structural and status fields.
        """
        safe_fields = {"id", "sessionId", "status", "metadata"}
        normalized = {k: v for k, v in raw_a2a_payload.items() if k in safe_fields}

        # Redact artifacts content, keep only counts
        if "artifacts" in raw_a2a_payload:
            normalized["artifact_count"] = len(raw_a2a_payload.get("artifacts", []))

        # Hash sensitive input if present
        if "input" in raw_a2a_payload:
            payload_str = json.dumps(raw_a2a_payload["input"], sort_keys=True, ensure_ascii=False)
            normalized["input_digest"] = hashlib.sha256(payload_str.encode()).hexdigest()[:16]

        return normalized
