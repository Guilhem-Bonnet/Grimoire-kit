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
    DEFAULT_MAX_BUDGET,
    DEFAULT_MAX_TOOL_CALLS,
    Checkpoint,
    CheckpointState,
    ExecutionContext,
    RunEvent,
    RunEventType,
    SideEffect,
    WorkflowInstance,
    WorkflowStatus,
)

# Valid workflow status transitions.
# REFUSED mirrors ABORTED as a terminal, non-resumable stop: it is reachable
# from every in-flight status and leads nowhere (B11 — plafonds MAST par
# instance : au plafond, l'instance ne poursuit jamais silencieusement).
_WF_TRANSITIONS: dict[WorkflowStatus, frozenset[WorkflowStatus]] = {
    WorkflowStatus.CREATED: frozenset({WorkflowStatus.RUNNING, WorkflowStatus.ABORTED, WorkflowStatus.REFUSED}),
    WorkflowStatus.RUNNING: frozenset({
        WorkflowStatus.CHECKPOINTED,
        WorkflowStatus.PAUSED,
        WorkflowStatus.BLOCKED,
        WorkflowStatus.COMPLETED,
        WorkflowStatus.ABORTED,
        WorkflowStatus.REFUSED,
    }),
    WorkflowStatus.CHECKPOINTED: frozenset({WorkflowStatus.RUNNING, WorkflowStatus.ABORTED, WorkflowStatus.REFUSED}),
    WorkflowStatus.PAUSED: frozenset({WorkflowStatus.RUNNING, WorkflowStatus.ABORTED, WorkflowStatus.REFUSED}),
    WorkflowStatus.BLOCKED: frozenset({WorkflowStatus.RUNNING, WorkflowStatus.ABORTED, WorkflowStatus.REFUSED}),
    WorkflowStatus.COMPLETED: frozenset({WorkflowStatus.VERIFIED}),
    WorkflowStatus.VERIFIED: frozenset(),
    WorkflowStatus.ABORTED: frozenset(),
    WorkflowStatus.REFUSED: frozenset(),
}

# Statuses that stopped the instance short of completion: mediate_tool must
# refuse further tool calls without raising and without re-attempting an
# invalid transition once one of these is reached.
_STOPPED_STATUSES = frozenset({WorkflowStatus.ABORTED, WorkflowStatus.REFUSED})


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
        kernel.checkpoint(wfi.id, ctx, step_id="parse",
                          completed_steps=["read-source"], pending_steps=["lock"])
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
        max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
        max_budget: int = DEFAULT_MAX_BUDGET,
    ) -> WorkflowInstance:
        """Create a workflow instance with per-instance MAST caps (B11).

        ``max_tool_calls`` bounds the number of tool calls ``mediate_tool``
        will mediate for this instance — the kernel's only observable unit
        of "turn" (FM-1.3, circuit breaker). ``max_budget`` bounds the sum
        of ``cost`` seen across those calls — a call-count proxy by default
        (see ``DEFAULT_MAX_BUDGET`` in ``schemas.py``) unless a caller passes
        a real cost per call (FM-1.5, budget). Overridable per instance;
        the module-level defaults are the "reasonable default" caps.
        """
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
            max_tool_calls=max_tool_calls,
            max_budget=max_budget,
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

    def mediate_tool(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        ctx: ExecutionContext,
        wfi_id: str,
        *,
        cost: int = 1,
    ) -> bool:
        """Call the tool mediator if registered.  Returns True if tool execution is allowed.

        Enforces the instance's MAST caps (B11) before consulting the
        mediator: once ``max_tool_calls`` mediated calls or ``max_budget``
        cost units are exhausted, the instance is refused (terminal state,
        checkpoint written, ``workflow.refused`` event emitted) and this
        call — and every subsequent one — returns ``False`` without raising
        and without letting the instance keep running.  ``cost`` defaults to
        1 (a call-count proxy); pass a real cost when the caller has one.

        The caps are checked *before* ``tool.requested`` is emitted: a call
        that will be refused for being over cap never appears in the event
        log as requested, only as blocked — the log stays an accurate record
        of what was actually mediated. ``cost`` must be non-negative; a
        negative value would let a caller shrink ``budget_used`` and defeat
        the cap, so it raises ``ValueError`` instead.
        """
        if cost < 0:
            raise ValueError(f"cost must be non-negative, got {cost}")

        instances = self._load_instances()
        wfi = instances.get(wfi_id)
        if wfi is None:
            raise GrimoireRuntimeError(f"WorkflowInstance not found: {wfi_id}")

        if wfi.status in _STOPPED_STATUSES:
            self._emit(RunEventType.TOOL_BLOCKED, wfi, ctx, payload={"tool_name": tool_name, "reason": f"instance already {wfi.status.value}"})
            return False

        next_calls = wfi.tool_calls_used + 1
        next_budget = wfi.budget_used + cost
        if next_calls > wfi.max_tool_calls or next_budget > wfi.max_budget:
            over_calls = next_calls > wfi.max_tool_calls
            reason = (
                f"plafond MAST atteint pour {wfi.id} — "
                f"appels d'outils médiés {next_calls}/{wfi.max_tool_calls}"
                f"{' (dépassé)' if over_calls else ''}, "
                f"budget {next_budget}/{wfi.max_budget}"
                f"{' (dépassé)' if not over_calls else ''}"
            )
            self._refuse(wfi, ctx, reason=reason)
            self._emit(RunEventType.TOOL_BLOCKED, wfi, ctx, payload={"tool_name": tool_name, "reason": "mast_cap_exceeded"})
            return False

        self._emit(RunEventType.TOOL_REQUESTED, wfi, ctx, payload={"tool_name": tool_name, "args": tool_args})

        wfi = WorkflowInstance.from_dict({
            **wfi.to_dict(),
            "caps": {
                "max_tool_calls": wfi.max_tool_calls,
                "max_budget": wfi.max_budget,
                "tool_calls_used": next_calls,
                "budget_used": next_budget,
            },
        })
        self._save_instance(wfi)

        allowed = self._tool_mediator(tool_name, tool_args, ctx) if self._tool_mediator is not None else True
        if allowed:
            self._emit(RunEventType.TOOL_COMPLETED, wfi, ctx, payload={"tool_name": tool_name})
        else:
            self._emit(RunEventType.TOOL_BLOCKED, wfi, ctx, payload={"tool_name": tool_name})
        return allowed

    def _refuse(self, wfi: WorkflowInstance, ctx: ExecutionContext, *, reason: str) -> WorkflowInstance:
        """Transition an instance to the terminal REFUSED status (B11).

        Writes a checkpoint (so replay/audit can see exactly where the
        instance stopped) before flipping the status, then emits
        ``WORKFLOW_REFUSED``. Never raises — a refusal is an ordinary,
        observable outcome, not an error.
        """
        chk = Checkpoint(
            id=f"chk-{wfi.id}-refused-{uuid.uuid4().hex[:6]}",
            workflow_instance_id=wfi.id,
            run_id=wfi.run_id,
            step_id="refused",
            state=CheckpointState(completed_steps=(), pending_steps=(), side_effects=()),
            created_at=_now_iso(),
            idempotency_key=f"idem-{wfi.id}-refused",
            safe_to_resume=False,
        )
        self._append_checkpoint(chk)
        updated_refs = (*wfi.checkpoint_refs, chk.id)
        wfi = self._transition(wfi, WorkflowStatus.REFUSED, abort_reason=reason)
        wfi = WorkflowInstance.from_dict({**wfi.to_dict(), "checkpoint_refs": list(updated_refs)})
        self._save_instance(wfi)
        self._emit(
            RunEventType.WORKFLOW_REFUSED,
            wfi,
            ctx,
            payload={
                "reason": reason,
                "checkpoint_id": chk.id,
                "tool_calls_used": wfi.tool_calls_used,
                "max_tool_calls": wfi.max_tool_calls,
                "budget_used": wfi.budget_used,
                "max_budget": wfi.max_budget,
            },
        )
        return wfi

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

    def list_checkpoints(self, wfi_id: str | None = None) -> list[Checkpoint]:
        """Checkpoints dans l'ordre d'écriture, restreints à une instance si demandé."""
        out: list[Checkpoint] = []
        if not self._checkpoints_path.exists():
            return out
        for line in self._checkpoints_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                chk = Checkpoint.from_dict(json.loads(line))
            except (json.JSONDecodeError, KeyError):
                continue
            if wfi_id is None or chk.workflow_instance_id == wfi_id:
                out.append(chk)
        return out

    def _latest_checkpoint(self, wfi_id: str) -> Checkpoint | None:
        checkpoints = self.list_checkpoints(wfi_id)
        return checkpoints[-1] if checkpoints else None

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
