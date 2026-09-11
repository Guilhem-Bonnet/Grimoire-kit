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

    def test_agent_dispatch_counts_ignores_untagged_traces(self, tmp_path) -> None:
        """Un gate de tâche ou un appel modèle n'est pas un choix d'agent (issue #365)."""
        ledger = TraceLedger(tmp_path)
        _make_trace(ledger, run_id="RUN-untagged")
        assert ledger.agent_dispatch_counts() == {}

    def test_agent_dispatch_counts_counts_and_dates_tagged_traces(self, tmp_path) -> None:
        from grimoire.traces.ledger import AGENT_DISPATCH_TAG

        ledger = TraceLedger(tmp_path)
        for run_id, started_at in (
            ("RUN-a1", "2026-01-01T00:00:00+00:00"),
            ("RUN-a2", "2026-01-02T00:00:00+00:00"),
        ):
            ledger.record(
                run_id=run_id,
                workflow_instance_id="",
                mission_id="",
                task_id="",
                recipe_id="grimoire.entry-persona",
                outcome=TraceOutcome.SUCCESS,
                started_at=started_at,
                agent_id="concierge",
                tags=[AGENT_DISPATCH_TAG],
            )
        ledger.record(
            run_id="RUN-b1",
            workflow_instance_id="",
            mission_id="",
            task_id="",
            recipe_id="grimoire.entry-persona",
            outcome=TraceOutcome.SUCCESS,
            started_at="2026-01-03T00:00:00+00:00",
            agent_id="scribe",
            tags=[AGENT_DISPATCH_TAG],
        )
        counts = ledger.agent_dispatch_counts()
        assert counts == {
            "concierge": {"count": 2, "last_seen": "2026-01-02T00:00:00+00:00"},
            "scribe": {"count": 1, "last_seen": "2026-01-03T00:00:00+00:00"},
        }

    def test_agent_miss_counts_ignores_untagged_traces(self, tmp_path) -> None:
        """Un choix d'agent (ou un gate de tâche) n'est pas un non-choix (issue #389)."""
        ledger = TraceLedger(tmp_path)
        _make_trace(ledger, run_id="RUN-untagged")
        assert ledger.agent_miss_counts() == {}

    def test_agent_miss_counts_counts_and_dates_by_specialty(self, tmp_path) -> None:
        """Symétrique de ``agent_dispatch_counts`` : la clé d'agrégation est la spécialité manquante."""
        from grimoire.traces.ledger import AGENT_MISS_TAG

        ledger = TraceLedger(tmp_path)
        for run_id, started_at in (
            ("RUN-m1", "2026-01-01T00:00:00+00:00"),
            ("RUN-m2", "2026-01-02T00:00:00+00:00"),
        ):
            ledger.record(
                run_id=run_id,
                workflow_instance_id="",
                mission_id="",
                task_id="",
                recipe_id="grimoire.entry-persona.miss",
                outcome=TraceOutcome.FAILURE,
                started_at=started_at,
                agent_id="generic-dev",
                tags=[AGENT_MISS_TAG, "category:infra", "specialty:terraform"],
            )
        ledger.record(
            run_id="RUN-m3",
            workflow_instance_id="",
            mission_id="",
            task_id="",
            recipe_id="grimoire.entry-persona.miss",
            outcome=TraceOutcome.FAILURE,
            started_at="2026-01-03T00:00:00+00:00",
            agent_id="",
            tags=[AGENT_MISS_TAG, "category:design"],
        )
        counts = ledger.agent_miss_counts()
        assert counts == {
            "terraform": {"count": 2, "last_seen": "2026-01-02T00:00:00+00:00"},
            "(non nommée)": {"count": 1, "last_seen": "2026-01-03T00:00:00+00:00"},
        }

    def test_export_otel_jsonl(self, tmp_path) -> None:
        """One `invoke_agent` parent span, plus one `execute_tool` child per call."""
        ledger = TraceLedger(tmp_path)
        _make_trace(ledger)
        out = tmp_path / "otel.jsonl"
        count = ledger.export_otel_jsonl(out)
        assert count == 3  # 1 parent (invoke_agent) + 2 children (execute_tool)
        assert out.exists()
        import json

        lines = [json.loads(line) for line in out.read_text().splitlines()]
        parent = lines[0]
        assert "traceId" in parent
        assert parent["name"] == "invoke_agent grimoire-master"
        assert parent["attributes"]["gen_ai.provider.name"] == "grimoire"
        assert parent["attributes"]["gen_ai.operation.name"] == "invoke_agent"
        assert parent["attributes"]["gen_ai.conversation.id"] == "MIS-test-001"
        assert parent["attributes"]["gen_ai.agent.name"] == "grimoire-master"
        assert "gen_ai.system" not in parent["attributes"]

    def test_export_otel_tool_call_spans_have_real_timestamps(self, tmp_path) -> None:
        """Regression: `_ns` used to receive `tc.latency_ms.__class__.__name__`
        (the string ``"float"``) instead of an ISO timestamp, so every
        tool-call span's `timeUnixNano` read zero — a silently empty OTel
        export for every tool call ever traced.
        """
        ledger = TraceLedger(tmp_path)
        _make_trace(ledger)
        out = tmp_path / "otel.jsonl"
        ledger.export_otel_jsonl(out)
        import json

        lines = [json.loads(line) for line in out.read_text().splitlines()]
        tool_spans = [line for line in lines if line.get("parentSpanId")]
        assert len(tool_spans) == 2
        for span in tool_spans:
            assert span["name"].startswith("execute_tool ")
            assert span["startTimeUnixNano"] > 0
            assert span["endTimeUnixNano"] >= span["startTimeUnixNano"]
            assert span["attributes"]["gen_ai.operation.name"] == "execute_tool"
            assert span["attributes"]["gen_ai.tool.name"] in {"read_file", "write_file"}
            assert span["attributes"]["gen_ai.provider.name"] == "grimoire"

    def test_export_otel_tool_call_uses_its_own_timestamp_when_recorded(self, tmp_path) -> None:
        ledger = TraceLedger(tmp_path)
        ledger.record(
            run_id="RUN-ts",
            workflow_instance_id="WFI-ts",
            mission_id="MIS-ts",
            task_id="GAO-ts",
            recipe_id="recipe.ts",
            outcome=TraceOutcome.SUCCESS,
            started_at="2026-01-01T00:00:00+00:00",
            completed_at="2026-01-01T00:01:00+00:00",
            agent_id="dev",
            tool_calls=[
                {
                    "tool": "grep",
                    "verdict": "allow",
                    "latency_ms": 40.0,
                    "timestamp": "2026-01-01T00:00:30+00:00",
                    "call_id": "call-001",
                }
            ],
        )
        out = tmp_path / "otel.jsonl"
        ledger.export_otel_jsonl(out)
        import json

        lines = [json.loads(line) for line in out.read_text().splitlines()]
        child = next(line for line in lines if line.get("parentSpanId"))
        expected_start_ns = int(
            __import__("datetime").datetime.fromisoformat("2026-01-01T00:00:30+00:00").timestamp() * 1_000_000_000
        )
        assert child["startTimeUnixNano"] == expected_start_ns
        assert child["endTimeUnixNano"] == expected_start_ns + int(40.0 * 1_000_000)
        assert child["attributes"]["gen_ai.tool.call.id"] == "call-001"

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
