"""D3/M2 — CrewAI Flow → Grimoire Recipe adapter.

Maps CrewAI Flow/Task definitions to Grimoire Recipes and normalizes
external CrewAI traces into the TraceLedger.

Guardrails:
- CrewAI runner does not replace the Grimoire RuntimeKernel.
- Imported flows cannot close tasks autonomously (NEEDS_VERIFICATION guardrail).
- Output schema is required for each flow (enforced at import).
- External traces are normalized before recording.

CrewAI flow format (dict)::

    {
      "name": "my-flow",
      "description": "...",
      "tasks": [
        {
          "id": "task1",
          "name": "Research",
          "description": "Research the topic",
          "agent": "researcher",
          "expected_output": "research_results",
          "output_schema": {"type": "string"}
        },
        {
          "id": "task2",
          "name": "Write",
          "agent": "writer",
          "depends_on": ["task1"],
          "expected_output": "report",
          "output_schema": {"type": "string"}
        }
      ],
      "output_schema": {"report": {"type": "string"}}
    }

M2 — Integration contract:
- CrewAI tasks map 1:1 to RecipeSteps.
- CrewAI agents map to RecipeStep.roles.
- depends_on is recorded in step description (no parallel closure without evidence).
- CrewAI execution traces are normalized via normalize_crewai_trace().
- CrewAI does NOT become a source of truth: all state lives in MissionLedger.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from grimoire.evidence.schemas import EvidenceProfile
from grimoire.runtime.recipes import Recipe, RecipeStep, VerificationGate
from grimoire.traces.ledger import TraceLedger
from grimoire.traces.schemas import TraceOutcome

__all__ = [
    "CrewAIAdapter",
    "CrewAIFlow",
    "CrewAIImportReport",
    "CrewAITask",
]

_EXPERIMENTAL_TAG = "experimental"
_CREWAI_TAG = "crewai"


@dataclass(frozen=True, slots=True)
class CrewAITask:
    id: str
    name: str
    agent: str = ""
    description: str = ""
    expected_output: str = ""
    depends_on: tuple[str, ...] = ()
    output_schema: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CrewAITask:
        return cls(
            id=str(d["id"]),
            name=str(d.get("name", d["id"])),
            agent=str(d.get("agent", "")),
            description=str(d.get("description", "")),
            expected_output=str(d.get("expected_output", "")),
            depends_on=tuple(str(x) for x in d.get("depends_on", [])),
            output_schema=dict(d.get("output_schema") or {}),
        )


@dataclass(frozen=True, slots=True)
class CrewAIFlow:
    name: str
    tasks: tuple[CrewAITask, ...]
    description: str = ""
    output_schema: dict[str, Any] = field(default_factory=dict)
    version: str = "1.0.0"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CrewAIFlow:
        return cls(
            name=str(d["name"]),
            tasks=tuple(CrewAITask.from_dict(t) for t in d.get("tasks", [])),
            description=str(d.get("description", "")),
            output_schema=dict(d.get("output_schema") or {}),
            version=str(d.get("version", "1.0.0")),
        )


@dataclass
class CrewAIImportReport:
    flow_name: str
    recipe_id: str
    tasks_converted: int = 0
    missing_output_schema: bool = False
    errors: list[str] = field(default_factory=list)
    trace_id: str = ""

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0 and not self.missing_output_schema

    def to_dict(self) -> dict[str, Any]:
        return {
            "flow_name": self.flow_name,
            "recipe_id": self.recipe_id,
            "tasks_converted": self.tasks_converted,
            "missing_output_schema": self.missing_output_schema,
            "errors": self.errors,
            "trace_id": self.trace_id,
        }


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:64]


class CrewAIAdapter:
    """Import CrewAI flow definitions as Grimoire Recipes.

    Usage::

        adapter = CrewAIAdapter(trace_ledger=trace)
        recipe, report = adapter.import_flow(flow_dict)

        # Normalize a CrewAI execution trace for storage
        safe_trace = CrewAIAdapter.normalize_crewai_trace(raw_trace)
    """

    def __init__(self, trace_ledger: TraceLedger | None = None) -> None:
        self._trace = trace_ledger

    def import_flow(
        self,
        flow: dict[str, Any] | CrewAIFlow,
        *,
        recipe_id_prefix: str = "crewai",
    ) -> tuple[Recipe, CrewAIImportReport]:
        """Convert a CrewAI flow dict to a Grimoire Recipe.

        The imported recipe:
        - Is tagged experimental + crewai.
        - Requires an output_schema (warned if missing).
        - Cannot close tasks autonomously (no CLOSED state gate).
        """
        if isinstance(flow, dict):
            flow = CrewAIFlow.from_dict(flow)

        recipe_id = f"{recipe_id_prefix}.{_slugify(flow.name)}"
        report = CrewAIImportReport(flow_name=flow.name, recipe_id=recipe_id)

        if not flow.output_schema:
            report.missing_output_schema = True
            report.errors.append("output_schema is required for CrewAI flow import (M2 guardrail)")

        steps: list[RecipeStep] = []
        gates: list[VerificationGate] = []

        for task in flow.tasks:
            desc_parts = [task.description]
            if task.depends_on:
                desc_parts.append(f"depends_on: {', '.join(task.depends_on)}")
            if task.expected_output:
                desc_parts.append(f"expected_output: {task.expected_output}")

            step = RecipeStep(
                id=task.id,
                name=task.name,
                description=" | ".join(p for p in desc_parts if p),
                roles=(task.agent,) if task.agent else (),
                tools_allowed=(),
                evidence_required=bool(task.output_schema),
                outputs=(task.expected_output,) if task.expected_output else (),
            )
            steps.append(step)
            report.tasks_converted += 1

            if task.output_schema:
                gates.append(VerificationGate(
                    step_id=task.id,
                    required_evidence_kinds=("report",),
                    blocking=False,
                ))

        now = datetime.now(tz=UTC).isoformat()
        recipe = Recipe(
            id=recipe_id,
            name=flow.name,
            version=flow.version,
            description=flow.description,
            steps=tuple(steps),
            verification_gates=tuple(gates),
            output_schema=flow.output_schema,
            evidence_profile=EvidenceProfile.LIGHT,
            tags=(_EXPERIMENTAL_TAG, _CREWAI_TAG),
            created_at=now,
            updated_at=now,
        )

        if self._trace:
            try:
                run_id = f"crewai-{_slugify(flow.name)}"
                outcome = TraceOutcome.SUCCESS if report.ok else TraceOutcome.PARTIAL
                self._trace.record(
                    run_id=run_id,
                    workflow_instance_id=run_id,
                    mission_id="",
                    task_id="",
                    recipe_id=recipe_id,
                    outcome=outcome,
                    started_at=now,
                    tags=[_CREWAI_TAG, "import", *([] if report.ok else ["guardrail-error"])],
                )
                report.trace_id = run_id
            except Exception as exc:
                report.errors.append(f"Trace record: {exc}")

        return recipe, report

    @staticmethod
    def normalize_crewai_trace(raw: dict[str, Any]) -> dict[str, Any]:
        """Normalize a raw CrewAI execution trace for TraceLedger storage.

        Keeps only structural fields; strips agent internals, thoughts, and tool outputs.
        Hashes sensitive content.
        """
        import hashlib
        import json

        safe = {}
        for key in ("flow_name", "task_id", "status", "timestamp", "duration_ms"):
            if key in raw:
                safe[key] = raw[key]

        # Task count but not task content
        if "tasks" in raw:
            safe["task_count"] = len(raw["tasks"])

        # Hash agent reasoning/thoughts
        for sensitive_key in ("thoughts", "tool_output", "context", "memory"):
            if sensitive_key in raw:
                payload_str = json.dumps(raw[sensitive_key], sort_keys=True, ensure_ascii=False)
                safe[f"{sensitive_key}_digest"] = hashlib.sha256(
                    payload_str.encode()
                ).hexdigest()[:16]

        safe["source"] = "crewai"
        return safe
