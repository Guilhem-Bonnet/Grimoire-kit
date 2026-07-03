"""Mission and task dataclass schemas for the Grimoire Agent OS.

All identifiers follow the canonical formats defined in SCHEMAS-CONTRATS-cibles.md:
  Mission   → MIS-<slug>-<seq>
  Task      → GAO-<area>-<seq>
  Event     → evt-<ulid>
  Incident  → inc-<task>-<seq>
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MissionState(str, Enum):
    DRAFT = "draft"
    OPEN = "open"
    BLOCKED = "blocked"
    VERIFYING = "verifying"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class TaskState(str, Enum):
    PROPOSED = "proposed"
    READY = "ready"
    CLAIMED = "claimed"
    RUNNING = "running"
    BLOCKED = "blocked"
    NEEDS_VERIFICATION = "needs_verification"
    FAILED = "failed"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class TaskType(str, Enum):
    ANALYSIS = "analysis"
    ARCHITECTURE = "architecture"
    IMPLEMENTATION = "implementation"
    TEST = "test"
    DOCUMENTATION = "documentation"
    MIGRATION = "migration"
    SECURITY = "security"
    OPERATION = "operation"
    CLEANUP = "cleanup"


class RiskProfile(str, Enum):
    LIGHT = "light"
    STANDARD = "standard"
    STRICT = "strict"
    SECURITY_CRITICAL = "security_critical"
    RELEASE = "release"


class IncidentSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IncidentStatus(str, Enum):
    OPEN = "open"
    ACCEPTED = "accepted"
    RESOLVED = "resolved"


class DependencyKind(str, Enum):
    BLOCKS = "blocks"
    RELATES = "relates"
    PARENT_CHILD = "parent_child"
    DISCOVERED_FROM = "discovered_from"
    SUPERSEDES = "supersedes"


@dataclass(frozen=True, slots=True)
class TaskDependency:
    kind: DependencyKind
    target: str

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "target": self.target}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TaskDependency:
        return cls(kind=DependencyKind(d["kind"]), target=d["target"])


@dataclass(frozen=True, slots=True)
class TaskClaim:
    actor_id: str
    host_id: str
    exclusive_files: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "actor_id": self.actor_id,
            "host_id": self.host_id,
            "exclusive_files": list(self.exclusive_files),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TaskClaim:
        return cls(
            actor_id=d["actor_id"],
            host_id=d["host_id"],
            exclusive_files=tuple(d.get("exclusive_files", [])),
        )


@dataclass(frozen=True, slots=True)
class Mission:
    id: str
    title: str
    status: MissionState
    origin: str
    created_at: str
    schema_version: str = "grimoire.mission.v1"
    description: str = ""
    risk_profile: RiskProfile = RiskProfile.STANDARD
    created_by: str = ""
    scope_repos: tuple[str, ...] = ()
    scope_surfaces: tuple[str, ...] = ()
    scope_packs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "schema_version": self.schema_version,
            "title": self.title,
            "description": self.description,
            "status": self.status.value,
            "origin": self.origin,
            "risk_profile": self.risk_profile.value,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "scope": {
                "repos": list(self.scope_repos),
                "surfaces": list(self.scope_surfaces),
                "packs": list(self.scope_packs),
            },
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Mission:
        scope = d.get("scope", {})
        return cls(
            id=d["id"],
            title=d["title"],
            status=MissionState(d["status"]),
            origin=d["origin"],
            created_at=d["created_at"],
            schema_version=d.get("schema_version", "grimoire.mission.v1"),
            description=d.get("description", ""),
            risk_profile=RiskProfile(d.get("risk_profile", "standard")),
            created_by=d.get("created_by", ""),
            scope_repos=tuple(scope.get("repos", [])),
            scope_surfaces=tuple(scope.get("surfaces", [])),
            scope_packs=tuple(scope.get("packs", [])),
        )


@dataclass(frozen=True, slots=True)
class MissionTask:
    id: str
    mission_id: str
    title: str
    status: TaskState
    type: TaskType
    risk_profile: RiskProfile
    acceptance: tuple[str, ...]
    created_at: str
    schema_version: str = "grimoire.mission_task.v1"
    description: str = ""
    surface: str = ""
    owner: str = ""
    claim: TaskClaim | None = None
    dependencies: tuple[TaskDependency, ...] = ()
    guardrails: tuple[str, ...] = ()
    expected_evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "schema_version": self.schema_version,
            "mission_id": self.mission_id,
            "title": self.title,
            "description": self.description,
            "status": self.status.value,
            "type": self.type.value,
            "risk_profile": self.risk_profile.value,
            "surface": self.surface,
            "owner": self.owner,
            "acceptance": list(self.acceptance),
            "guardrails": list(self.guardrails),
            "expected_evidence": list(self.expected_evidence),
            "dependencies": [dep.to_dict() for dep in self.dependencies],
            "created_at": self.created_at,
        }
        if self.claim is not None:
            d["claim"] = self.claim.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MissionTask:
        claim = TaskClaim.from_dict(d["claim"]) if d.get("claim") else None
        return cls(
            id=d["id"],
            mission_id=d["mission_id"],
            title=d["title"],
            status=TaskState(d["status"]),
            type=TaskType(d["type"]),
            risk_profile=RiskProfile(d["risk_profile"]),
            acceptance=tuple(d.get("acceptance", [])),
            created_at=d["created_at"],
            schema_version=d.get("schema_version", "grimoire.mission_task.v1"),
            description=d.get("description", ""),
            surface=d.get("surface", ""),
            owner=d.get("owner", ""),
            claim=claim,
            dependencies=tuple(TaskDependency.from_dict(dep) for dep in d.get("dependencies", [])),
            guardrails=tuple(d.get("guardrails", [])),
            expected_evidence=tuple(d.get("expected_evidence", [])),
        )


@dataclass(frozen=True, slots=True)
class LedgerEvent:
    id: str
    event_type: str
    entity_id: str
    entity_kind: str
    actor_id: str
    created_at: str
    schema_version: str = "grimoire.ledger_event.v1"
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "schema_version": self.schema_version,
            "event_type": self.event_type,
            "entity_id": self.entity_id,
            "entity_kind": self.entity_kind,
            "actor_id": self.actor_id,
            "created_at": self.created_at,
            "payload": dict(self.payload),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LedgerEvent:
        return cls(
            id=d["id"],
            event_type=d["event_type"],
            entity_id=d["entity_id"],
            entity_kind=d["entity_kind"],
            actor_id=d["actor_id"],
            created_at=d["created_at"],
            schema_version=d.get("schema_version", "grimoire.ledger_event.v1"),
            payload=d.get("payload", {}),
        )


@dataclass(frozen=True, slots=True)
class Incident:
    id: str
    mission_id: str
    task_id: str
    kind: str
    severity: IncidentSeverity
    status: IncidentStatus
    summary: str
    created_at: str
    schema_version: str = "grimoire.incident.v1"
    workflow_instance_id: str = ""
    causes: tuple[str, ...] = ()
    recommended_actions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "schema_version": self.schema_version,
            "mission_id": self.mission_id,
            "task_id": self.task_id,
            "workflow_instance_id": self.workflow_instance_id,
            "kind": self.kind,
            "severity": self.severity.value,
            "status": self.status.value,
            "summary": self.summary,
            "causes": list(self.causes),
            "recommended_actions": list(self.recommended_actions),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Incident:
        return cls(
            id=d["id"],
            mission_id=d["mission_id"],
            task_id=d["task_id"],
            kind=d["kind"],
            severity=IncidentSeverity(d["severity"]),
            status=IncidentStatus(d["status"]),
            summary=d["summary"],
            created_at=d["created_at"],
            schema_version=d.get("schema_version", "grimoire.incident.v1"),
            workflow_instance_id=d.get("workflow_instance_id", ""),
            causes=tuple(d.get("causes", [])),
            recommended_actions=tuple(d.get("recommended_actions", [])),
        )
