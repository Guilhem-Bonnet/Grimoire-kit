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


# --- #446 : wfi_id/run_id ne tronque plus silencieusement recipe_id --------


def test_create_instance_does_not_truncate_recipe_id(kernel):
    """Un recipe_id de 17 caractères tenait déjà dans l'ancienne coupe à 16,

    perdant son premier caractère (``tasklib-hardening`` -> ``asklib-hardening``,
    #446). Le slug garde désormais l'identifiant complet."""
    ctx = _ctx()
    wfi = kernel.create_instance(ctx, recipe_id="tasklib-hardening")
    assert wfi.id == "WFI-tasklib-hardening-001"


def test_create_instance_distinct_recipes_never_collide_on_wfi_id(tmp_path):
    """Deux blueprints dont les 16 derniers caractères coïncidaient après la

    coupe (#446) obtenaient exactement le même wfi_id sur un kernel neuf —
    reproduit ici avec deux kernels indépendants (deux hôtes/dispatches
    distincts), le cas le plus sévère : pas de compteur de séquence commun
    pour même accidentellement les distinguer."""
    ctx = _ctx()
    kernel_a = RuntimeKernel(tmp_path / "runtime-a")
    kernel_b = RuntimeKernel(tmp_path / "runtime-b")
    wfi_a = kernel_a.create_instance(ctx, recipe_id="tasklib-hardening")
    wfi_b = kernel_b.create_instance(ctx, recipe_id="xasklib-hardening")
    assert wfi_a.id != wfi_b.id
    assert wfi_a.id == "WFI-tasklib-hardening-001"
    assert wfi_b.id == "WFI-xasklib-hardening-001"


def test_create_instance_long_recipe_ids_disambiguated_by_hash(kernel):
    """Au-delà de la borne de longueur, deux id qui ne diffèrent qu'après la

    coupe restent distincts grâce à l'empreinte du recipe_id complet — pas
    une simple troncature muette."""
    ctx = _ctx()
    long_a = "projet-" + "y" * 60 + "-variante-alpha"
    long_b = "projet-" + "y" * 60 + "-variante-beta"
    wfi_a = kernel.create_instance(ctx, recipe_id=long_a)
    wfi_b = kernel.create_instance(ctx, recipe_id=long_b)
    assert wfi_a.id != wfi_b.id
    assert wfi_a.recipe_id == long_a
    assert wfi_b.recipe_id == long_b


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


# --- B11 : plafonds MAST par instance (tours/appels d'outils médiés + budget) ---


def test_create_instance_has_default_caps(kernel):
    ctx = _ctx()
    wfi = kernel.create_instance(ctx, recipe_id="recipe.test")
    assert wfi.max_tool_calls > 0
    assert wfi.max_budget > 0
    assert wfi.tool_calls_used == 0
    assert wfi.budget_used == 0


def test_create_instance_accepts_custom_caps(kernel):
    ctx = _ctx()
    wfi = kernel.create_instance(ctx, recipe_id="recipe.test", max_tool_calls=3, max_budget=30)
    assert wfi.max_tool_calls == 3
    assert wfi.max_budget == 30


def test_mediate_tool_counts_calls_and_stays_running_under_cap(kernel):
    ctx = _ctx()
    wfi = kernel.create_instance(ctx, recipe_id="recipe.test", max_tool_calls=2, max_budget=1000)
    kernel.start(wfi.id, ctx)
    assert kernel.mediate_tool("filesystem.read", {}, ctx, wfi.id) is True
    updated = kernel.get_instance(wfi.id)
    assert updated.tool_calls_used == 1
    assert updated.status == WorkflowStatus.RUNNING


def test_mediate_tool_refuses_when_tool_call_cap_exceeded(kernel):
    ctx = _ctx()
    wfi = kernel.create_instance(ctx, recipe_id="recipe.test", max_tool_calls=2, max_budget=1000)
    kernel.start(wfi.id, ctx)
    assert kernel.mediate_tool("filesystem.read", {}, ctx, wfi.id) is True
    assert kernel.mediate_tool("filesystem.read", {}, ctx, wfi.id) is True
    # Third call exceeds the cap of 2 mediated tool calls.
    assert kernel.mediate_tool("filesystem.read", {}, ctx, wfi.id) is False
    refused = kernel.get_instance(wfi.id)
    assert refused.status == WorkflowStatus.REFUSED
    assert refused.abort_reason


def test_mediate_tool_refuses_when_budget_cap_exceeded(kernel):
    ctx = _ctx()
    wfi = kernel.create_instance(ctx, recipe_id="recipe.test", max_tool_calls=1000, max_budget=5)
    kernel.start(wfi.id, ctx)
    assert kernel.mediate_tool("shell", {}, ctx, wfi.id, cost=5) is True
    # Budget is exhausted; one more unit of cost tips it over.
    assert kernel.mediate_tool("shell", {}, ctx, wfi.id, cost=1) is False
    refused = kernel.get_instance(wfi.id)
    assert refused.status == WorkflowStatus.REFUSED


def test_cap_refusal_writes_checkpoint_and_event(kernel):
    ctx = _ctx()
    wfi = kernel.create_instance(ctx, recipe_id="recipe.test", max_tool_calls=1, max_budget=1000)
    kernel.start(wfi.id, ctx)
    kernel.mediate_tool("t", {}, ctx, wfi.id)
    kernel.mediate_tool("t", {}, ctx, wfi.id)  # exceeds the cap of 1
    checkpoints = kernel.list_checkpoints(wfi.id)
    assert checkpoints, "a checkpoint must be written when a cap refuses the instance"
    events = kernel.get_run_events(wfi.id)
    assert RunEventType.WORKFLOW_REFUSED in [e.event_type for e in events]


def test_refused_is_a_terminal_status(kernel):
    ctx = _ctx()
    wfi = kernel.create_instance(ctx, recipe_id="recipe.test", max_tool_calls=0, max_budget=1000)
    kernel.start(wfi.id, ctx)
    kernel.mediate_tool("t", {}, ctx, wfi.id)  # immediately exceeds the cap of 0
    refused = kernel.get_instance(wfi.id)
    assert refused.status == WorkflowStatus.REFUSED
    with pytest.raises(GrimoireRuntimeError, match="Invalid workflow transition"):
        kernel.start(wfi.id, ctx)


def test_mediate_tool_never_raises_after_refusal_and_stays_blocked(kernel):
    ctx = _ctx()
    wfi = kernel.create_instance(ctx, recipe_id="recipe.test", max_tool_calls=1, max_budget=1000)
    kernel.start(wfi.id, ctx)
    assert kernel.mediate_tool("t", {}, ctx, wfi.id) is True
    assert kernel.mediate_tool("t", {}, ctx, wfi.id) is False  # cap hit -> refused
    # Calling again on an already-refused instance must not raise nor silently allow.
    assert kernel.mediate_tool("t", {}, ctx, wfi.id) is False
    refused = kernel.get_instance(wfi.id)
    assert refused.status == WorkflowStatus.REFUSED


def test_mediate_tool_rejects_negative_cost(kernel):
    ctx = _ctx()
    wfi = kernel.create_instance(ctx, recipe_id="recipe.test", max_tool_calls=10, max_budget=10)
    kernel.start(wfi.id, ctx)
    # A negative cost would let a caller shrink budget_used and defeat the
    # cap (B11) instead of tripping it — must be refused outright.
    with pytest.raises(ValueError, match="non-negative"):
        kernel.mediate_tool("t", {}, ctx, wfi.id, cost=-5)
    unchanged = kernel.get_instance(wfi.id)
    assert unchanged.budget_used == 0
    assert unchanged.status == WorkflowStatus.RUNNING


def test_mediate_tool_does_not_emit_requested_when_cap_already_exceeded(kernel):
    ctx = _ctx()
    wfi = kernel.create_instance(ctx, recipe_id="recipe.test", max_tool_calls=1, max_budget=1000)
    kernel.start(wfi.id, ctx)
    assert kernel.mediate_tool("t", {}, ctx, wfi.id) is True
    assert kernel.mediate_tool("t", {}, ctx, wfi.id) is False  # over cap -> refused, never requested
    events = [e.event_type for e in kernel.get_run_events(wfi.id)]
    # Exactly one requested (the call that was actually mediated); the
    # refused call is only ever blocked, never logged as requested too.
    assert events.count(RunEventType.TOOL_REQUESTED) == 1
    assert events.count(RunEventType.TOOL_BLOCKED) == 1


def test_list_instances_filter_by_task(kernel):
    ctx = _ctx()
    wfi = kernel.create_instance(ctx, recipe_id="recipe.test")
    results = kernel.list_instances(task_id="GAO-test-001")
    assert any(w.id == wfi.id for w in results)
    results_other = kernel.list_instances(task_id="GAO-other-001")
    assert not results_other


# --- #204 : la frontière d'étape (advance_step / fail_step / STEP_*) --------


def test_advance_step_from_checkpointed_returns_to_running_and_emits_step_started(kernel):
    ctx = _ctx()
    wfi = kernel.create_instance(ctx, recipe_id="recipe.test")
    kernel.start(wfi.id, ctx)
    kernel.checkpoint(wfi.id, ctx, step_id="s1", completed_steps=["s1"], pending_steps=["s2"])
    wfi = kernel.advance_step(wfi.id, ctx, step_id="s2")
    assert wfi.status == WorkflowStatus.RUNNING
    events = kernel.get_run_events(wfi.id)
    step_started = [e for e in events if e.event_type is RunEventType.STEP_STARTED]
    assert len(step_started) == 1
    assert step_started[0].payload["step_id"] == "s2"
    # Un seul WORKFLOW_STARTED pour tout le run : advance_step n'en réémet pas
    # un second, contrairement au contournement (rappeler start()) débusqué
    # par le prototype de la première étape de #204.
    assert len([e for e in events if e.event_type is RunEventType.WORKFLOW_STARTED]) == 1


def test_advance_step_from_running_is_a_no_op_transition(kernel):
    """Le tout premier node d'un run : déjà RUNNING après start(), pas de transition à faire."""
    ctx = _ctx()
    wfi = kernel.create_instance(ctx, recipe_id="recipe.test")
    kernel.start(wfi.id, ctx)
    wfi = kernel.advance_step(wfi.id, ctx, step_id="s1")
    assert wfi.status == WorkflowStatus.RUNNING


def test_checkpoint_emits_step_completed_with_step_id(kernel):
    ctx = _ctx()
    wfi = kernel.create_instance(ctx, recipe_id="recipe.test")
    kernel.start(wfi.id, ctx)
    kernel.checkpoint(wfi.id, ctx, step_id="parse", completed_steps=["parse"], pending_steps=[])
    events = kernel.get_run_events(wfi.id)
    completed = [e for e in events if e.event_type is RunEventType.STEP_COMPLETED]
    assert len(completed) == 1
    assert completed[0].payload["step_id"] == "parse"


def test_fail_step_blocks_the_workflow_and_names_the_step(kernel):
    ctx = _ctx()
    wfi = kernel.create_instance(ctx, recipe_id="recipe.test")
    kernel.start(wfi.id, ctx)
    wfi = kernel.fail_step(wfi.id, ctx, step_id="parse", reason="pin=out contract mismatch")
    assert wfi.status == WorkflowStatus.BLOCKED
    assert wfi.abort_reason == "pin=out contract mismatch"
    events = kernel.get_run_events(wfi.id)
    failed = [e for e in events if e.event_type is RunEventType.STEP_FAILED]
    assert len(failed) == 1
    assert failed[0].payload["step_id"] == "parse"
    assert failed[0].payload["reason"] == "pin=out contract mismatch"


def test_advance_step_from_blocked_recovers_to_running(kernel):
    """Une retentative après correction de l'hôte repart légalement de BLOCKED."""
    ctx = _ctx()
    wfi = kernel.create_instance(ctx, recipe_id="recipe.test")
    kernel.start(wfi.id, ctx)
    kernel.fail_step(wfi.id, ctx, step_id="parse", reason="bad output")
    wfi = kernel.advance_step(wfi.id, ctx, step_id="parse")
    assert wfi.status == WorkflowStatus.RUNNING


# --- P0.4 : un contrat d'adapter unique --------------------------------------


def test_slugify_has_a_single_definition() -> None:
    """The identifier normalisation lives in one place, not three."""
    import ast
    from pathlib import Path as _Path

    runtime = _Path(__file__).resolve().parent.parent.parent / "src" / "grimoire" / "runtime"
    defs = [
        f"{module.name}:{node.name}"
        for module in runtime.rglob("*.py")
        for node in ast.parse(module.read_text(encoding="utf-8")).body
        if isinstance(node, ast.FunctionDef) and node.name in {"slugify", "_slugify"}
    ]
    assert defs == ["adapter_base.py:slugify"], defs


def test_every_adapter_satisfies_the_protocol() -> None:
    """The three adapters expose the same entry point and identify their source."""
    from grimoire.runtime.adapter_base import RecipeAdapter
    from grimoire.runtime.crewai_adapter import CrewAIAdapter
    from grimoire.runtime.gascity_converter import GasCityConverter
    from grimoire.runtime.langgraph_adapter import LangGraphAdapter

    adapters = [CrewAIAdapter(), LangGraphAdapter(), GasCityConverter()]
    assert [a.source_id for a in adapters] == ["crewai", "langgraph", "gascity"]
    for adapter in adapters:
        assert isinstance(adapter, RecipeAdapter)
        assert callable(adapter.to_recipe)


@pytest.mark.parametrize(
    ("adapter_path", "definition"),
    [
        ("crewai", {"name": "no-schema", "tasks": [{"id": "t1", "description": "d"}]}),
        ("langgraph", {"name": "no-schema", "nodes": [{"id": "n1", "name": "N"}], "edges": []}),
        ("gascity", {"name": "no-schema", "molecules": [{"id": "m1", "name": "M"}]}),
    ],
)
def test_import_without_output_schema_is_not_ok(adapter_path: str, definition: dict) -> None:
    """A definition that declares no output cannot be verified afterwards.

    CrewAI and LangGraph already refused it; Gas City carried ``output_schema``
    through without ever checking it, so an unverifiable formula reported ``ok``.
    """
    from grimoire.runtime.crewai_adapter import CrewAIAdapter
    from grimoire.runtime.gascity_converter import GasCityConverter
    from grimoire.runtime.langgraph_adapter import LangGraphAdapter

    adapter = {"crewai": CrewAIAdapter, "langgraph": LangGraphAdapter, "gascity": GasCityConverter}[adapter_path]()
    _recipe, report = adapter.to_recipe(definition)
    assert report.ok is False


def test_deprecated_entry_points_still_work() -> None:
    """The pre-``to_recipe`` names remain, per the SemVer policy of ADR-002."""
    from grimoire.runtime.crewai_adapter import CrewAIAdapter
    from grimoire.runtime.gascity_converter import GasCityConverter
    from grimoire.runtime.langgraph_adapter import LangGraphAdapter

    assert callable(CrewAIAdapter().import_flow)
    assert callable(LangGraphAdapter().import_graph)
    assert callable(GasCityConverter().convert)


def test_runtime_package_exports_the_recipe_surface() -> None:
    """An adapter author imports everything they need from ``grimoire.runtime``."""
    from grimoire import runtime

    for name in ("Recipe", "RecipeStep", "VerificationGate", "RecipeAdapter", "ImportReport", "slugify"):
        assert hasattr(runtime, name), name
