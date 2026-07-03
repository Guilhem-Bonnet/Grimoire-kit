"""Tests for missions/projections.py — CockpitProjectionBuilder."""

from __future__ import annotations

from grimoire.missions.ledger import MissionLedger
from grimoire.missions.projections import CockpitProjectionBuilder, build_cockpit_from_paths
from grimoire.missions.schemas import MissionState, TaskState
from grimoire.runtime.kernel import RuntimeKernel
from grimoire.runtime.schemas import ExecutionContext


def _ctx(mission_id: str = "MIS-test-001", task_id: str = "GAO-test-001") -> ExecutionContext:
    return ExecutionContext(
        run_id="RUN-test-001",
        mission_id=mission_id,
        task_id=task_id,
        workflow_instance_id="",
        actor_id="agent-test",
        host_id="claude-code-cli",
        risk_profile="standard",
    )


class TestCockpitProjectionBuilder:
    def test_empty_ledger(self, tmp_path) -> None:
        ledger = MissionLedger(tmp_path / "ledger")
        kernel = RuntimeKernel(tmp_path / "kernel")
        builder = CockpitProjectionBuilder(ledger, kernel)
        cockpit = builder.build()
        assert cockpit.missions == ()
        assert cockpit.active_workflows == ()
        assert cockpit.verification_queue == ()

    def test_single_mission_projection(self, tmp_path) -> None:
        ledger = MissionLedger(tmp_path / "ledger")
        kernel = RuntimeKernel(tmp_path / "kernel")
        mission = ledger.create_mission("Test mission", origin="test")
        ledger.transition_mission(mission.id, MissionState.OPEN)
        builder = CockpitProjectionBuilder(ledger, kernel)
        cockpit = builder.build()
        assert len(cockpit.missions) == 1
        assert cockpit.missions[0].mission_id == mission.id
        assert cockpit.missions[0].status == "open"

    def test_task_counts(self, tmp_path) -> None:
        ledger = MissionLedger(tmp_path / "ledger")
        kernel = RuntimeKernel(tmp_path / "kernel")
        mission = ledger.create_mission("M1", origin="test")
        ledger.transition_mission(mission.id, MissionState.OPEN)
        t1 = ledger.create_task(mission.id, "Task 1", acceptance=("done",))
        t2 = ledger.create_task(mission.id, "Task 2", acceptance=("done",))
        ledger.transition_task(t1.id, TaskState.READY)
        ledger.transition_task(t2.id, TaskState.READY)
        ledger.transition_task(t2.id, TaskState.CLAIMED)
        cockpit = CockpitProjectionBuilder(ledger, kernel).build()
        mp = cockpit.missions[0]
        assert mp.task_count == 2
        assert mp.ready_count == 1

    def test_verification_queue_populated(self, tmp_path) -> None:
        ledger = MissionLedger(tmp_path / "ledger")
        kernel = RuntimeKernel(tmp_path / "kernel")
        mission = ledger.create_mission("M1", origin="test")
        ledger.transition_mission(mission.id, MissionState.OPEN)
        task = ledger.create_task(mission.id, "Task NV", acceptance=("done",))
        ledger.transition_task(task.id, TaskState.READY)
        ledger.transition_task(task.id, TaskState.CLAIMED)
        ledger.transition_task(task.id, TaskState.RUNNING)
        ledger.transition_task(task.id, TaskState.NEEDS_VERIFICATION)
        cockpit = CockpitProjectionBuilder(ledger, kernel).build()
        assert task.id in cockpit.verification_queue

    def test_incident_count(self, tmp_path) -> None:
        ledger = MissionLedger(tmp_path / "ledger")
        kernel = RuntimeKernel(tmp_path / "kernel")
        mission = ledger.create_mission("M1", origin="test")
        ledger.transition_mission(mission.id, MissionState.OPEN)
        task = ledger.create_task(mission.id, "Task Inc", acceptance=("done",))
        from grimoire.missions.schemas import IncidentSeverity
        ledger.open_incident(mission.id, task.id, "blocker", "some context", severity=IncidentSeverity.MEDIUM)
        cockpit = CockpitProjectionBuilder(ledger, kernel).build()
        assert cockpit.total_incident_count == 1

    def test_to_dict(self, tmp_path) -> None:
        ledger = MissionLedger(tmp_path / "ledger")
        kernel = RuntimeKernel(tmp_path / "kernel")
        cockpit = CockpitProjectionBuilder(ledger, kernel).build()
        d = cockpit.to_dict()
        assert "missions" in d
        assert "generated_at" in d
        assert "verification_queue" in d

    def test_filter_by_mission_id(self, tmp_path) -> None:
        ledger = MissionLedger(tmp_path / "ledger")
        kernel = RuntimeKernel(tmp_path / "kernel")
        m1 = ledger.create_mission("M1", origin="test")
        ledger.create_mission("M2", origin="test")
        builder = CockpitProjectionBuilder(ledger, kernel)
        cockpit = builder.build(mission_id=m1.id)
        assert len(cockpit.missions) == 1
        assert cockpit.missions[0].mission_id == m1.id

    def test_build_cockpit_from_paths(self, tmp_path) -> None:
        cockpit = build_cockpit_from_paths(tmp_path / "ledger", tmp_path / "kernel")
        assert cockpit.missions == ()
