"""Trace and Eval schemas — consolidated run record linking events, policy, evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TraceOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    ABORTED = "aborted"
    PARTIAL = "partial"


@dataclass(frozen=True, slots=True)
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TokenUsage:
        return cls(
            prompt_tokens=int(d.get("prompt_tokens", 0)),
            completion_tokens=int(d.get("completion_tokens", 0)),
            total_tokens=int(d.get("total_tokens", 0)),
            estimated_cost_usd=float(d.get("estimated_cost_usd", 0.0)),
        )


@dataclass(frozen=True, slots=True)
class ToolCallTrace:
    tool: str
    verdict: str
    args_hash: str = ""
    policy_verdict_id: str = ""
    latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "verdict": self.verdict,
            "args_hash": self.args_hash,
            "policy_verdict_id": self.policy_verdict_id,
            "latency_ms": self.latency_ms,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ToolCallTrace:
        return cls(
            tool=d["tool"],
            verdict=d.get("verdict", "allow"),
            args_hash=d.get("args_hash", ""),
            policy_verdict_id=d.get("policy_verdict_id", ""),
            latency_ms=float(d.get("latency_ms", 0.0)),
        )


@dataclass(frozen=True, slots=True)
class PolicyVerdictRef:
    verdict_id: str
    action_kind: str
    verdict: str

    def to_dict(self) -> dict[str, Any]:
        return {"verdict_id": self.verdict_id, "action_kind": self.action_kind, "verdict": self.verdict}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PolicyVerdictRef:
        return cls(verdict_id=d["verdict_id"], action_kind=d["action_kind"], verdict=d["verdict"])


@dataclass(frozen=True, slots=True)
class TraceRecord:
    """Consolidated trace for one workflow run."""

    id: str
    run_id: str
    workflow_instance_id: str
    mission_id: str
    task_id: str
    recipe_id: str
    outcome: TraceOutcome
    started_at: str
    schema_version: str = "grimoire.trace.v1"
    completed_at: str = ""
    agent_id: str = ""
    host_id: str = ""
    model: str = ""
    tool_calls: tuple[ToolCallTrace, ...] = ()
    policy_verdicts: tuple[PolicyVerdictRef, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    error_count: int = 0
    retry_count: int = 0
    quality_score: float = 0.0
    latency_ms: float = 0.0
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    tags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "workflow_instance_id": self.workflow_instance_id,
            "mission_id": self.mission_id,
            "task_id": self.task_id,
            "recipe_id": self.recipe_id,
            "agent": {"agent_id": self.agent_id, "host_id": self.host_id, "model": self.model},
            "outcome": self.outcome.value,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "policy_verdicts": [pv.to_dict() for pv in self.policy_verdicts],
            "evidence_refs": list(self.evidence_refs),
            "stats": {
                "error_count": self.error_count,
                "retry_count": self.retry_count,
                "quality_score": self.quality_score,
                "latency_ms": self.latency_ms,
            },
            "token_usage": self.token_usage.to_dict(),
            "tags": list(self.tags),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TraceRecord:
        agent = d.get("agent", {})
        stats = d.get("stats", {})
        return cls(
            id=d["id"],
            run_id=d["run_id"],
            workflow_instance_id=d["workflow_instance_id"],
            mission_id=d["mission_id"],
            task_id=d["task_id"],
            recipe_id=d["recipe_id"],
            outcome=TraceOutcome(d["outcome"]),
            started_at=d["started_at"],
            schema_version=d.get("schema_version", "grimoire.trace.v1"),
            completed_at=d.get("completed_at", ""),
            agent_id=agent.get("agent_id", ""),
            host_id=agent.get("host_id", ""),
            model=agent.get("model", ""),
            tool_calls=tuple(ToolCallTrace.from_dict(tc) for tc in d.get("tool_calls", [])),
            policy_verdicts=tuple(PolicyVerdictRef.from_dict(pv) for pv in d.get("policy_verdicts", [])),
            evidence_refs=tuple(d.get("evidence_refs", [])),
            error_count=int(stats.get("error_count", 0)),
            retry_count=int(stats.get("retry_count", 0)),
            quality_score=float(stats.get("quality_score", 0.0)),
            latency_ms=float(stats.get("latency_ms", 0.0)),
            token_usage=TokenUsage.from_dict(d.get("token_usage", {})),
            tags=tuple(d.get("tags", [])),
        )
