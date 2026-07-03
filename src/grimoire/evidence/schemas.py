"""Evidence pack and verification verdict schemas."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class EvidenceKind(str, Enum):
    TEST = "test"
    LOG = "log"
    DIFF = "diff"
    DOC = "doc"
    SCHEMA = "schema"
    TRACE = "trace"
    SCREENSHOT = "screenshot"
    REPORT = "report"


class EvidenceProfile(str, Enum):
    LIGHT = "light"
    STANDARD = "standard"
    STRICT = "strict"
    SECURITY_CRITICAL = "security_critical"
    RELEASE = "release"


class VerdictResult(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    id: str
    kind: EvidenceKind
    uri: str
    digest: str
    summary: str = ""
    schema_version: str = "grimoire.evidence_item.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "schema_version": self.schema_version,
            "kind": self.kind.value,
            "uri": self.uri,
            "digest": self.digest,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EvidenceItem:
        return cls(
            id=d["id"],
            kind=EvidenceKind(d["kind"]),
            uri=d["uri"],
            digest=d["digest"],
            summary=d.get("summary", ""),
            schema_version=d.get("schema_version", "grimoire.evidence_item.v1"),
        )

    @classmethod
    def from_file(cls, item_id: str, kind: EvidenceKind, path: Path, summary: str = "") -> EvidenceItem:
        """Create an EvidenceItem by computing SHA-256 digest of a local file."""
        data = path.read_bytes()
        digest = "sha256-" + hashlib.sha256(data).hexdigest()
        return cls(id=item_id, kind=kind, uri=str(path), digest=digest, summary=summary)

    @classmethod
    def from_text(cls, item_id: str, kind: EvidenceKind, text: str, uri: str = "", summary: str = "") -> EvidenceItem:
        """Create an EvidenceItem with a SHA-256 digest of the provided text."""
        digest = "sha256-" + hashlib.sha256(text.encode()).hexdigest()
        return cls(id=item_id, kind=kind, uri=uri, digest=digest, summary=summary)


@dataclass(frozen=True, slots=True)
class EvidenceCoverage:
    acceptance_covered: tuple[str, ...]
    acceptance_missing: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "acceptance_covered": list(self.acceptance_covered),
            "acceptance_missing": list(self.acceptance_missing),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EvidenceCoverage:
        return cls(
            acceptance_covered=tuple(d.get("acceptance_covered", [])),
            acceptance_missing=tuple(d.get("acceptance_missing", [])),
        )


@dataclass(frozen=True, slots=True)
class EvidencePack:
    id: str
    task_id: str
    profile: EvidenceProfile
    items: tuple[EvidenceItem, ...]
    created_at: str
    schema_version: str = "grimoire.evidence_pack.v1"
    workflow_instance_id: str = ""
    coverage: EvidenceCoverage | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "workflow_instance_id": self.workflow_instance_id,
            "profile": self.profile.value,
            "items": [item.to_dict() for item in self.items],
            "created_at": self.created_at,
        }
        if self.coverage is not None:
            d["coverage"] = self.coverage.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EvidencePack:
        coverage = EvidenceCoverage.from_dict(d["coverage"]) if d.get("coverage") else None
        return cls(
            id=d["id"],
            task_id=d["task_id"],
            profile=EvidenceProfile(d["profile"]),
            items=tuple(EvidenceItem.from_dict(i) for i in d.get("items", [])),
            created_at=d["created_at"],
            schema_version=d.get("schema_version", "grimoire.evidence_pack.v1"),
            workflow_instance_id=d.get("workflow_instance_id", ""),
            coverage=coverage,
        )


@dataclass(frozen=True, slots=True)
class VerificationCheck:
    id: str
    result: VerdictResult
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "result": self.result.value, "reason": self.reason}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> VerificationCheck:
        return cls(id=d["id"], result=VerdictResult(d["result"]), reason=d.get("reason", ""))


@dataclass(frozen=True, slots=True)
class VerdictDecision:
    close_task: bool
    reopen_task: bool
    create_incident: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "close_task": self.close_task,
            "reopen_task": self.reopen_task,
            "create_incident": self.create_incident,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> VerdictDecision:
        return cls(
            close_task=d.get("close_task", False),
            reopen_task=d.get("reopen_task", False),
            create_incident=d.get("create_incident", False),
        )


@dataclass(frozen=True, slots=True)
class VerificationVerdict:
    id: str
    task_id: str
    evidence_pack_id: str
    verdict: VerdictResult
    profile: EvidenceProfile
    checks: tuple[VerificationCheck, ...]
    decision: VerdictDecision
    created_at: str
    schema_version: str = "grimoire.verification_verdict.v1"
    created_by: str = "verifier-runtime"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "evidence_pack_id": self.evidence_pack_id,
            "verdict": self.verdict.value,
            "profile": self.profile.value,
            "checks": [c.to_dict() for c in self.checks],
            "decision": self.decision.to_dict(),
            "created_by": self.created_by,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> VerificationVerdict:
        return cls(
            id=d["id"],
            task_id=d["task_id"],
            evidence_pack_id=d["evidence_pack_id"],
            verdict=VerdictResult(d["verdict"]),
            profile=EvidenceProfile(d["profile"]),
            checks=tuple(VerificationCheck.from_dict(c) for c in d.get("checks", [])),
            decision=VerdictDecision.from_dict(d.get("decision", {})),
            created_at=d["created_at"],
            schema_version=d.get("schema_version", "grimoire.verification_verdict.v1"),
            created_by=d.get("created_by", "verifier-runtime"),
        )
