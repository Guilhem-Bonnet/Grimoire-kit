"""Tests for J3 — A2A protocol adapter."""

from __future__ import annotations

from pathlib import Path

from grimoire.bridges.a2a_adapter import (
    A2AAdapter,
    A2AArtifact,
    A2AImportReport,
    A2AMessage,
    A2AMessagePart,
    A2ATask,
    A2ATaskState,
)
from grimoire.evidence.service import EvidenceService
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import MissionState, TaskState
from grimoire.traces.ledger import TraceLedger

# ── Helpers ────────────────────────────────────────────────────────────────────

def _ledger(tmp_path: Path) -> MissionLedger:
    return MissionLedger(tmp_path / "ledger")


def _evidence(tmp_path: Path) -> EvidenceService:
    return EvidenceService(tmp_path / "evidence")


def _trace(tmp_path: Path) -> TraceLedger:
    return TraceLedger(tmp_path / "traces")


def _a2a_task(
    task_id: str = "task-001",
    state: str = A2ATaskState.SUBMITTED,
    session_id: str = "sess-42",
    input_text: str = "Do something",
    artifacts: list[dict] | None = None,
) -> A2ATask:
    input_msg = A2AMessage(
        role="user",
        parts=(A2AMessagePart(type="text", text=input_text),),
    )
    artifact_objs: tuple[A2AArtifact, ...] = ()
    if artifacts:
        artifact_objs = tuple(A2AArtifact.from_dict(a) for a in artifacts)
    return A2ATask(
        id=task_id,
        status_state=state,
        session_id=session_id,
        input_message=input_msg,
        artifacts=artifact_objs,
    )


# ── Schema unit tests ──────────────────────────────────────────────────────────

class TestA2AMessagePart:
    def test_roundtrip_text(self) -> None:
        part = A2AMessagePart(type="text", text="hello", mime_type="text/plain")
        d = part.to_dict()
        assert d["type"] == "text"
        assert d["text"] == "hello"
        assert d["mimeType"] == "text/plain"
        restored = A2AMessagePart.from_dict(d)
        assert restored == part

    def test_roundtrip_data(self) -> None:
        part = A2AMessagePart(type="data", data={"key": "value"})
        d = part.to_dict()
        assert d["data"] == {"key": "value"}
        assert "text" not in d
        restored = A2AMessagePart.from_dict(d)
        assert restored.data == {"key": "value"}

    def test_empty_fields_omitted(self) -> None:
        part = A2AMessagePart(type="text")
        d = part.to_dict()
        assert "mimeType" not in d
        assert "data" not in d


class TestA2AMessage:
    def test_text_property(self) -> None:
        msg = A2AMessage(
            role="agent",
            parts=(
                A2AMessagePart(type="text", text="hello "),
                A2AMessagePart(type="data", data={"x": 1}),
                A2AMessagePart(type="text", text="world"),
            ),
        )
        assert msg.text == "hello  world"

    def test_roundtrip(self) -> None:
        msg = A2AMessage(
            role="user",
            parts=(A2AMessagePart(type="text", text="test"),),
        )
        restored = A2AMessage.from_dict(msg.to_dict())
        assert restored.role == "user"
        assert restored.text == "test"


class TestA2AArtifact:
    def test_text_content(self) -> None:
        artifact = A2AArtifact(
            index=0,
            parts=(
                A2AMessagePart(type="text", text="result output"),
                A2AMessagePart(type="data", data={"raw": True}),
            ),
            name="output",
        )
        assert "result output" in artifact.text_content()

    def test_roundtrip(self) -> None:
        raw = {
            "index": 1,
            "parts": [{"type": "text", "text": "content"}],
            "name": "report",
            "description": "final report",
        }
        artifact = A2AArtifact.from_dict(raw)
        d = artifact.to_dict()
        assert d["index"] == 1
        assert d["name"] == "report"
        assert d["description"] == "final report"


class TestA2ATask:
    def test_is_terminal_completed(self) -> None:
        task = _a2a_task(state=A2ATaskState.COMPLETED)
        assert task.is_terminal is True

    def test_is_terminal_failed(self) -> None:
        task = _a2a_task(state=A2ATaskState.FAILED)
        assert task.is_terminal is True

    def test_is_terminal_canceled(self) -> None:
        task = _a2a_task(state=A2ATaskState.CANCELED)
        assert task.is_terminal is True

    def test_not_terminal_working(self) -> None:
        task = _a2a_task(state=A2ATaskState.WORKING)
        assert task.is_terminal is False

    def test_not_terminal_submitted(self) -> None:
        task = _a2a_task(state=A2ATaskState.SUBMITTED)
        assert task.is_terminal is False

    def test_roundtrip(self) -> None:
        task = _a2a_task()
        d = task.to_dict()
        restored = A2ATask.from_dict(d)
        assert restored.id == task.id
        assert restored.status_state == task.status_state
        assert restored.session_id == task.session_id

    def test_from_dict_no_input(self) -> None:
        d = {"id": "t1", "status": {"state": "submitted"}}
        task = A2ATask.from_dict(d)
        assert task.input_message is None


# ── Adapter tests ──────────────────────────────────────────────────────────────

class TestA2AAdapterImport:
    def test_creates_mission_and_task(self, tmp_path: Path) -> None:
        ledger = _ledger(tmp_path)
        adapter = A2AAdapter(ledger)
        task = _a2a_task("t-001", A2ATaskState.SUBMITTED)

        report = adapter.import_task(task, mission_id="MIS-NEW")

        assert isinstance(report, A2AImportReport)
        assert report.a2a_task_id == "t-001"
        assert report.grimoire_task_id.startswith("GAO-a2a-")
        assert report.errors == []

    def test_stable_task_id_format(self, tmp_path: Path) -> None:
        ledger = _ledger(tmp_path)
        adapter = A2AAdapter(ledger)
        task = _a2a_task("my-task/id:123")

        report = adapter.import_task(task, mission_id="MIS-001")

        assert report.grimoire_task_id == "GAO-a2a-my-task-id-123"

    def test_uses_provided_mission_id_when_exists(self, tmp_path: Path) -> None:
        ledger = _ledger(tmp_path)
        mission = ledger.create_mission("Existing mission", origin="test")
        ledger.transition_mission(mission.id, MissionState.OPEN, actor_id="test")
        adapter = A2AAdapter(ledger)

        report = adapter.import_task(_a2a_task(), mission_id=mission.id)

        assert report.mission_id == mission.id
        tasks = ledger.list_tasks(mission.id)
        assert len(tasks) == 1

    def test_creates_new_mission_when_id_not_found(self, tmp_path: Path) -> None:
        ledger = _ledger(tmp_path)
        adapter = A2AAdapter(ledger)

        report = adapter.import_task(_a2a_task(), mission_id="MIS-NONEXISTENT")

        created_mission = ledger.get_mission(report.mission_id)
        assert created_mission is not None

    def test_completed_maps_to_needs_verification(self, tmp_path: Path) -> None:
        """Guardrail: external task completion must not auto-close — lands in NEEDS_VERIFICATION."""
        ledger = _ledger(tmp_path)
        adapter = A2AAdapter(ledger)
        task = _a2a_task("t-closed", A2ATaskState.COMPLETED)

        report = adapter.import_task(task, mission_id="MIS-001")

        assert report.state_mapped == TaskState.NEEDS_VERIFICATION.value
        grimoire_task = ledger.get_task(report.grimoire_task_id)
        assert grimoire_task is not None
        assert grimoire_task.status == TaskState.NEEDS_VERIFICATION

    def test_submitted_maps_to_proposed(self, tmp_path: Path) -> None:
        ledger = _ledger(tmp_path)
        adapter = A2AAdapter(ledger)
        report = adapter.import_task(_a2a_task(state=A2ATaskState.SUBMITTED), mission_id="MIS-001")
        assert report.state_mapped == TaskState.PROPOSED.value

    def test_working_maps_to_running(self, tmp_path: Path) -> None:
        ledger = _ledger(tmp_path)
        adapter = A2AAdapter(ledger)
        report = adapter.import_task(_a2a_task(state=A2ATaskState.WORKING), mission_id="MIS-001")
        assert report.state_mapped == TaskState.RUNNING.value

    def test_input_required_maps_to_blocked(self, tmp_path: Path) -> None:
        ledger = _ledger(tmp_path)
        adapter = A2AAdapter(ledger)
        report = adapter.import_task(_a2a_task(state=A2ATaskState.INPUT_REQUIRED), mission_id="MIS-001")
        assert report.state_mapped == TaskState.BLOCKED.value

    def test_failed_maps_to_failed(self, tmp_path: Path) -> None:
        ledger = _ledger(tmp_path)
        adapter = A2AAdapter(ledger)
        report = adapter.import_task(_a2a_task(state=A2ATaskState.FAILED), mission_id="MIS-001")
        assert report.state_mapped == TaskState.FAILED.value

    def test_records_input_message(self, tmp_path: Path) -> None:
        ledger = _ledger(tmp_path)
        adapter = A2AAdapter(ledger)
        task = _a2a_task("t-msg", input_text="Do the thing")

        report = adapter.import_task(task, mission_id="MIS-001")

        assert report.messages_recorded == 1

    def test_title_from_input_message(self, tmp_path: Path) -> None:
        ledger = _ledger(tmp_path)
        adapter = A2AAdapter(ledger)
        task = _a2a_task("t-title", input_text="Compute the answer to life")

        report = adapter.import_task(task, mission_id="MIS-001")

        grimoire_task = ledger.get_task(report.grimoire_task_id)
        assert grimoire_task is not None
        assert "Compute" in grimoire_task.title

    def test_idempotent_second_import(self, tmp_path: Path) -> None:
        ledger = _ledger(tmp_path)
        mission = ledger.create_mission("M", origin="test")
        ledger.transition_mission(mission.id, MissionState.OPEN, actor_id="test")
        adapter = A2AAdapter(ledger)
        task = _a2a_task("t-idem")

        report1 = adapter.import_task(task, mission_id=mission.id)
        report2 = adapter.import_task(task, mission_id=mission.id)

        assert report1.grimoire_task_id == report2.grimoire_task_id
        tasks = ledger.list_tasks(mission.id)
        assert len(tasks) == 1

    def test_imports_artifacts_with_evidence(self, tmp_path: Path) -> None:
        ledger = _ledger(tmp_path)
        svc = _evidence(tmp_path)
        adapter = A2AAdapter(ledger, evidence=svc)
        artifacts = [
            {"index": 0, "parts": [{"type": "text", "text": "result text"}], "name": "output"},
        ]
        task = _a2a_task("t-art", artifacts=artifacts)

        report = adapter.import_task(task, mission_id="MIS-001")

        assert report.artifacts_imported == 1
        assert report.errors == []

    def test_no_artifacts_without_evidence_service(self, tmp_path: Path) -> None:
        ledger = _ledger(tmp_path)
        adapter = A2AAdapter(ledger)  # no evidence service
        artifacts = [{"index": 0, "parts": [{"type": "text", "text": "data"}]}]
        task = _a2a_task("t-noart", artifacts=artifacts)

        report = adapter.import_task(task, mission_id="MIS-001")

        assert report.artifacts_imported == 0

    def test_emits_trace_with_trace_ledger(self, tmp_path: Path) -> None:
        ledger = _ledger(tmp_path)
        trace = _trace(tmp_path)
        adapter = A2AAdapter(ledger, trace_ledger=trace)
        task = _a2a_task("t-trace")

        report = adapter.import_task(task, mission_id="MIS-001")

        assert report.trace_id.startswith("a2a-")
        assert report.errors == []

    def test_report_to_dict(self, tmp_path: Path) -> None:
        ledger = _ledger(tmp_path)
        adapter = A2AAdapter(ledger)
        report = adapter.import_task(_a2a_task(), mission_id="MIS-001")

        d = report.to_dict()
        assert "a2a_task_id" in d
        assert "grimoire_task_id" in d
        assert "state_mapped" in d
        assert "errors" in d


class TestA2AAdapterExport:
    def test_export_task_status(self, tmp_path: Path) -> None:
        ledger = _ledger(tmp_path)
        adapter = A2AAdapter(ledger)
        task = _a2a_task("t-exp")
        report = adapter.import_task(task, mission_id="MIS-001")

        grimoire_task = ledger.get_task(report.grimoire_task_id)
        assert grimoire_task is not None

        status = adapter.export_task_status(grimoire_task)

        assert "id" in status
        assert "status" in status
        assert status["status"]["state"] in (
            A2ATaskState.SUBMITTED, A2ATaskState.WORKING, A2ATaskState.COMPLETED,
            A2ATaskState.INPUT_REQUIRED, A2ATaskState.FAILED, A2ATaskState.CANCELED,
        )
        assert "grimoire_mission_id" in status["metadata"]
        assert "grimoire_status" in status["metadata"]

    def test_export_completed_maps_correctly(self, tmp_path: Path) -> None:
        ledger = _ledger(tmp_path)
        adapter = A2AAdapter(ledger)
        task = _a2a_task("t-done", A2ATaskState.COMPLETED)
        report = adapter.import_task(task, mission_id="MIS-001")

        grimoire_task = ledger.get_task(report.grimoire_task_id)
        assert grimoire_task is not None

        status = adapter.export_task_status(grimoire_task)
        assert status["status"]["state"] == A2ATaskState.COMPLETED


class TestA2ANormalizeTrace:
    def test_strips_non_safe_fields(self) -> None:
        raw = {
            "id": "t1",
            "sessionId": "s1",
            "status": {"state": "completed"},
            "metadata": {},
            "secret_key": "SHOULD_BE_STRIPPED",
            "user_data": "pii here",
        }
        normalized = A2AAdapter.normalize_trace(raw)
        assert "secret_key" not in normalized
        assert "user_data" not in normalized
        assert normalized["id"] == "t1"

    def test_counts_artifacts(self) -> None:
        raw = {
            "id": "t1",
            "status": {},
            "artifacts": [{"index": 0}, {"index": 1}, {"index": 2}],
        }
        normalized = A2AAdapter.normalize_trace(raw)
        assert normalized["artifact_count"] == 3
        assert "artifacts" not in normalized

    def test_hashes_input(self) -> None:
        raw = {
            "id": "t1",
            "status": {},
            "input": {"role": "user", "parts": [{"type": "text", "text": "secret mission"}]},
        }
        normalized = A2AAdapter.normalize_trace(raw)
        assert "input" not in normalized
        assert "input_digest" in normalized
        assert len(normalized["input_digest"]) == 16

    def test_hash_is_deterministic(self) -> None:
        raw = {"id": "t1", "status": {}, "input": {"text": "hello"}}
        assert A2AAdapter.normalize_trace(raw)["input_digest"] == A2AAdapter.normalize_trace(raw)["input_digest"]

    def test_no_input_no_digest(self) -> None:
        raw = {"id": "t1", "status": {}}
        normalized = A2AAdapter.normalize_trace(raw)
        assert "input_digest" not in normalized
