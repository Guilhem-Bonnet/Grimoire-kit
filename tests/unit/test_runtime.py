"""Tests for the Runtime Kernel module."""

from __future__ import annotations

import pytest

from grimoire.core.exceptions import GrimoireRuntimeError
from grimoire.runtime.kernel import RuntimeKernel
from grimoire.runtime.schemas import (
    ExecutionContext,
    RunEventType,
    WorkflowStatus,
)


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        run_id="RUN-test",
        mission_id="MIS-test-001",
        task_id="GAO-test-001",
        workflow_instance_id="",
        actor_id="agent",
        host_id="host-test",
        risk_profile="standard",
    )


@pytest.fixture
def kernel(tmp_path):
    return RuntimeKernel(tmp_path / "runtime")


def test_create_instance(kernel):
    ctx = _ctx()
    wfi = kernel.create_instance(ctx, recipe_id="recipe.test")
    assert wfi.id.startswith("WFI-")
    assert wfi.status == WorkflowStatus.CREATED
    assert wfi.recipe_id == "recipe.test"


def test_start_transitions_to_running(kernel):
    ctx = _ctx()
    wfi = kernel.create_instance(ctx, recipe_id="recipe.test")
    wfi = kernel.start(wfi.id, ctx)
    assert wfi.status == WorkflowStatus.RUNNING


def test_invalid_transition_raises(kernel):
    ctx = _ctx()
    wfi = kernel.create_instance(ctx, recipe_id="recipe.test")
    with pytest.raises(GrimoireRuntimeError, match="Invalid workflow transition"):
        kernel.complete(wfi.id, ctx)


def test_checkpoint_saves_state(kernel):
    ctx = _ctx()
    wfi = kernel.create_instance(ctx, recipe_id="recipe.test")
    kernel.start(wfi.id, ctx)
    wfi, chk = kernel.checkpoint(
        wfi.id,
        ctx,
        step_id="parse",
        completed_steps=["read-source", "parse"],
        pending_steps=["lock", "doctor"],
    )
    assert wfi.status == WorkflowStatus.CHECKPOINTED
    assert chk.step_id == "parse"
    assert chk.state.completed_steps == ("read-source", "parse")
    assert chk.idempotency_key == f"idem-{wfi.id}-parse"
    assert chk.id in wfi.checkpoint_refs


def test_resume_from_checkpoint(kernel):
    ctx = _ctx()
    wfi = kernel.create_instance(ctx, recipe_id="recipe.test")
    kernel.start(wfi.id, ctx)
    kernel.checkpoint(wfi.id, ctx, step_id="s1", completed_steps=["s1"], pending_steps=["s2"])
    wfi, chk = kernel.resume_from_checkpoint(wfi.id, ctx)
    assert wfi.status == WorkflowStatus.RUNNING
    assert chk is not None
    assert chk.step_id == "s1"


def test_complete_workflow(kernel):
    ctx = _ctx()
    wfi = kernel.create_instance(ctx, recipe_id="recipe.test")
    kernel.start(wfi.id, ctx)
    wfi = kernel.complete(wfi.id, ctx, evidence_pack_id="EVD-GAO-test-001-001")
    assert wfi.status == WorkflowStatus.COMPLETED
    assert wfi.evidence_pack_id == "EVD-GAO-test-001-001"


def test_abort_workflow(kernel):
    ctx = _ctx()
    wfi = kernel.create_instance(ctx, recipe_id="recipe.test")
    kernel.start(wfi.id, ctx)
    wfi = kernel.abort(wfi.id, ctx, reason="policy block")
    assert wfi.status == WorkflowStatus.ABORTED
    assert wfi.abort_reason == "policy block"


def test_run_events_emitted(kernel):
    ctx = _ctx()
    wfi = kernel.create_instance(ctx, recipe_id="recipe.test")
    kernel.start(wfi.id, ctx)
    kernel.complete(wfi.id, ctx)
    events = kernel.get_run_events(wfi.id)
    event_types = [e.event_type for e in events]
    assert RunEventType.WORKFLOW_STARTED in event_types
    assert RunEventType.WORKFLOW_COMPLETED in event_types


def test_tool_mediation_allowed(kernel):
    ctx = _ctx()
    wfi = kernel.create_instance(ctx, recipe_id="recipe.test")
    kernel.start(wfi.id, ctx)
    allowed = kernel.mediate_tool("filesystem.read", {}, ctx, wfi.id)
    assert allowed is True


def test_tool_mediation_blocked_by_mediator(kernel, tmp_path):
    def blocking_mediator(tool: str, args: dict, ctx: ExecutionContext) -> bool:
        return tool != "shell"

    k = RuntimeKernel(tmp_path / "runtime2", tool_mediator=blocking_mediator)
    ctx = _ctx()
    wfi = k.create_instance(ctx, recipe_id="recipe.test")
    k.start(wfi.id, ctx)
    assert k.mediate_tool("filesystem.read", {}, ctx, wfi.id) is True
    assert k.mediate_tool("shell", {}, ctx, wfi.id) is False


def test_list_instances_filter_by_task(kernel):
    ctx = _ctx()
    wfi = kernel.create_instance(ctx, recipe_id="recipe.test")
    results = kernel.list_instances(task_id="GAO-test-001")
    assert any(w.id == wfi.id for w in results)
    results_other = kernel.list_instances(task_id="GAO-other-001")
    assert not results_other
