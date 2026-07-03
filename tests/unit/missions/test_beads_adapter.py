"""Tests for B3 — Beads → Grimoire Mission Ledger adapter."""

from __future__ import annotations

import json
from pathlib import Path

from grimoire.missions.beads_adapter import BeadsImportReport, export_beads_jsonl, import_beads_jsonl
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import DependencyKind, MissionState, TaskType


def _write_beads(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")


def _ledger(tmp_path: Path) -> MissionLedger:
    return MissionLedger(tmp_path / "ledger")


# ── Import ─────────────────────────────────────────────────────────────────────

class TestBeadsImport:
    def test_import_creates_mission_and_tasks(self, tmp_path: Path) -> None:
        beads = tmp_path / "export.jsonl"
        _write_beads(beads, [
            {"kind": "issue", "id": "BEA-001", "title": "Implement auth", "status": "open", "labels": []},
            {"kind": "issue", "id": "BEA-002", "title": "Write tests", "status": "open", "labels": ["test"]},
        ])
        ledger = _ledger(tmp_path)
        report = import_beads_jsonl(beads, ledger, mission_title="Auth Sprint")

        assert isinstance(report, BeadsImportReport)
        assert report.issues_found == 2
        assert report.tasks_created == 2
        assert report.tasks_skipped == 0
        assert report.errors == []

    def test_import_task_type_from_labels(self, tmp_path: Path) -> None:
        beads = tmp_path / "export.jsonl"
        _write_beads(beads, [
            {"kind": "issue", "id": "BEA-001", "title": "Auth tests", "status": "open", "labels": ["test"]},
            {"kind": "issue", "id": "BEA-002", "title": "Auth docs", "status": "open", "labels": ["docs"]},
            {"kind": "issue", "id": "BEA-003", "title": "Auth impl", "status": "open", "labels": []},
        ])
        ledger = _ledger(tmp_path)
        report = import_beads_jsonl(beads, ledger)

        tasks = {t.id: t for t in ledger.list_tasks(report.mission_id)}
        assert tasks["GAO-beads-bea-001"].type == TaskType.TEST
        assert tasks["GAO-beads-bea-002"].type == TaskType.DOCUMENTATION
        assert tasks["GAO-beads-bea-003"].type == TaskType.IMPLEMENTATION

    def test_import_dependencies_linked(self, tmp_path: Path) -> None:
        beads = tmp_path / "export.jsonl"
        _write_beads(beads, [
            {"kind": "issue", "id": "BEA-001", "title": "Base task", "status": "open", "labels": []},
            {"kind": "issue", "id": "BEA-002", "title": "Depends task", "status": "open", "labels": []},
            {"kind": "dependency", "source": "BEA-002", "target": "BEA-001"},
        ])
        ledger = _ledger(tmp_path)
        report = import_beads_jsonl(beads, ledger)

        assert report.dependencies_linked == 1
        assert report.errors == []

    def test_import_comments_recorded(self, tmp_path: Path) -> None:
        beads = tmp_path / "export.jsonl"
        _write_beads(beads, [
            {"kind": "issue", "id": "BEA-001", "title": "Task", "status": "open", "labels": []},
            {"kind": "comment", "issue_id": "BEA-001", "id": "COM-001", "body": "Started", "author": "alice"},
        ])
        ledger = _ledger(tmp_path)
        report = import_beads_jsonl(beads, ledger)

        assert report.comments_recorded == 1
        events = ledger.events_for("GAO-beads-bea-001")
        assert any(e.event_type == "beads.comment" for e in events)

    def test_import_idempotent_second_run_skips(self, tmp_path: Path) -> None:
        beads = tmp_path / "export.jsonl"
        _write_beads(beads, [
            {"kind": "issue", "id": "BEA-001", "title": "Task", "status": "open", "labels": []},
        ])
        ledger = _ledger(tmp_path)
        r1 = import_beads_jsonl(beads, ledger)
        # Second import reuses mission_id — tasks already exist
        r2 = import_beads_jsonl(beads, ledger, mission_id=r1.mission_id)

        assert r1.tasks_created == 1
        assert r2.tasks_skipped == 1
        assert r2.tasks_created == 0
        # No duplicates
        tasks = ledger.list_tasks(r1.mission_id)
        assert len(tasks) == 1

    def test_import_preserves_beads_id_in_description(self, tmp_path: Path) -> None:
        beads = tmp_path / "export.jsonl"
        _write_beads(beads, [
            {"kind": "issue", "id": "BEA-042", "title": "Auth module", "status": "open",
             "labels": [], "source_repo": "api-service"},
        ])
        ledger = _ledger(tmp_path)
        import_beads_jsonl(beads, ledger)

        task = ledger.get_task("GAO-beads-bea-042")
        assert task is not None
        assert "beads_id: BEA-042" in task.description
        assert "api-service" in task.description

    def test_import_skips_malformed_issues(self, tmp_path: Path) -> None:
        beads = tmp_path / "export.jsonl"
        _write_beads(beads, [
            {"kind": "issue", "title": "No ID"},  # missing id
            {"kind": "issue", "id": "BEA-001", "title": "Valid", "status": "open", "labels": []},
        ])
        ledger = _ledger(tmp_path)
        report = import_beads_jsonl(beads, ledger)

        assert report.issues_found == 2
        assert report.tasks_created == 1

    def test_import_dependency_unknown_beads_id_error(self, tmp_path: Path) -> None:
        beads = tmp_path / "export.jsonl"
        _write_beads(beads, [
            {"kind": "issue", "id": "BEA-001", "title": "Task", "status": "open", "labels": []},
            {"kind": "dependency", "source": "BEA-001", "target": "BEA-UNKNOWN"},
        ])
        ledger = _ledger(tmp_path)
        report = import_beads_jsonl(beads, ledger)

        assert len(report.errors) == 1
        assert "BEA-UNKNOWN" in report.errors[0]

    def test_import_empty_file(self, tmp_path: Path) -> None:
        beads = tmp_path / "export.jsonl"
        beads.write_text("", encoding="utf-8")
        ledger = _ledger(tmp_path)
        report = import_beads_jsonl(beads, ledger)

        assert report.issues_found == 0
        assert report.tasks_created == 0

    def test_import_report_to_dict(self, tmp_path: Path) -> None:
        beads = tmp_path / "export.jsonl"
        _write_beads(beads, [
            {"kind": "issue", "id": "BEA-001", "title": "Task", "status": "open", "labels": []},
        ])
        ledger = _ledger(tmp_path)
        report = import_beads_jsonl(beads, ledger)
        d = report.to_dict()

        assert "mission_id" in d
        assert "tasks_created" in d
        assert d["tasks_created"] == 1


# ── Export ─────────────────────────────────────────────────────────────────────

class TestBeadsExport:
    def test_export_produces_jsonl(self, tmp_path: Path) -> None:
        ledger = _ledger(tmp_path)
        mission = ledger.create_mission("M", origin="test")
        ledger.transition_mission(mission.id, MissionState.OPEN)
        ledger.create_task(mission.id, "Task A", acceptance=("done",))
        ledger.create_task(mission.id, "Task B", acceptance=("done",))

        out = tmp_path / "beads.jsonl"
        count = export_beads_jsonl(ledger, mission.id, out)

        assert count == 2
        lines = [json.loads(line) for line in out.read_text().splitlines()]
        assert all(rec["kind"] == "issue" for rec in lines)
        titles = {rec["title"] for rec in lines}
        assert "Task A" in titles and "Task B" in titles

    def test_export_includes_dependencies(self, tmp_path: Path) -> None:
        from grimoire.missions.schemas import TaskDependency
        ledger = _ledger(tmp_path)
        mission = ledger.create_mission("M", origin="test")
        ledger.transition_mission(mission.id, MissionState.OPEN)
        t1 = ledger.create_task(mission.id, "Base", acceptance=("done",))
        ledger.create_task(
            mission.id, "Child", acceptance=("done",),
            dependencies=(TaskDependency(kind=DependencyKind.BLOCKS, target=t1.id),),
        )

        out = tmp_path / "beads.jsonl"
        count = export_beads_jsonl(ledger, mission.id, out)

        records = [json.loads(line) for line in out.read_text().splitlines()]
        dep_records = [r for r in records if r["kind"] == "dependency"]
        assert count == 3  # 2 issues + 1 dependency
        assert len(dep_records) == 1
        assert dep_records[0]["target"] == t1.id

    def test_export_empty_mission(self, tmp_path: Path) -> None:
        ledger = _ledger(tmp_path)
        mission = ledger.create_mission("M", origin="test")
        ledger.transition_mission(mission.id, MissionState.OPEN)

        out = tmp_path / "beads.jsonl"
        count = export_beads_jsonl(ledger, mission.id, out)

        assert count == 0
        assert out.read_text() == ""

    def test_roundtrip_import_export(self, tmp_path: Path) -> None:
        beads = tmp_path / "export.jsonl"
        _write_beads(beads, [
            {"kind": "issue", "id": "BEA-001", "title": "Alpha", "status": "open", "labels": []},
            {"kind": "issue", "id": "BEA-002", "title": "Beta", "status": "open", "labels": ["test"]},
            {"kind": "dependency", "source": "BEA-002", "target": "BEA-001"},
        ])
        ledger = _ledger(tmp_path)
        report = import_beads_jsonl(beads, ledger)

        out = tmp_path / "re-export.jsonl"
        count = export_beads_jsonl(ledger, report.mission_id, out)

        records = [json.loads(line) for line in out.read_text().splitlines()]
        issues = [r for r in records if r["kind"] == "issue"]
        assert len(issues) == 2
        assert count >= 2  # 2 issues (deps are emitted per task if stored)
