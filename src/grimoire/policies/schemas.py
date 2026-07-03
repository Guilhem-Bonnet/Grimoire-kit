"""Policy request, verdict, and rule schemas."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class VerdictKind(str, Enum):
    ALLOW = "allow"
    WARN = "warn"
    BLOCK = "block"


class PolicyMode(str, Enum):
    SHADOW = "shadow"
    CANARY = "canary"
    ENFORCED = "enforced"


class ActionKind(str, Enum):
    TOOL_USE = "tool_use"
    FILE_WRITE = "file_write"
    NETWORK = "network"
    SECRET_ACCESS = "secret_access"
    PACK_ACTIVATION = "pack_activation"
    TASK_CLOSE = "task_close"
    MISSION_CLOSE = "mission_close"


class MutationClass(str, Enum):
    READ_ONLY = "read_only"
    MUTATION_CONTROLLED = "mutation_controlled"
    PACK_ACTIVATION = "pack_activation"
    DESTRUCTIVE = "destructive"


@dataclass(frozen=True, slots=True)
class PolicyActor:
    actor_id: str
    host_id: str

    def to_dict(self) -> dict[str, Any]:
        return {"actor_id": self.actor_id, "host_id": self.host_id}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PolicyActor:
        return cls(actor_id=d["actor_id"], host_id=d["host_id"])


@dataclass(frozen=True, slots=True)
class PolicyAction:
    kind: ActionKind
    tool: str
    mutation_class: MutationClass
    command: str = ""
    target_files: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "tool": self.tool,
            "mutation_class": self.mutation_class.value,
            "command": self.command,
            "target_files": list(self.target_files),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PolicyAction:
        return cls(
            kind=ActionKind(d["kind"]),
            tool=d["tool"],
            mutation_class=MutationClass(d.get("mutation_class", "read_only")),
            command=d.get("command", ""),
            target_files=tuple(d.get("target_files", [])),
        )


@dataclass(frozen=True, slots=True)
class PolicyRequest:
    id: str
    run_id: str
    task_id: str
    actor: PolicyActor
    action: PolicyAction
    risk_profile: str
    created_at: str
    schema_version: str = "grimoire.policy_request.v1"
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "actor": self.actor.to_dict(),
            "action": self.action.to_dict(),
            "risk_profile": self.risk_profile,
            "context": dict(self.context),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PolicyRequest:
        return cls(
            id=d["id"],
            run_id=d["run_id"],
            task_id=d["task_id"],
            actor=PolicyActor.from_dict(d["actor"]),
            action=PolicyAction.from_dict(d["action"]),
            risk_profile=d["risk_profile"],
            created_at=d["created_at"],
            schema_version=d.get("schema_version", "grimoire.policy_request.v1"),
            context=d.get("context", {}),
        )


@dataclass(frozen=True, slots=True)
class PolicyRule:
    """A single policy rule with an evaluator function signature.

    Rules are registered in PolicyEngine and evaluated against PolicyRequest.
    """

    id: str
    description: str
    action_kinds: tuple[ActionKind, ...]
    mutation_classes: tuple[MutationClass, ...]
    risk_profiles: tuple[str, ...]
    verdict_on_match: VerdictKind
    reason_template: str = ""

    def matches(self, request: PolicyRequest) -> bool:
        kind_ok = not self.action_kinds or request.action.kind in self.action_kinds
        mutation_ok = not self.mutation_classes or request.action.mutation_class in self.mutation_classes
        profile_ok = not self.risk_profiles or request.risk_profile in self.risk_profiles
        return kind_ok and mutation_ok and profile_ok

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "action_kinds": [k.value for k in self.action_kinds],
            "mutation_classes": [m.value for m in self.mutation_classes],
            "risk_profiles": list(self.risk_profiles),
            "verdict_on_match": self.verdict_on_match.value,
            "reason_template": self.reason_template,
        }


@dataclass(frozen=True, slots=True)
class MatchedRule:
    rule_id: str
    verdict: VerdictKind
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"rule_id": self.rule_id, "verdict": self.verdict.value, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class PolicyVerdict:
    id: str
    request_id: str
    run_id: str
    verdict: VerdictKind
    mode: PolicyMode
    reason: str
    created_at: str
    schema_version: str = "grimoire.policy_verdict.v1"
    matched_rules: tuple[MatchedRule, ...] = ()
    allow_retry_after: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "run_id": self.run_id,
            "verdict": self.verdict.value,
            "mode": self.mode.value,
            "reason": self.reason,
            "rules": {"matched": [r.to_dict() for r in self.matched_rules]},
            "allow_retry_after": list(self.allow_retry_after),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PolicyVerdict:
        rules_raw = d.get("rules", {})
        matched = [
            MatchedRule(rule_id=r["rule_id"], verdict=VerdictKind(r["verdict"]), reason=r.get("reason", ""))
            for r in rules_raw.get("matched", [])
        ]
        return cls(
            id=d["id"],
            request_id=d["request_id"],
            run_id=d["run_id"],
            verdict=VerdictKind(d["verdict"]),
            mode=PolicyMode(d["mode"]),
            reason=d["reason"],
            created_at=d["created_at"],
            schema_version=d.get("schema_version", "grimoire.policy_verdict.v1"),
            matched_rules=tuple(matched),
            allow_retry_after=tuple(d.get("allow_retry_after", [])),
        )
