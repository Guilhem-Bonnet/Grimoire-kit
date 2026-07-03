"""L2 — SDK public API surface tests.

Validates that the top-level `grimoire` package exports are complete,
importable, and produce working objects from the public interface only.
"""

from __future__ import annotations

from pathlib import Path

import grimoire
from grimoire import (
    EvidenceItem,
    EvidenceKind,
    EvidenceProfile,
    EvidenceService,
    MissionLedger,
    MissionState,
    PolicyEngine,
    RuntimeKernel,
    TaskState,
    TraceLedger,
    TraceOutcome,
    __version__,
)

# ── Smoke: all symbols importable ─────────────────────────────────────────────

class TestSDKExports:
    def test_version_string(self) -> None:
        assert isinstance(__version__, str)
        parts = __version__.split(".")
        assert len(parts) >= 2

    def test_all_symbols_in_all(self) -> None:
        expected = {
            "GrimoireConfig", "GrimoireError", "GrimoireProject", "__version__",
            "MissionLedger", "MissionState", "MissionTask", "TaskState",
            "EvidenceItem", "EvidenceKind", "EvidenceProfile", "EvidenceService",
            "PolicyEngine", "RuntimeKernel",
            "TraceOutcome", "TraceLedger",
            "PackManifest", "MemoryManager",
        }
        assert expected.issubset(set(grimoire.__all__))

    def test_all_symbols_are_classes_or_enums(self) -> None:
        for name in grimoire.__all__:
            if name == "__version__":
                continue
            obj = getattr(grimoire, name)
            assert callable(obj) or hasattr(obj, "__mro__"), f"{name} is not a class"


# ── MissionLedger public API ───────────────────────────────────────────────────

class TestMissionLedgerPublicAPI:
    def test_create_mission_via_sdk_import(self, tmp_path: Path) -> None:
        ledger = MissionLedger(tmp_path / "ledger")
        mission = ledger.create_mission("Test mission", origin="sdk-test")
        ledger.transition_mission(mission.id, MissionState.OPEN, actor_id="sdk")

        assert mission.id.startswith("MIS-")
        assert ledger.get_mission(mission.id) is not None

    def test_task_state_enum_accessible(self) -> None:
        assert TaskState.PROPOSED.value == "proposed"
        assert TaskState.NEEDS_VERIFICATION.value == "needs_verification"
        assert TaskState.CLOSED.value == "closed"

    def test_mission_state_enum_accessible(self) -> None:
        assert MissionState.OPEN.value == "open"
        assert MissionState.CLOSED.value == "closed"


# ── EvidenceService public API ─────────────────────────────────────────────────

class TestEvidenceServicePublicAPI:
    def test_create_pack_and_verify(self, tmp_path: Path) -> None:
        svc = EvidenceService(tmp_path / "evidence")
        item = EvidenceItem.from_text("item-1", EvidenceKind.TEST, "all tests pass", summary="tests")
        pack = svc.create_pack("GAO-task-sdk", EvidenceProfile.LIGHT, [item])

        assert pack.task_id == "GAO-task-sdk"
        result = svc.verify(pack)
        assert result.verdict.value == "passed"

    def test_evidence_kind_accessible(self) -> None:
        assert EvidenceKind.TEST.value == "test"
        assert EvidenceKind.REPORT.value == "report"

    def test_evidence_profile_accessible(self) -> None:
        assert EvidenceProfile.LIGHT.value == "light"
        assert EvidenceProfile.STANDARD.value == "standard"


# ── TraceLedger public API ─────────────────────────────────────────────────────

class TestTraceLedgerPublicAPI:
    def test_record_and_list(self, tmp_path: Path) -> None:
        from datetime import UTC, datetime
        trace = TraceLedger(tmp_path / "traces")
        trace.record(
            run_id="sdk-run-001",
            workflow_instance_id="wf-001",
            mission_id="MIS-001",
            task_id="GAO-task-001",
            recipe_id="test-recipe",
            outcome=TraceOutcome.SUCCESS,
            started_at=datetime.now(tz=UTC).isoformat(),
        )
        records = trace.list_traces()
        assert len(records) == 1
        assert records[0].run_id == "sdk-run-001"

    def test_trace_outcome_accessible(self) -> None:
        assert TraceOutcome.SUCCESS.value == "success"
        assert TraceOutcome.PARTIAL.value == "partial"


# ── RuntimeKernel public API ──────────────────────────────────────────────────

class TestRuntimeKernelPublicAPI:
    def test_instantiable(self, tmp_path: Path) -> None:
        kernel = RuntimeKernel(tmp_path / "kernel")
        assert kernel is not None


# ── PolicyEngine public API ────────────────────────────────────────────────────

class TestPolicyEnginePublicAPI:
    def test_instantiable(self, tmp_path: Path) -> None:
        engine = PolicyEngine()
        assert engine is not None

    def test_has_evaluate_method(self) -> None:
        assert hasattr(PolicyEngine, "evaluate")


# ── Example project — gate for L2 ────────────────────────────────────────────

class TestExampleProject:
    """Gate: example project compiles and produces expected results."""

    def test_end_to_end_workflow(self, tmp_path: Path) -> None:
        """Minimal agentic loop: create mission → run task → close with evidence."""
        # Setup
        ledger = MissionLedger(tmp_path / "ledger")
        evidence = EvidenceService(tmp_path / "evidence")

        # Create mission + task
        mission = ledger.create_mission("SDK example", origin="sdk-test")
        ledger.transition_mission(mission.id, MissionState.OPEN, actor_id="sdk")

        task = ledger.create_task(
            mission.id,
            "Implement feature X",
            acceptance=("feature X implemented and tested",),
        )

        # Progress task to completion
        ledger.transition_task(task.id, TaskState.READY, actor_id="sdk")
        ledger.claim_task(task.id, actor_id="sdk", host_id="test-host")
        ledger.transition_task(task.id, TaskState.RUNNING, actor_id="sdk")
        ledger.transition_task(task.id, TaskState.NEEDS_VERIFICATION, actor_id="sdk")

        # Gather evidence (STANDARD requires TEST + LOG)
        item_test = EvidenceItem.from_text(
            "ev-1", EvidenceKind.TEST, "pytest: 42 passed", summary="all tests pass"
        )
        item_log = EvidenceItem.from_text(
            "ev-2", EvidenceKind.LOG, "no errors in run log", summary="clean run log"
        )
        pack = evidence.create_pack(task.id, EvidenceProfile.STANDARD, [item_test, item_log])
        result = evidence.verify(pack)
        assert result.verdict == result.verdict.PASSED

        # Close task
        ledger.transition_task(task.id, TaskState.CLOSED, actor_id="sdk")
        ledger.transition_mission(mission.id, MissionState.CLOSED, actor_id="sdk")

        # Verify final state
        final_task = ledger.get_task(task.id)
        assert final_task is not None
        assert final_task.status == TaskState.CLOSED
        final_mission = ledger.get_mission(mission.id)
        assert final_mission is not None
        assert final_mission.status == MissionState.CLOSED
