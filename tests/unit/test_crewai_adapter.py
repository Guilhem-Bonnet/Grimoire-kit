"""Tests for D3/M2 — CrewAI Flow → Grimoire Recipe adapter."""

from __future__ import annotations

from pathlib import Path

from grimoire.runtime.crewai_adapter import (
    CrewAIAdapter,
    CrewAIFlow,
    CrewAIImportReport,
    CrewAITask,
)
from grimoire.runtime.recipes import Recipe
from grimoire.traces.ledger import TraceLedger


def _flow(name: str = "my-flow", tasks: list[dict] | None = None, output_schema: dict | None = None) -> dict:
    if tasks is None:
        tasks = [
            {"id": "t1", "name": "Research", "agent": "researcher",
             "description": "Find info", "expected_output": "findings",
             "output_schema": {"type": "string"}},
            {"id": "t2", "name": "Write", "agent": "writer",
             "depends_on": ["t1"], "expected_output": "report",
             "output_schema": {"type": "string"}},
        ]
    if output_schema is None:
        output_schema = {"report": {"type": "string"}}
    return {"name": name, "tasks": tasks, "output_schema": output_schema, "version": "1.0.0"}


class TestCrewAITask:
    def test_from_dict(self) -> None:
        t = CrewAITask.from_dict({"id": "t1", "name": "T1", "agent": "dev",
                                   "depends_on": ["t0"], "expected_output": "code"})
        assert t.id == "t1"
        assert t.agent == "dev"
        assert "t0" in t.depends_on

    def test_minimal(self) -> None:
        t = CrewAITask.from_dict({"id": "x"})
        assert t.agent == ""
        assert t.depends_on == ()


class TestCrewAIFlow:
    def test_from_dict(self) -> None:
        f = CrewAIFlow.from_dict(_flow())
        assert f.name == "my-flow"
        assert len(f.tasks) == 2


class TestCrewAIAdapter:
    def test_import_produces_recipe(self) -> None:
        adapter = CrewAIAdapter()
        recipe, report = adapter.import_flow(_flow())
        assert isinstance(recipe, Recipe)
        assert isinstance(report, CrewAIImportReport)

    def test_recipe_id_from_flow_name(self) -> None:
        adapter = CrewAIAdapter()
        recipe, _ = adapter.import_flow(_flow("My Flow"))
        assert recipe.id == "crewai.my-flow"

    def test_tasks_become_steps(self) -> None:
        adapter = CrewAIAdapter()
        recipe, report = adapter.import_flow(_flow())
        assert len(recipe.steps) == 2
        assert report.tasks_converted == 2

    def test_agent_mapped_to_roles(self) -> None:
        adapter = CrewAIAdapter()
        recipe, _ = adapter.import_flow(_flow())
        step_t1 = next(s for s in recipe.steps if s.id == "t1")
        assert "researcher" in step_t1.roles

    def test_depends_on_in_description(self) -> None:
        adapter = CrewAIAdapter()
        recipe, _ = adapter.import_flow(_flow())
        step_t2 = next(s for s in recipe.steps if s.id == "t2")
        assert "t1" in step_t2.description

    def test_expected_output_in_outputs(self) -> None:
        adapter = CrewAIAdapter()
        recipe, _ = adapter.import_flow(_flow())
        step_t1 = next(s for s in recipe.steps if s.id == "t1")
        assert "findings" in step_t1.outputs

    def test_crewai_tag_on_recipe(self) -> None:
        adapter = CrewAIAdapter()
        recipe, _ = adapter.import_flow(_flow())
        assert "crewai" in recipe.tags
        assert "experimental" in recipe.tags

    def test_missing_output_schema_warns(self) -> None:
        adapter = CrewAIAdapter()
        flow_no_schema = _flow(output_schema={})
        _, report = adapter.import_flow(flow_no_schema)
        assert report.missing_output_schema is True
        assert not report.ok
        assert any("output_schema" in e for e in report.errors)

    def test_output_schema_preserved(self) -> None:
        adapter = CrewAIAdapter()
        recipe, _ = adapter.import_flow(_flow())
        assert "report" in recipe.output_schema

    def test_guardrail_no_auto_close(self) -> None:
        """Gate D3: verification gates are non-blocking so tasks land in NEEDS_VERIFICATION."""
        adapter = CrewAIAdapter()
        recipe, _ = adapter.import_flow(_flow())
        # All gates from tasks with output_schema are non-blocking
        for gate in recipe.verification_gates:
            assert gate.blocking is False

    def test_trace_emitted_with_trace_ledger(self, tmp_path: Path) -> None:
        trace = TraceLedger(tmp_path / "traces")
        adapter = CrewAIAdapter(trace_ledger=trace)
        _, report = adapter.import_flow(_flow())
        assert report.trace_id != ""
        assert report.ok
        records = trace.list_traces()
        assert len(records) == 1
        assert "crewai" in records[0].tags

    def test_no_trace_without_ledger(self) -> None:
        adapter = CrewAIAdapter()
        _, report = adapter.import_flow(_flow())
        assert report.trace_id == ""

    def test_accepts_flow_object(self) -> None:
        adapter = CrewAIAdapter()
        flow_obj = CrewAIFlow.from_dict(_flow())
        _recipe, report = adapter.import_flow(flow_obj)
        assert report.ok

    def test_report_to_dict(self) -> None:
        adapter = CrewAIAdapter()
        _, report = adapter.import_flow(_flow())
        d = report.to_dict()
        assert "flow_name" in d
        assert "tasks_converted" in d
        assert "missing_output_schema" in d

    def test_custom_prefix(self) -> None:
        adapter = CrewAIAdapter()
        recipe, _ = adapter.import_flow(_flow("wf"), recipe_id_prefix="myprefix")
        assert recipe.id.startswith("myprefix.")


class TestNormalizeCrewAITrace:
    def test_keeps_safe_fields(self) -> None:
        raw = {"flow_name": "my-flow", "task_id": "t1", "status": "done",
               "timestamp": "2026-01-01", "duration_ms": 1200,
               "secret_data": "SENSITIVE"}
        normalized = CrewAIAdapter.normalize_crewai_trace(raw)
        assert normalized["flow_name"] == "my-flow"
        assert normalized["status"] == "done"
        assert "secret_data" not in normalized

    def test_hashes_thoughts(self) -> None:
        raw = {"flow_name": "f", "thoughts": "agent reasoning here"}
        normalized = CrewAIAdapter.normalize_crewai_trace(raw)
        assert "thoughts" not in normalized
        assert "thoughts_digest" in normalized
        assert len(normalized["thoughts_digest"]) == 16

    def test_hashes_tool_output(self) -> None:
        raw = {"flow_name": "f", "tool_output": {"result": "sensitive"}}
        normalized = CrewAIAdapter.normalize_crewai_trace(raw)
        assert "tool_output" not in normalized
        assert "tool_output_digest" in normalized

    def test_counts_tasks(self) -> None:
        raw = {"flow_name": "f", "tasks": [{"id": "t1"}, {"id": "t2"}]}
        normalized = CrewAIAdapter.normalize_crewai_trace(raw)
        assert normalized["task_count"] == 2
        assert "tasks" not in normalized

    def test_source_tag(self) -> None:
        raw = {"flow_name": "f"}
        normalized = CrewAIAdapter.normalize_crewai_trace(raw)
        assert normalized["source"] == "crewai"
