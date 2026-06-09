"""Trace and Eval Ledger — persist, query, and export run traces.

Stores TraceRecords to JSONL. Supports OTel GenAI JSONL export
without requiring the opentelemetry-sdk package.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.traces.schemas import (
    PolicyVerdictRef,
    TokenUsage,
    ToolCallTrace,
    TraceOutcome,
    TraceRecord,
)

__all__ = ["TraceLedger"]

_OTEL_SPAN_KIND_INTERNAL = "SPAN_KIND_INTERNAL"
_OTEL_STATUS_OK = "STATUS_CODE_OK"
_OTEL_STATUS_ERROR = "STATUS_CODE_ERROR"


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _ns(iso: str) -> int:
    """Convert ISO timestamp to nanoseconds since epoch (OTel convention)."""
    try:
        dt = datetime.fromisoformat(iso)
        return int(dt.timestamp() * 1_000_000_000)
    except (ValueError, OSError):
        return 0


class TraceLedger:
    """Persist and query run traces.

    Usage::

        ledger = TraceLedger(Path("_grimoire-runtime-output/traces"))
        trace = ledger.record(
            run_id="RUN-abc123",
            workflow_instance_id="WFI-...",
            mission_id="MIS-...",
            task_id="GAO-...",
            recipe_id="recipe.pack.convert",
            outcome=TraceOutcome.SUCCESS,
            started_at=...,
        )
        ledger.export_otel_jsonl(Path("traces-otel.jsonl"))
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._traces_path = root / "traces.jsonl"

    # ── Private helpers ────────────────────────────────────────────────────

    def _append(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with open(self._traces_path, "a", encoding="utf-8") as fh:
            fh.write(line)

    def _load_all(self) -> list[TraceRecord]:
        records: list[TraceRecord] = []
        if not self._traces_path.exists():
            return records
        for line in self._traces_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            with contextlib.suppress(json.JSONDecodeError, KeyError):
                records.append(TraceRecord.from_dict(json.loads(line)))
        return records

    def _next_id(self, run_id: str) -> str:
        existing = [t for t in self._load_all() if t.run_id == run_id]
        return f"TRC-{run_id}-{len(existing) + 1:03d}"

    # ── Public write API ───────────────────────────────────────────────────

    def record(
        self,
        *,
        run_id: str,
        workflow_instance_id: str,
        mission_id: str,
        task_id: str,
        recipe_id: str,
        outcome: TraceOutcome,
        started_at: str,
        completed_at: str = "",
        agent_id: str = "",
        host_id: str = "",
        model: str = "",
        tool_calls: list[dict[str, Any]] | None = None,
        policy_verdicts: list[dict[str, Any]] | None = None,
        evidence_refs: list[str] | None = None,
        error_count: int = 0,
        retry_count: int = 0,
        quality_score: float = 0.0,
        latency_ms: float = 0.0,
        token_usage: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        trace_id: str | None = None,
    ) -> TraceRecord:
        trace = TraceRecord(
            id=trace_id or self._next_id(run_id),
            run_id=run_id,
            workflow_instance_id=workflow_instance_id,
            mission_id=mission_id,
            task_id=task_id,
            recipe_id=recipe_id,
            outcome=outcome,
            started_at=started_at,
            completed_at=completed_at or _now_iso(),
            agent_id=agent_id,
            host_id=host_id,
            model=model,
            tool_calls=tuple(ToolCallTrace.from_dict(tc) for tc in (tool_calls or [])),
            policy_verdicts=tuple(PolicyVerdictRef.from_dict(pv) for pv in (policy_verdicts or [])),
            evidence_refs=tuple(evidence_refs or []),
            error_count=error_count,
            retry_count=retry_count,
            quality_score=quality_score,
            latency_ms=latency_ms,
            token_usage=TokenUsage.from_dict(token_usage or {}),
            tags=tuple(tags or []),
        )
        self._append(trace.to_dict())
        return trace

    # ── Queries ────────────────────────────────────────────────────────────

    def get_trace(self, trace_id: str) -> TraceRecord | None:
        for t in self._load_all():
            if t.id == trace_id:
                return t
        return None

    def list_traces(
        self,
        *,
        mission_id: str | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
        outcome: TraceOutcome | None = None,
    ) -> list[TraceRecord]:
        traces = self._load_all()
        if mission_id:
            traces = [t for t in traces if t.mission_id == mission_id]
        if task_id:
            traces = [t for t in traces if t.task_id == task_id]
        if run_id:
            traces = [t for t in traces if t.run_id == run_id]
        if outcome:
            traces = [t for t in traces if t.outcome == outcome]
        return traces

    def policy_block_rate(self, mission_id: str | None = None) -> float:
        """Fraction of tool calls that were blocked."""
        traces = self.list_traces(mission_id=mission_id)
        total = sum(len(t.tool_calls) for t in traces)
        if total == 0:
            return 0.0
        blocked = sum(1 for t in traces for tc in t.tool_calls if tc.verdict == "block")
        return blocked / total

    # ── OTel GenAI JSONL export ────────────────────────────────────────────

    def export_otel_jsonl(self, dest: Path, *, mission_id: str | None = None) -> int:
        """Export traces as OTel GenAI JSONL spans. Returns count written.

        Format follows OpenTelemetry GenAI semantic conventions.
        No opentelemetry-sdk dependency required — pure JSONL output.
        """
        traces = self.list_traces(mission_id=mission_id)
        lines: list[str] = []
        for trace in traces:
            span = self._to_otel_span(trace)
            lines.append(json.dumps(span, ensure_ascii=False))
        dest.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return len(lines)

    # ── Langfuse JSONL export ──────────────────────────────────────────────

    def export_langfuse(self, dest: Path, *, mission_id: str | None = None) -> int:
        """Export traces as Langfuse trace objects (JSONL). Returns count written.

        Format follows Langfuse /api/public/traces REST contract.
        No langfuse SDK required — pure JSONL output for batch import.
        """
        traces = self.list_traces(mission_id=mission_id)
        lines: list[str] = []
        for trace in traces:
            obj = self._to_langfuse_trace(trace)
            lines.append(json.dumps(obj, ensure_ascii=False))
        dest.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return len(lines)

    def _to_langfuse_trace(self, trace: TraceRecord) -> dict[str, Any]:
        tags = list(trace.tags)
        if trace.outcome.value not in tags:
            tags.append(trace.outcome.value)
        return {
            "id": trace.id,
            "name": f"grimoire.{trace.recipe_id}",
            "timestamp": trace.started_at,
            "input": {
                "run_id": trace.run_id,
                "mission_id": trace.mission_id,
                "task_id": trace.task_id,
                "recipe_id": trace.recipe_id,
                "workflow_instance_id": trace.workflow_instance_id,
            },
            "output": {
                "outcome": trace.outcome.value,
                "quality_score": trace.quality_score,
                "error_count": trace.error_count,
            },
            "metadata": {
                "agent_id": trace.agent_id,
                "host_id": trace.host_id,
                "model": trace.model,
                "latency_ms": trace.latency_ms,
                "retry_count": trace.retry_count,
                "policy_blocks": sum(1 for tc in trace.tool_calls if tc.verdict == "block"),
                "prompt_tokens": trace.token_usage.prompt_tokens,
                "completion_tokens": trace.token_usage.completion_tokens,
            },
            "tags": tags,
            "userId": trace.agent_id or None,
            "sessionId": trace.run_id,
            "endTime": trace.completed_at or None,
        }

    def _to_otel_span(self, trace: TraceRecord) -> dict[str, Any]:
        trace_id_hex = uuid.uuid5(uuid.NAMESPACE_URL, trace.run_id).hex
        span_id_hex = uuid.uuid5(uuid.NAMESPACE_URL, trace.id).hex[:16]
        status_code = _OTEL_STATUS_OK if trace.outcome == TraceOutcome.SUCCESS else _OTEL_STATUS_ERROR

        attrs: dict[str, Any] = {
            "grimoire.run_id": trace.run_id,
            "grimoire.mission_id": trace.mission_id,
            "grimoire.task_id": trace.task_id,
            "grimoire.recipe_id": trace.recipe_id,
            "grimoire.workflow_instance_id": trace.workflow_instance_id,
            "grimoire.outcome": trace.outcome.value,
            "gen_ai.system": "grimoire",
            "gen_ai.request.model": trace.model,
            "gen_ai.usage.input_tokens": trace.token_usage.prompt_tokens,
            "gen_ai.usage.output_tokens": trace.token_usage.completion_tokens,
            "grimoire.error_count": trace.error_count,
            "grimoire.retry_count": trace.retry_count,
            "grimoire.quality_score": trace.quality_score,
            "grimoire.policy_blocks": sum(1 for tc in trace.tool_calls if tc.verdict == "block"),
        }

        events = [
            {
                "timeUnixNano": _ns(tc.latency_ms.__class__.__name__),
                "name": "grimoire.tool_call",
                "attributes": {
                    "tool": tc.tool,
                    "verdict": tc.verdict,
                    "policy_verdict_id": tc.policy_verdict_id,
                },
            }
            for tc in trace.tool_calls
        ]

        return {
            "traceId": trace_id_hex,
            "spanId": span_id_hex,
            "name": f"grimoire.workflow.{trace.recipe_id}",
            "kind": _OTEL_SPAN_KIND_INTERNAL,
            "startTimeUnixNano": _ns(trace.started_at),
            "endTimeUnixNano": _ns(trace.completed_at),
            "status": {"code": status_code},
            "attributes": attrs,
            "events": events,
            "resource": {
                "attributes": {
                    "service.name": "grimoire-kit",
                    "grimoire.host_id": trace.host_id,
                    "grimoire.agent_id": trace.agent_id,
                }
            },
        }
