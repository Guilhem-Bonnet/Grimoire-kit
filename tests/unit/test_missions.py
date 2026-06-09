"""Tests for the Mission Ledger module."""

from __future__ import annotations

import pytest

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import (
    MissionState,
    TaskState,
)


@pytest.fixture
def ledger(tmp_path):
    return MissionLedger(tmp_path / "ledger")


# ── Mission lifecycle ─────────────────────────────────────────────────────────

def test_create_mission(ledger):
    m = ledger.create_mission("Pack Registry", origin="user")
    assert m.id.startswith("MIS-pack-registry")
    assert m.status == MissionState.DRAFT
    assert m.title == "Pack Registry"


def test_create_mission_custom_id(ledger):
    m = ledger.create_mission("Test", origin="user", mission_id="MIS-test-001")
    assert m.id == "MIS-test-001"


def test_mission_persisted_on_disk(ledger, tmp_path):
    ledger.create_mission("Persist Test", origin="user")
    # Load fresh ledger from same root
    ledger2 = MissionLedger(tmp_path / "ledger")
    missions = ledger2.list_missions()
    assert len(missions) == 1
    assert missions[0].title == "Persist Test"


def test_mission_transition_draft_to_open(ledger):
    m = ledger.create_mission("Flow", origin="user")
    m = ledger.transition_mission(m.id, MissionState.OPEN)
    assert m.status == MissionState.OPEN


def test_mission_invalid_transition_raises(ledger):
    m = ledger.create_mission("Flow", origin="user")
    with pytest.raises(GrimoireMissionError, match="Invalid mission transition"):
        ledger.transition_mission(m.id, MissionState.CLOSED)


def test_mission_not_found_raises(ledger):
    with pytest.raises(GrimoireMissionError, match="Mission not found"):
        ledger.transition_mission("MIS-doesnt-exist-001", MissionState.OPEN)


def test_full_mission_lifecycle(ledger):
    m = ledger.create_mission("Full Flow", origin="user")
    m = ledger.transition_mission(m.id, MissionState.OPEN)
    m = ledger.transition_mission(m.id, MissionState.VERIFYING)
    m = ledger.transition_mission(m.id, MissionState.CLOSED)
    assert m.status == MissionState.CLOSED


# ── Task lifecycle ─────────────────────────────────────────────────────────────

def test_create_task_requires_acceptance(ledger):
    m = ledger.create_mission("M", origin="user")
    with pytest.raises(GrimoireMissionError, match="acceptance criterion"):
        ledger.create_task(m.id, "No acceptance", acceptance=())


def test_create_task_requires_valid_mission(ledger):
    with pytest.raises(GrimoireMissionError, match="Mission not found"):
        ledger.create_task("MIS-ghost-001", "Task", acceptance=("done",))


def test_create_task(ledger):
    m = ledger.create_mission("M", origin="user")
    t = ledger.create_task(m.id, "Implement Ledger", acceptance=("tests pass",))
    assert t.id.startswith("GAO-implement")
    assert t.status == TaskState.PROPOSED
    assert t.acceptance == ("tests pass",)


def test_task_state_machine_happy_path(ledger):
    m = ledger.create_mission("M", origin="user")
    t = ledger.create_task(m.id, "Task", acceptance=("done",))
    t = ledger.transition_task(t.id, TaskState.READY)
    assert t.status == TaskState.READY
    t = ledger.claim_task(t.id, actor_id="agent", host_id="host-cli")
    assert t.status == TaskState.CLAIMED
    assert t.claim is not None
    assert t.claim.actor_id == "agent"
    t = ledger.transition_task(t.id, TaskState.RUNNING)
    t = ledger.transition_task(t.id, TaskState.NEEDS_VERIFICATION)
    t = ledger.transition_task(t.id, TaskState.CLOSED)
    assert t.status == TaskState.CLOSED


def test_task_invalid_transition_raises(ledger):
    m = ledger.create_mission("M", origin="user")
    t = ledger.create_task(m.id, "Task", acceptance=("done",))
    with pytest.raises(GrimoireMissionError, match="Invalid task transition"):
        ledger.transition_task(t.id, TaskState.CLOSED)


def test_task_cancel_from_ready(ledger):
    m = ledger.create_mission("M", origin="user")
    t = ledger.create_task(m.id, "Task", acceptance=("done",))
    t = ledger.transition_task(t.id, TaskState.READY)
    t = ledger.transition_task(t.id, TaskState.CANCELLED)
    assert t.status == TaskState.CANCELLED


# ── Queries ─────────────────────────────────────────────────────────────────────

def test_ready_tasks_query(ledger):
    m = ledger.create_mission("M", origin="user")
    t1 = ledger.create_task(m.id, "T1", acceptance=("done",))
    t2 = ledger.create_task(m.id, "T2", acceptance=("done",))
    ledger.transition_task(t1.id, TaskState.READY)
    ledger.transition_task(t2.id, TaskState.READY)
    ready = ledger.ready_tasks(m.id)
    assert len(ready) == 2


def test_claimed_tasks_query(ledger):
    m = ledger.create_mission("M", origin="user")
    t = ledger.create_task(m.id, "T", acceptance=("done",))
    ledger.transition_task(t.id, TaskState.READY)
    ledger.claim_task(t.id, actor_id="agent", host_id="h")
    assert len(ledger.claimed_tasks(m.id)) == 1


def test_list_events_and_incidents(ledger):
    m = ledger.create_mission("M", origin="user")
    t = ledger.create_task(m.id, "T", acceptance=("done",))
    inc = ledger.open_incident(m.id, t.id, "policy_block", "Missing evidence")

    assert len(ledger.list_events()) >= 2
    assert ledger.list_events(t.id)
    assert ledger.list_incidents() == [inc]
    assert ledger.list_incidents(t.id) == [inc]


def test_needs_verification_query(ledger):
    m = ledger.create_mission("M", origin="user")
    t = ledger.create_task(m.id, "T", acceptance=("done",))
    ledger.transition_task(t.id, TaskState.READY)
    ledger.claim_task(t.id, actor_id="agent", host_id="h")
    ledger.transition_task(t.id, TaskState.RUNNING)
    ledger.transition_task(t.id, TaskState.NEEDS_VERIFICATION)
    assert len(ledger.needs_verification_tasks(m.id)) == 1


# ── Incidents ─────────────────────────────────────────────────────────────────

def test_open_incident(ledger):
    m = ledger.create_mission("M", origin="user")
    t = ledger.create_task(m.id, "T", acceptance=("done",))
    inc = ledger.open_incident(m.id, t.id, "policy_block", "Lock file missing")
    assert inc.id.startswith("inc-")
    assert ledger.open_incidents(t.id) == [inc]


def test_resolve_incident(ledger):
    m = ledger.create_mission("M", origin="user")
    t = ledger.create_task(m.id, "T", acceptance=("done",))
    inc = ledger.open_incident(m.id, t.id, "policy_block", "Missing")
    ledger.resolve_incident(inc.id)
    assert ledger.open_incidents(t.id) == []


# ── Import/Export ─────────────────────────────────────────────────────────────

def test_export_import_roundtrip(ledger, tmp_path):
    m = ledger.create_mission("M", origin="user")
    ledger.create_task(m.id, "T", acceptance=("done",))
    export_path = tmp_path / "export.jsonl"
    count = ledger.export_jsonl(export_path)
    assert count >= 2

    ledger2 = MissionLedger(tmp_path / "ledger2")
    imported = ledger2.import_jsonl(export_path)
    assert imported == count
    assert ledger2.get_mission(m.id) is not None
