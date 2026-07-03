"""Tests for I2 (EvidenceProjection, VerdictProjection) and I3 (IncidentProjection) in projections.py."""

from __future__ import annotations

from grimoire.evidence.schemas import EvidenceItem, EvidenceKind, EvidenceProfile
from grimoire.evidence.service import EvidenceService
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.projections import (
    CockpitProjectionBuilder,
    EvidenceProjection,
    IncidentProjection,
    VerdictProjection,
    build_cockpit_from_paths,
)
from grimoire.missions.schemas import IncidentSeverity, MissionState, TaskState
from grimoire.runtime.kernel import RuntimeKernel


def _setup(tmp_path):
    ledger = MissionLedger(tmp_path / "ledger")
    kernel = RuntimeKernel(tmp_path / "kernel")
    evidence = EvidenceService(tmp_path / "evidence")
    return ledger, kernel, evidence


class TestIncidentProjection:
    def test_incidents_empty_without_incidents(self, tmp_path) -> None:
        ledger, kernel, evidence = _setup(tmp_path)
        mission = ledger.create_mission("M", origin="test")
        ledger.transition_mission(mission.id, MissionState.OPEN)
        cockpit = CockpitProjectionBuilder(ledger, kernel, evidence=evidence).build()
        assert cockpit.incidents == ()

    def test_incident_projection_populated(self, tmp_path) -> None:
        ledger, kernel, evidence = _setup(tmp_path)
        mission = ledger.create_mission("M", origin="test")
        ledger.transition_mission(mission.id, MissionState.OPEN)
        task = ledger.create_task(mission.id, "T", acceptance=("done",))
        ledger.open_incident(mission.id, task.id, "blocker", "something broke", severity=IncidentSeverity.HIGH)
        cockpit = CockpitProjectionBuilder(ledger, kernel, evidence=evidence).build()
        assert len(cockpit.incidents) == 1
        inc = cockpit.incidents[0]
        assert isinstance(inc, IncidentProjection)
        assert inc.mission_id == mission.id
        assert inc.task_id == task.id
        assert inc.kind == "blocker"
        assert inc.severity == "high"
        assert inc.status == "open"
        assert "something broke" in inc.summary

    def test_incident_projection_to_dict(self, tmp_path) -> None:
        ledger, kernel, evidence = _setup(tmp_path)
        mission = ledger.create_mission("M", origin="test")
        ledger.transition_mission(mission.id, MissionState.OPEN)
        task = ledger.create_task(mission.id, "T", acceptance=("done",))
        ledger.open_incident(mission.id, task.id, "blocker", "ctx", severity=IncidentSeverity.MEDIUM)
        cockpit = CockpitProjectionBuilder(ledger, kernel, evidence=evidence).build()
        d = cockpit.to_dict()
        assert "incidents" in d
        assert len(d["incidents"]) == 1
        assert d["incidents"][0]["severity"] == "medium"

    def test_total_incident_count_still_works(self, tmp_path) -> None:
        ledger, kernel, evidence = _setup(tmp_path)
        mission = ledger.create_mission("M", origin="test")
        ledger.transition_mission(mission.id, MissionState.OPEN)
        task = ledger.create_task(mission.id, "T", acceptance=("done",))
        ledger.open_incident(mission.id, task.id, "blocker", "ctx", severity=IncidentSeverity.LOW)
        ledger.open_incident(mission.id, task.id, "deploy-fail", "ctx2", severity=IncidentSeverity.HIGH)
        cockpit = CockpitProjectionBuilder(ledger, kernel, evidence=evidence).build()
        assert cockpit.total_incident_count == 2
        assert len(cockpit.incidents) == 2


class TestEvidenceProjection:
    def test_no_evidence_without_service(self, tmp_path) -> None:
        ledger = MissionLedger(tmp_path / "ledger")
        kernel = RuntimeKernel(tmp_path / "kernel")
        mission = ledger.create_mission("M", origin="test")
        ledger.transition_mission(mission.id, MissionState.OPEN)
        ledger.create_task(mission.id, "T", acceptance=("done",))
        cockpit = CockpitProjectionBuilder(ledger, kernel).build()
        assert cockpit.evidence_packs == ()
        assert cockpit.verdicts == ()

    def test_evidence_pack_projected(self, tmp_path) -> None:
        ledger, kernel, evidence = _setup(tmp_path)
        mission = ledger.create_mission("M", origin="test")
        ledger.transition_mission(mission.id, MissionState.OPEN)
        task = ledger.create_task(mission.id, "T", acceptance=("done",))
        ledger.transition_task(task.id, TaskState.READY)
        ledger.transition_task(task.id, TaskState.CLAIMED)
        ledger.transition_task(task.id, TaskState.RUNNING)

        pack = evidence.create_pack(
            task_id=task.id,
            profile=EvidenceProfile.STANDARD,
            items=[EvidenceItem.from_text("ev-001", EvidenceKind.TEST, "tests pass", uri="ev://test")],
        )

        cockpit = CockpitProjectionBuilder(ledger, kernel, evidence=evidence).build()
        assert len(cockpit.evidence_packs) == 1
        ep = cockpit.evidence_packs[0]
        assert isinstance(ep, EvidenceProjection)
        assert ep.pack_id == pack.id
        assert ep.task_id == task.id
        assert ep.item_count == 1
        assert ep.profile == "standard"

    def test_task_evidence_pack_id_populated(self, tmp_path) -> None:
        ledger, kernel, evidence = _setup(tmp_path)
        mission = ledger.create_mission("M", origin="test")
        ledger.transition_mission(mission.id, MissionState.OPEN)
        task = ledger.create_task(mission.id, "T", acceptance=("done",))
        pack = evidence.create_pack(
            task_id=task.id,
            profile=EvidenceProfile.STANDARD,
            items=[EvidenceItem.from_text("ev-001", EvidenceKind.TEST, "ok", uri="ev://test")],
        )
        cockpit = CockpitProjectionBuilder(ledger, kernel, evidence=evidence).build()
        tp = cockpit.missions[0].tasks[0]
        assert tp.evidence_pack_id == pack.id

    def test_verdict_projected(self, tmp_path) -> None:
        ledger, kernel, evidence = _setup(tmp_path)
        mission = ledger.create_mission("M", origin="test")
        ledger.transition_mission(mission.id, MissionState.OPEN)
        task = ledger.create_task(mission.id, "T", acceptance=("done",))
        pack = evidence.create_pack(
            task_id=task.id,
            profile=EvidenceProfile.LIGHT,
            items=[EvidenceItem.from_text("ev-001", EvidenceKind.TEST, "ok", uri="ev://test")],
        )
        evidence.verify(pack)
        cockpit = CockpitProjectionBuilder(ledger, kernel, evidence=evidence).build()
        assert len(cockpit.verdicts) == 1
        vp = cockpit.verdicts[0]
        assert isinstance(vp, VerdictProjection)
        assert vp.verdict == "passed"
        assert vp.decision_close is True
        assert vp.evidence_pack_id == pack.id

    def test_evidence_projection_to_dict(self, tmp_path) -> None:
        ledger, kernel, evidence = _setup(tmp_path)
        cockpit = CockpitProjectionBuilder(ledger, kernel, evidence=evidence).build()
        d = cockpit.to_dict()
        assert "evidence_packs" in d
        assert "verdicts" in d
        assert isinstance(d["evidence_packs"], list)

    def test_build_cockpit_from_paths_with_evidence(self, tmp_path) -> None:
        cockpit = build_cockpit_from_paths(
            tmp_path / "ledger", tmp_path / "kernel", evidence_root=tmp_path / "evidence"
        )
        assert cockpit.evidence_packs == ()
        assert cockpit.verdicts == ()
        assert cockpit.incidents == ()
