"""Runtime Kernel — manages WorkflowInstance lifecycle with checkpointing and replay."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.core.exceptions import GrimoireRuntimeError
from grimoire.runtime.schemas import (
    Checkpoint,
    CheckpointState,
    ExecutionContext,
    RunEvent,
    RunEventType,
    SideEffect,
    WorkflowInstance,
    WorkflowStatus,
)

# Valid workflow status transitions
_WF_TRANSITIONS: dict[WorkflowStatus, frozenset[WorkflowStatus]] = {
    WorkflowStatus.CREATED: frozenset({WorkflowStatus.RUNNING, WorkflowStatus.ABORTED}),
    WorkflowStatus.RUNNING: frozenset({
        WorkflowStatus.CHECKPOINTED,
        WorkflowStatus.PAUSED,
        WorkflowStatus.BLOCKED,
        WorkflowStatus.COMPLETED,
        WorkflowStatus.ABORTED,
    }),
    WorkflowStatus.CHECKPOINTED: frozenset({WorkflowStatus.RUNNING, WorkflowStatus.ABORTED}),
    WorkflowStatus.PAUSED: frozenset({WorkflowStatus.RUNNING, WorkflowStatus.ABORTED}),
    WorkflowStatus.BLOCKED: frozenset({WorkflowStatus.RUNNING, WorkflowStatus.ABORTED}),
    WorkflowStatus.COMPLETED: frozenset({WorkflowStatus.VERIFIED}),
    WorkflowStatus.VERIFIED: frozenset(),
    WorkflowStatus.ABORTED: frozenset(),
}


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


class RuntimeKernel:
    """Manages workflow instance lifecycle, checkpoints, and run events.

    All state is persisted to JSONL files in the root directory.
    The tool mediator hook allows callers to inject policy checks before tool execution.

    Usage::

        kernel = RuntimeKernel(Path("_grimoire-runtime-output/runtime"))
        ctx = ExecutionContext(run_id=..., mission_id=..., ...)
        wfi = kernel.create_instance(ctx, recipe_id="recipe.pack.convert-gascity")
        kernel.start(wfi.id, ctx)
        kernel.checkpoint(wfi.id, ctx, step_id="parse", completed=["read-source"], pending=["lock"])
        kernel.complete(wfi.id, ctx)
    """

    def __init__(self, root: Path, tool_mediator: Callable[[str, dict[str, Any], ExecutionContext], bool] | None = None) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._instances_path = root / "instances.jsonl"
        self._events_path = root / "run_events.jsonl"
        self._checkpoints_path = root / "checkpoints.jsonl"
        self._tool_mediator = tool_mediator

    # ── Private helpers ────────────────────────────────────────────────────

    def _load_instances(self) -> dict[str, WorkflowInstance]:
        instances: dict[str, WorkflowInstance] = {}
        if not self._instances_path.exists():
            return instances
        for line in self._instances_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
                wfi = WorkflowInstance.from_dict(raw)
                instances[wfi.id] = wfi
            except (json.JSONDecodeError, KeyError):
                pass
        return instances

    def _save_instance(self, wfi: WorkflowInstance) -> None:
        """Replace the serialized instance in the instances file."""
        instances = self._load_instances()
        instances[wfi.id] = wfi
        lines = [json.dumps(v.to_dict(), ensure_ascii=False) for v in instances.values()]
        self._instances_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _append_event(self, event: RunEvent) -> None:
        line = json.dumps(event.to_dict(), ensure_ascii=False) + "\n"
        with open(self._events_path, "a", encoding="utf-8") as fh:
            fh.write(line)

    def _append_checkpoint(self, chk: Checkpoint) -> None:
        line = json.dumps(chk.to_dict(), ensure_ascii=False) + "\n"
        with open(self._checkpoints_path, "a", encoding="utf-8") as fh:
            fh.write(line)

    def _emit(self, event_type: RunEventType, wfi: WorkflowInstance, ctx: ExecutionContext, payload: dict[str, Any] | None = None) -> RunEvent:
        event = RunEvent(
            id=f"evt-{uuid.uuid4().hex[:12]}",
            run_id=wfi.run_id or ctx.run_id,
            mission_id=wfi.mission_id,
            task_id=wfi.task_id,
            workflow_instance_id=wfi.id,
            event_type=event_type,
            actor_id=ctx.actor_id,
            host_id=ctx.host_id,
            created_at=_now_iso(),
            payload=payload or {},
            span_id=f"span-{event_type.value}-{uuid.uuid4().hex[:6]}",
        )
        self._append_event(event)
        return event

    def _transition(self, wfi: WorkflowInstance, to_status: WorkflowStatus, abort_reason: str = "") -> WorkflowInstance:
        allowed = _WF_TRANSITIONS.get(wfi.status, frozenset())
        if to_status not in allowed:
            raise GrimoireRuntimeError(
                f"Invalid workflow transition {wfi.status.value} → {to_status.value} for {wfi.id}"
            )
        updated = WorkflowInstance.from_dict({
            **wfi.to_dict(),
            "status": to_status.value,
            "abort_reason": abort_reason or wfi.abort_reason,
        })
        self._save_instance(updated)
        return updated

    # ── Public API ─────────────────────────────────────────────────────────

    def create_instance(
        self,
        ctx: ExecutionContext,
        recipe_id: str,
        *,
        recipe_version: str = "",
        wfi_id: str | None = None,
    ) -> WorkflowInstance:
        run_id = ctx.run_id or f"RUN-{uuid.uuid4().hex[:12]}"
        instances = self._load_instances()
        if wfi_id is None:
            slug = recipe_id.replace(".", "-")[-16:]
            seq = sum(1 for k in instances if k.startswith(f"WFI-{slug}")) + 1
            wfi_id = f"WFI-{slug}-{seq:03d}"
        wfi = WorkflowInstance(
            id=wfi_id,
            recipe_id=recipe_id,
            recipe_version=recipe_version,
            mission_id=ctx.mission_id,
            task_id=ctx.task_id,
            run_id=run_id,
            status=WorkflowStatus.CREATED,
            host_id=ctx.host_id,
            actor_id=ctx.actor_id,
            created_at=_now_iso(),
        )
        self._save_instance(wfi)
        return wfi

    def start(self, wfi_id: str, ctx: ExecutionContext) -> WorkflowInstance:
        instances = self._load_instances()
        wfi = instances.get(wfi_id)
        if wfi is None:
            raise GrimoireRuntimeError(f"WorkflowInstance not found: {wfi_id}")
        wfi = self._transition(wfi, WorkflowStatus.RUNNING)
        self._emit(RunEventType.WORKFLOW_STARTED, wfi, ctx)
        return wfi

    def checkpoint(
        self,
        wfi_id: str,
        ctx: ExecutionContext,
        *,
        step_id: str,
        completed_steps: list[str],
        pending_steps: list[str],
        side_effects: list[dict[str, Any]] | None = None,
        evidence_refs: list[str] | None = None,
    ) -> tuple[WorkflowInstance, Checkpoint]:
        instances = self._load_instances()
        wfi = instances.get(wfi_id)
        if wfi is None:
            raise GrimoireRuntimeError(f"WorkflowInstance not found: {wfi_id}")
        state = CheckpointState(
            completed_steps=tuple(completed_steps),
            pending_steps=tuple(pending_steps),
            side_effects=tuple(SideEffect.from_dict(se) for se in (side_effects or [])),
        )
        idempotency_key = f"idem-{wfi_id}-{step_id}"
        chk = Checkpoint(
            id=f"chk-{wfi_id}-{step_id}",
            workflow_instance_id=wfi_id,
            run_id=wfi.run_id,
            step_id=step_id,
            state=state,
            created_at=_now_iso(),
            idempotency_key=idempotency_key,
            safe_to_resume=True,
            evidence_refs=tuple(evidence_refs or []),
        )
        self._append_checkpoint(chk)
        updated_refs = (*wfi.checkpoint_refs, chk.id)
        wfi = self._transition(wfi, WorkflowStatus.CHECKPOINTED)
        # Store the new checkpoint ref
        wfi = WorkflowInstance.from_dict({**wfi.to_dict(), "checkpoint_refs": list(updated_refs)})
        self._save_instance(wfi)
        self._emit(RunEventType.CHECKPOINT_SAVED, wfi, ctx, payload={"checkpoint_id": chk.id, "step_id": step_id})
        return wfi, chk

    def resume_from_checkpoint(self, wfi_id: str, ctx: ExecutionContext) -> tuple[WorkflowInstance, Checkpoint | None]:
        instances = self._load_instances()
        wfi = instances.get(wfi_id)
        if wfi is None:
            raise GrimoireRuntimeError(f"WorkflowInstance not found: {wfi_id}")
        chk = self._latest_checkpoint(wfi_id)
        if chk is not None and not chk.safe_to_resume:
            raise GrimoireRuntimeError(f"Latest checkpoint {chk.id} is not safe to resume")
        wfi = self._transition(wfi, WorkflowStatus.RUNNING)
        self._emit(RunEventType.CHECKPOINT_RESUMED, wfi, ctx, payload={"checkpoint_id": chk.id if chk else None})
        return wfi, chk

    def mediate_tool(self, tool_name: str, tool_args: dict[str, Any], ctx: ExecutionContext, wfi_id: str) -> bool:
        """Call the tool mediator if registered.  Returns True if tool execution is allowed."""
        instances = self._load_instances()
        wfi = instances.get(wfi_id)
        if wfi is None:
            raise GrimoireRuntimeError(f"WorkflowInstance not found: {wfi_id}")
        self._emit(RunEventType.TOOL_REQUESTED, wfi, ctx, payload={"tool_name": tool_name, "args": tool_args})
        allowed = self._tool_mediator(tool_name, tool_args, ctx) if self._tool_mediator is not None else True
        if allowed:
            self._emit(RunEventType.TOOL_COMPLETED, wfi, ctx, payload={"tool_name": tool_name})
        else:
            self._emit(RunEventType.TOOL_BLOCKED, wfi, ctx, payload={"tool_name": tool_name})
        return allowed

    def complete(self, wfi_id: str, ctx: ExecutionContext, *, evidence_pack_id: str = "") -> WorkflowInstance:
        instances = self._load_instances()
        wfi = instances.get(wfi_id)
        if wfi is None:
            raise GrimoireRuntimeError(f"WorkflowInstance not found: {wfi_id}")
        if wfi.status == WorkflowStatus.CHECKPOINTED:
            wfi = self._transition(wfi, WorkflowStatus.RUNNING)
        wfi = self._transition(wfi, WorkflowStatus.COMPLETED)
        if evidence_pack_id:
            wfi = WorkflowInstance.from_dict({**wfi.to_dict(), "evidence_pack_id": evidence_pack_id})
            self._save_instance(wfi)
        self._emit(RunEventType.WORKFLOW_COMPLETED, wfi, ctx)
        return wfi

    def abort(self, wfi_id: str, ctx: ExecutionContext, *, reason: str = "") -> WorkflowInstance:
        instances = self._load_instances()
        wfi = instances.get(wfi_id)
        if wfi is None:
            raise GrimoireRuntimeError(f"WorkflowInstance not found: {wfi_id}")
        wfi = self._transition(wfi, WorkflowStatus.ABORTED, abort_reason=reason)
        self._emit(RunEventType.WORKFLOW_ABORTED, wfi, ctx, payload={"reason": reason})
        return wfi

    # ── Queries ─────────────────────────────────────────────────────────────

    def get_instance(self, wfi_id: str) -> WorkflowInstance | None:
        return self._load_instances().get(wfi_id)

    def list_instances(self, task_id: str | None = None) -> list[WorkflowInstance]:
        instances = list(self._load_instances().values())
        if task_id is not None:
            instances = [w for w in instances if w.task_id == task_id]
        return instances

    def _latest_checkpoint(self, wfi_id: str) -> Checkpoint | None:
        if not self._checkpoints_path.exists():
            return None
        latest: Checkpoint | None = None
        for line in self._checkpoints_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
                chk = Checkpoint.from_dict(raw)
                if chk.workflow_instance_id == wfi_id:
                    latest = chk
            except (json.JSONDecodeError, KeyError):
                pass
        return latest

    def get_run_events(self, wfi_id: str | None = None) -> list[RunEvent]:
        events: list[RunEvent] = []
        if not self._events_path.exists():
            return events
        for line in self._events_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
                evt = RunEvent.from_dict(raw)
                if wfi_id is None or evt.workflow_instance_id == wfi_id:
                    events.append(evt)
            except (json.JSONDecodeError, KeyError):
                pass
        return events
