"""Tests for traces/schemas.py and traces/ledger.py."""

from __future__ import annotations

from grimoire.traces.ledger import TraceLedger
from grimoire.traces.schemas import (
    PolicyVerdictRef,
    TokenUsage,
    ToolCallTrace,
    TraceOutcome,
    TraceRecord,
)


def _make_trace(
    ledger: TraceLedger,
    *,
    run_id: str = "RUN-abc001",
    outcome: TraceOutcome = TraceOutcome.SUCCESS,
) -> TraceRecord:
    return ledger.record(
        run_id=run_id,
        workflow_instance_id="WFI-test-001",
        mission_id="MIS-test-001",
        task_id="GAO-test-001",
        recipe_id="recipe.test.basic",
        outcome=outcome,
        started_at="2026-01-01T00:00:00+00:00",
        completed_at="2026-01-01T00:01:00+00:00",
        agent_id="grimoire-master",
        host_id="claude-code-cli",
        model="claude-sonnet-4-6",
        tool_calls=[
            {"tool": "read_file", "verdict": "allow", "latency_ms": 12.5},
            {"tool": "write_file", "verdict": "warn", "latency_ms": 8.0},
        ],
        policy_verdicts=[
            {"verdict_id": "ver-001", "action_kind": "file_write", "verdict": "warn"},
        ],
        evidence_refs=["EVD-GAO-test-001-001"],
        error_count=0,
        retry_count=1,
        quality_score=0.9,
        latency_ms=73.0,
        token_usage={"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
        tags=["unit-test"],
    )


class TestTraceSchemas:
    def test_token_usage_roundtrip(self) -> None:
        tu = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150, estimated_cost_usd=0.002)
        assert TokenUsage.from_dict(tu.to_dict()) == tu

    def test_tool_call_trace_roundtrip(self) -> None:
        tc = ToolCallTrace(tool="read_file", verdict="allow", latency_ms=5.0, policy_verdict_id="v1")
        assert ToolCallTrace.from_dict(tc.to_dict()) == tc

    def test_policy_verdict_ref_roundtrip(self) -> None:
        pv = PolicyVerdictRef(verdict_id="v1", action_kind="file_write", verdict="warn")
        assert PolicyVerdictRef.from_dict(pv.to_dict()) == pv

    def test_trace_record_roundtrip(self, tmp_path) -> None:
        ledger = TraceLedger(tmp_path)
        trace = _make_trace(ledger)
        assert TraceRecord.from_dict(trace.to_dict()) == trace

    def test_trace_record_tool_calls_preserved(self, tmp_path) -> None:
        ledger = TraceLedger(tmp_path)
        trace = _make_trace(ledger)
        assert len(trace.tool_calls) == 2
        assert trace.tool_calls[0].tool == "read_file"
        assert trace.tool_calls[1].verdict == "warn"

    def test_trace_record_policy_verdicts(self, tmp_path) -> None:
        ledger = TraceLedger(tmp_path)
        trace = _make_trace(ledger)
        assert len(trace.policy_verdicts) == 1
        assert trace.policy_verdicts[0].verdict == "warn"


class TestTraceLedger:
    def test_record_and_get(self, tmp_path) -> None:
        ledger = TraceLedger(tmp_path)
        trace = _make_trace(ledger)
        fetched = ledger.get_trace(trace.id)
        assert fetched is not None
        assert fetched.run_id == "RUN-abc001"

    def test_list_traces_by_mission(self, tmp_path) -> None:
        ledger = TraceLedger(tmp_path)
        _make_trace(ledger, run_id="RUN-001")
        _make_trace(ledger, run_id="RUN-002")
        traces = ledger.list_traces(mission_id="MIS-test-001")
        assert len(traces) == 2

    def test_list_traces_by_outcome(self, tmp_path) -> None:
        ledger = TraceLedger(tmp_path)
        _make_trace(ledger, run_id="RUN-ok", outcome=TraceOutcome.SUCCESS)
        _make_trace(ledger, run_id="RUN-fail", outcome=TraceOutcome.FAILURE)
        failures = ledger.list_traces(outcome=TraceOutcome.FAILURE)
        assert len(failures) == 1
        assert failures[0].run_id == "RUN-fail"

    def test_policy_block_rate_no_blocks(self, tmp_path) -> None:
        ledger = TraceLedger(tmp_path)
        _make_trace(ledger)
        rate = ledger.policy_block_rate()
        assert rate == 0.0

    def test_policy_block_rate_with_blocks(self, tmp_path) -> None:
        ledger = TraceLedger(tmp_path)
        ledger.record(
            run_id="RUN-x",
            workflow_instance_id="WFI-x",
            mission_id="MIS-x",
            task_id="GAO-x",
            recipe_id="recipe.x",
            outcome=TraceOutcome.FAILURE,
            started_at="2026-01-01T00:00:00+00:00",
            tool_calls=[
                {"tool": "rm", "verdict": "block"},
                {"tool": "read", "verdict": "allow"},
            ],
        )
        rate = ledger.policy_block_rate()
        assert rate == 0.5

    def test_export_otel_jsonl(self, tmp_path) -> None:
        ledger = TraceLedger(tmp_path)
        _make_trace(ledger)
        out = tmp_path / "otel.jsonl"
        count = ledger.export_otel_jsonl(out)
        assert count == 1
        assert out.exists()
        import json
        line = json.loads(out.read_text())
        assert "traceId" in line
        assert line["name"].startswith("grimoire.workflow.")

    def test_persistence_across_instances(self, tmp_path) -> None:
        ledger1 = TraceLedger(tmp_path)
        t = _make_trace(ledger1)
        ledger2 = TraceLedger(tmp_path)
        assert ledger2.get_trace(t.id) is not None

    def test_auto_id_increments(self, tmp_path) -> None:
        ledger = TraceLedger(tmp_path)
        t1 = _make_trace(ledger, run_id="RUN-seq")
        t2 = _make_trace(ledger, run_id="RUN-seq")
        assert t1.id != t2.id
