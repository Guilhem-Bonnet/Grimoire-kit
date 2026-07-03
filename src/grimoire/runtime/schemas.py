"""Runtime Kernel schemas: WorkflowInstance, Checkpoint, RunEvent, ExecutionContext."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class WorkflowStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    CHECKPOINTED = "checkpointed"
    PAUSED = "paused"
    BLOCKED = "blocked"
    ABORTED = "aborted"
    COMPLETED = "completed"
    VERIFIED = "verified"


class RunEventType(str, Enum):
    WORKFLOW_STARTED = "workflow.started"
    WORKFLOW_COMPLETED = "workflow.completed"
    WORKFLOW_ABORTED = "workflow.aborted"
    STEP_STARTED = "step.started"
    STEP_COMPLETED = "step.completed"
    STEP_FAILED = "step.failed"
    TOOL_REQUESTED = "tool.requested"
    TOOL_COMPLETED = "tool.completed"
    TOOL_BLOCKED = "tool.blocked"
    CHECKPOINT_SAVED = "checkpoint.saved"
    CHECKPOINT_RESUMED = "checkpoint.resumed"


@dataclass(frozen=True, slots=True)
class WorkflowInstance:
    id: str
    recipe_id: str
    mission_id: str
    task_id: str
    status: WorkflowStatus
    created_at: str
    schema_version: str = "grimoire.workflow_instance.v1"
    recipe_version: str = ""
    run_id: str = ""
    host_id: str = ""
    actor_id: str = ""
    inputs_ref: str = ""
    outputs_ref: str = ""
    checkpoint_refs: tuple[str, ...] = ()
    evidence_pack_id: str = ""
    abort_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "schema_version": self.schema_version,
            "recipe_id": self.recipe_id,
            "recipe_version": self.recipe_version,
            "mission_id": self.mission_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "status": self.status.value,
            "host_id": self.host_id,
            "actor_id": self.actor_id,
            "inputs_ref": self.inputs_ref,
            "outputs_ref": self.outputs_ref,
            "checkpoint_refs": list(self.checkpoint_refs),
            "evidence_pack_id": self.evidence_pack_id,
            "abort_reason": self.abort_reason,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WorkflowInstance:
        return cls(
            id=d["id"],
            recipe_id=d["recipe_id"],
            mission_id=d["mission_id"],
            task_id=d["task_id"],
            status=WorkflowStatus(d["status"]),
            created_at=d["created_at"],
            schema_version=d.get("schema_version", "grimoire.workflow_instance.v1"),
            recipe_version=d.get("recipe_version", ""),
            run_id=d.get("run_id", ""),
            host_id=d.get("host_id", ""),
            actor_id=d.get("actor_id", ""),
            inputs_ref=d.get("inputs_ref", ""),
            outputs_ref=d.get("outputs_ref", ""),
            checkpoint_refs=tuple(d.get("checkpoint_refs", [])),
            evidence_pack_id=d.get("evidence_pack_id", ""),
            abort_reason=d.get("abort_reason", ""),
        )


@dataclass(frozen=True, slots=True)
class SideEffect:
    path: str
    mutation: str
    digest: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "mutation": self.mutation, "digest": self.digest}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SideEffect:
        return cls(path=d["path"], mutation=d["mutation"], digest=d.get("digest", ""))


@dataclass(frozen=True, slots=True)
class CheckpointState:
    completed_steps: tuple[str, ...]
    pending_steps: tuple[str, ...]
    side_effects: tuple[SideEffect, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "completed_steps": list(self.completed_steps),
            "pending_steps": list(self.pending_steps),
            "side_effects": [se.to_dict() for se in self.side_effects],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CheckpointState:
        return cls(
            completed_steps=tuple(d.get("completed_steps", [])),
            pending_steps=tuple(d.get("pending_steps", [])),
            side_effects=tuple(SideEffect.from_dict(se) for se in d.get("side_effects", [])),
        )


@dataclass(frozen=True, slots=True)
class Checkpoint:
    id: str
    workflow_instance_id: str
    run_id: str
    step_id: str
    state: CheckpointState
    created_at: str
    schema_version: str = "grimoire.checkpoint.v1"
    idempotency_key: str = ""
    safe_to_resume: bool = True
    evidence_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "schema_version": self.schema_version,
            "workflow_instance_id": self.workflow_instance_id,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "state": self.state.to_dict(),
            "resume": {
                "idempotency_key": self.idempotency_key,
                "safe_to_resume": self.safe_to_resume,
            },
            "evidence_refs": list(self.evidence_refs),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Checkpoint:
        resume = d.get("resume", {})
        return cls(
            id=d["id"],
            workflow_instance_id=d["workflow_instance_id"],
            run_id=d["run_id"],
            step_id=d["step_id"],
            state=CheckpointState.from_dict(d.get("state", {})),
            created_at=d["created_at"],
            schema_version=d.get("schema_version", "grimoire.checkpoint.v1"),
            idempotency_key=resume.get("idempotency_key", ""),
            safe_to_resume=resume.get("safe_to_resume", True),
            evidence_refs=tuple(d.get("evidence_refs", [])),
        )


@dataclass(frozen=True, slots=True)
class RunEvent:
    id: str
    run_id: str
    mission_id: str
    task_id: str
    workflow_instance_id: str
    event_type: RunEventType
    actor_id: str
    host_id: str
    created_at: str
    schema_version: str = "grimoire.run_event.v1"
    payload: dict[str, Any] = field(default_factory=dict)
    policy_verdict_id: str = ""
    span_id: str = ""
    parent_span_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "mission_id": self.mission_id,
            "task_id": self.task_id,
            "workflow_instance_id": self.workflow_instance_id,
            "event_type": self.event_type.value,
            "actor": {"actor_id": self.actor_id, "host_id": self.host_id},
            "payload": dict(self.payload),
            "policy": {"verdict_id": self.policy_verdict_id} if self.policy_verdict_id else {},
            "trace": {"span_id": self.span_id, "parent_span_id": self.parent_span_id},
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RunEvent:
        actor = d.get("actor", {})
        trace = d.get("trace", {})
        policy = d.get("policy", {})
        return cls(
            id=d["id"],
            run_id=d["run_id"],
            mission_id=d["mission_id"],
            task_id=d["task_id"],
            workflow_instance_id=d["workflow_instance_id"],
            event_type=RunEventType(d["event_type"]),
            actor_id=actor.get("actor_id", ""),
            host_id=actor.get("host_id", ""),
            created_at=d["created_at"],
            schema_version=d.get("schema_version", "grimoire.run_event.v1"),
            payload=d.get("payload", {}),
            policy_verdict_id=policy.get("verdict_id", ""),
            span_id=trace.get("span_id", ""),
            parent_span_id=trace.get("parent_span_id", ""),
        )


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """Current execution context passed to tool mediator."""

    run_id: str
    mission_id: str
    task_id: str
    workflow_instance_id: str
    actor_id: str
    host_id: str
    risk_profile: str
    step_id: str = ""
    idempotency_key: str = ""
    variables: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "mission_id": self.mission_id,
            "task_id": self.task_id,
            "workflow_instance_id": self.workflow_instance_id,
            "actor_id": self.actor_id,
            "host_id": self.host_id,
            "risk_profile": self.risk_profile,
            "step_id": self.step_id,
            "idempotency_key": self.idempotency_key,
            "variables": dict(self.variables),
        }
