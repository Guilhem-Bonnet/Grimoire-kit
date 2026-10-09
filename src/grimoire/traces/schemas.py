"""Trace and Eval schemas — consolidated run record linking events, policy, evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from grimoire.costs import Cost

TRACE_SCHEMA_V1 = "grimoire.trace.v1"
TRACE_SCHEMA_VERSION = "grimoire.trace.v2"


class TraceOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    ABORTED = "aborted"
    PARTIAL = "partial"


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Usage d'un appel ou d'une cascade. ``estimated_cost_usd`` vaut ``None`` quand rien n'est chiffré (W1-01).

    Un coût inconnu n'est pas ``0.0`` : ``unpriced_calls`` dit combien d'appels
    n'ont pas de prix, et :attr:`cost` en tire le statut (``exact`` |
    ``lower_bound`` | ``unknown``).
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float | None = None
    unpriced_calls: int = 0

    @property
    def cost(self) -> Cost:
        return Cost.from_parts(self.estimated_cost_usd, self.unpriced_calls)

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "unpriced_calls": self.unpriced_calls,
            "cost_status": self.cost.status,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any], *, schema_version: str = TRACE_SCHEMA_VERSION) -> TokenUsage:
        """Lecture tolérante de ``trace.v1`` : son ``0.0`` ne distinguait pas « gratuit » d'« inconnu ».

        Un enregistrement v1 à ``0.0`` (ou sans coût) est lu comme inconnu — le
        seul reproche honnête qu'on puisse lui faire ; un montant v1 positif
        reste exact. Un enregistrement v2 est pris tel quel.
        """
        raw = d.get("estimated_cost_usd")
        cost = float(raw) if isinstance(raw, int | float) and not isinstance(raw, bool) else None
        unpriced = int(d.get("unpriced_calls", 0) or 0)
        if schema_version == TRACE_SCHEMA_V1 and not cost:
            cost = None
        return cls(
            prompt_tokens=int(d.get("prompt_tokens", 0)),
            completion_tokens=int(d.get("completion_tokens", 0)),
            total_tokens=int(d.get("total_tokens", 0)),
            estimated_cost_usd=cost,
            unpriced_calls=max(unpriced, 1) if cost is None else unpriced,
        )


@dataclass(frozen=True, slots=True)
class ToolCallTrace:
    tool: str
    verdict: str
    args_hash: str = ""
    policy_verdict_id: str = ""
    latency_ms: float = 0.0
    #: ISO-8601 instant the call happened, when known. Empty on records
    #: written before this field existed — exporters must fall back to the
    #: parent trace's ``started_at`` rather than invent a timestamp.
    timestamp: str = ""
    #: Provider-issued tool-call id, when the caller has one. Feeds
    #: ``gen_ai.tool.call.id`` on export; empty means "not available".
    call_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "verdict": self.verdict,
            "args_hash": self.args_hash,
            "policy_verdict_id": self.policy_verdict_id,
            "latency_ms": self.latency_ms,
            "timestamp": self.timestamp,
            "call_id": self.call_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ToolCallTrace:
        return cls(
            tool=d["tool"],
            verdict=d.get("verdict", "allow"),
            args_hash=d.get("args_hash", ""),
            policy_verdict_id=d.get("policy_verdict_id", ""),
            latency_ms=float(d.get("latency_ms", 0.0)),
            timestamp=d.get("timestamp", ""),
            call_id=d.get("call_id", ""),
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
    schema_version: str = TRACE_SCHEMA_VERSION
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
        schema_version = d.get("schema_version", TRACE_SCHEMA_V1)
        return cls(
            id=d["id"],
            run_id=d["run_id"],
            workflow_instance_id=d["workflow_instance_id"],
            mission_id=d["mission_id"],
            task_id=d["task_id"],
            recipe_id=d["recipe_id"],
            outcome=TraceOutcome(d["outcome"]),
            started_at=d["started_at"],
            schema_version=schema_version,
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
            token_usage=TokenUsage.from_dict(d.get("token_usage", {}), schema_version=schema_version),
            tags=tuple(d.get("tags", [])),
        )
