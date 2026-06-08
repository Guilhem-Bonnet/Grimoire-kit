"""Beads → Grimoire Mission Ledger adapter (B3).

Import/export JSONL compatible with Beads work-graph format.
Beads is optional — this adapter is the only integration point.

Beads JSONL format (one record per line):
  {"kind": "issue", "id": "BEA-001", "title": "...", "status": "open",
   "labels": ["feature"], "source_repo": "api", "description": "..."}
  {"kind": "dependency", "source": "BEA-002", "target": "BEA-001"}
  {"kind": "comment", "issue_id": "BEA-001", "id": "COM-001", "body": "...",
   "author": "alice", "created_at": "2026-01-01T00:00:00Z"}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import DependencyKind, MissionState, TaskDependency, TaskType

__all__ = [
    "BeadsImportReport",
    "export_beads_jsonl",
    "import_beads_jsonl",
]

# ── Beads status → Grimoire TaskType heuristic ────────────────────────────────

_LABEL_TO_TASK_TYPE: dict[str, TaskType] = {
    "test": TaskType.TEST,
    "tests": TaskType.TEST,
    "doc": TaskType.DOCUMENTATION,
    "docs": TaskType.DOCUMENTATION,
    "architecture": TaskType.ARCHITECTURE,
    "arch": TaskType.ARCHITECTURE,
    "security": TaskType.SECURITY,
    "migration": TaskType.MIGRATION,
    "cleanup": TaskType.CLEANUP,
    "chore": TaskType.CLEANUP,
    "analysis": TaskType.ANALYSIS,
}


def _task_type_from_labels(labels: list[str]) -> TaskType:
    for label in labels:
        t = _LABEL_TO_TASK_TYPE.get(label.lower())
        if t is not None:
            return t
    return TaskType.IMPLEMENTATION


# ── Import ────────────────────────────────────────────────────────────────────

@dataclass
class BeadsImportReport:
    mission_id: str
    issues_found: int = 0
    tasks_created: int = 0
    tasks_skipped: int = 0
    dependencies_linked: int = 0
    comments_recorded: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "issues_found": self.issues_found,
            "tasks_created": self.tasks_created,
            "tasks_skipped": self.tasks_skipped,
            "dependencies_linked": self.dependencies_linked,
            "comments_recorded": self.comments_recorded,
            "errors": self.errors,
        }


def _parse_beads_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    deps: list[dict[str, Any]] = []
    comments: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = rec.get("kind", "")
        if kind == "issue":
            issues.append(rec)
        elif kind == "dependency":
            deps.append(rec)
        elif kind == "comment":
            comments.append(rec)
    return issues, deps, comments


def import_beads_jsonl(
    path: Path,
    ledger: MissionLedger,
    *,
    mission_title: str = "Beads Import",
    mission_id: str | None = None,
    actor_id: str = "beads-adapter",
) -> BeadsImportReport:
    """Import a Beads JSONL export into a MissionLedger.

    Idempotent: issues whose stable task_id already exist in the ledger
    are skipped (tasks_skipped incremented).
    """
    issues, deps, comments = _parse_beads_jsonl(path)

    # Create or reuse mission
    if mission_id is None:
        mission = ledger.create_mission(mission_title, origin="beads-adapter")
        ledger.transition_mission(mission.id, MissionState.OPEN, actor_id=actor_id)
        mission_id = mission.id
    else:
        existing = ledger.get_mission(mission_id)
        if existing is None:
            mission = ledger.create_mission(mission_title, origin="beads-adapter")
            ledger.transition_mission(mission.id, MissionState.OPEN, actor_id=actor_id)
            mission_id = mission.id

    report = BeadsImportReport(mission_id=mission_id, issues_found=len(issues))

    # First pass: create tasks (beads ID → grimoire ID mapping)
    beads_to_grimoire: dict[str, str] = {}
    existing_tasks = {t.id: t for t in ledger.list_tasks(mission_id)}

    for issue in issues:
        beads_id = str(issue.get("id", ""))
        if not beads_id:
            continue
        # Stable grimoire task ID derived from beads ID
        stable_id = f"GAO-beads-{beads_id.lower().replace('/', '-')}"
        if stable_id in existing_tasks:
            beads_to_grimoire[beads_id] = stable_id
            report.tasks_skipped += 1
            continue

        labels = list(issue.get("labels") or [])
        task_type = _task_type_from_labels(labels)
        title = str(issue.get("title") or beads_id)
        description_parts = []
        if issue.get("description"):
            description_parts.append(str(issue["description"]))
        if issue.get("source_repo"):
            description_parts.append(f"source_repo: {issue['source_repo']}")
        description_parts.append(f"beads_id: {beads_id}")
        description = " | ".join(description_parts)

        try:
            task = ledger.create_task(
                mission_id,
                title,
                type=task_type,
                description=description,
                acceptance=(f"Resolved: {title}",),
                owner=str(issue.get("assignee") or ""),
                task_id=stable_id,
            )
            beads_to_grimoire[beads_id] = task.id
            report.tasks_created += 1
        except Exception as exc:
            report.errors.append(f"Failed to create task for {beads_id}: {exc}")

    # Second pass: add dependencies using task update
    for dep in deps:
        source_beads = str(dep.get("source", ""))
        target_beads = str(dep.get("target", ""))
        dep_kind_raw = str(dep.get("kind", "blocks"))
        try:
            dep_kind = DependencyKind(dep_kind_raw)
        except ValueError:
            dep_kind = DependencyKind.BLOCKS

        source_id = beads_to_grimoire.get(source_beads)
        target_id = beads_to_grimoire.get(target_beads)
        if source_id is None or target_id is None:
            report.errors.append(
                f"Dependency {source_beads}→{target_beads}: unknown beads ID(s)"
            )
            continue

        source_task = ledger.get_task(source_id)
        if source_task is None:
            continue
        new_dep = TaskDependency(kind=dep_kind, target=target_id)
        if new_dep in source_task.dependencies:
            continue  # already linked — idempotent
        ledger._append_event(
            "task.dependency.added",
            source_id,
            "task",
            actor_id,
            {
                "task_id": source_id,
                "dependency": new_dep.to_dict(),
                "beads_source": source_beads,
                "beads_target": target_beads,
            },
        )
        report.dependencies_linked += 1

    # Third pass: comments → ledger events (provenance)
    for comment in comments:
        issue_id = str(comment.get("issue_id", ""))
        grimoire_id = beads_to_grimoire.get(issue_id)
        if grimoire_id is None:
            continue
        body = str(comment.get("body") or "")
        author = str(comment.get("author") or "beads")
        comment_id = str(comment.get("id") or "")
        ledger._append_event(
            "beads.comment",
            grimoire_id,
            "task",
            author,
            {"beads_comment_id": comment_id, "body": body},
        )
        report.comments_recorded += 1

    return report


# ── Export ────────────────────────────────────────────────────────────────────

def export_beads_jsonl(
    ledger: MissionLedger,
    mission_id: str,
    dest: Path,
) -> int:
    """Export a Grimoire mission to Beads-compatible JSONL.

    Returns the number of records written.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tasks = ledger.list_tasks(mission_id)
    count = 0
    now = datetime.now(tz=UTC).isoformat()

    with dest.open("w", encoding="utf-8") as fh:
        for task in tasks:
            issue: dict[str, Any] = {
                "kind": "issue",
                "id": task.id,
                "title": task.title,
                "status": task.status.value,
                "labels": [task.type.value],
                "description": task.description,
                "source_repo": task.surface or "",
                "created_at": task.created_at,
            }
            fh.write(json.dumps(issue, ensure_ascii=False) + "\n")
            count += 1

            for dep in task.dependencies:
                dep_rec: dict[str, Any] = {
                    "kind": "dependency",
                    "source": task.id,
                    "target": dep.target,
                    "kind_label": dep.kind.value,
                    "exported_at": now,
                }
                fh.write(json.dumps(dep_rec, ensure_ascii=False) + "\n")
                count += 1

    return count
