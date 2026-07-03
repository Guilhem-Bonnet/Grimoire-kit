"""D3/M2 — LangGraph StateGraph → Grimoire Recipe adapter.

Maps LangGraph StateGraph node/edge definitions to Grimoire Recipes and
normalizes external LangGraph execution traces into the TraceLedger.

Guardrails (same as CrewAI adapter):
- LangGraph runner does not replace the Grimoire RuntimeKernel.
- Imported graphs cannot close tasks autonomously (NEEDS_VERIFICATION).
- Output schema is required per graph (enforced at import).
- External traces are normalized before recording.

LangGraph graph format (dict)::

    {
      "name": "my-graph",
      "description": "...",
      "version": "1.0.0",
      "nodes": [
        {"id": "node1", "name": "Research", "description": "...", "type": "agent"},
        {"id": "node2", "name": "Write",    "description": "...", "type": "tool"}
      ],
      "edges": [
        {"from": "START", "to": "node1"},
        {"from": "node1", "to": "node2"},
        {"from": "node2", "to": "END"}
      ],
      "conditional_edges": [
        {"from": "node2", "conditions": {"continue": "node1", "done": "END"}}
      ],
      "output_schema": {"report": {"type": "string"}}
    }

M2 — Integration contract:
- LangGraph nodes map 1:1 to RecipeSteps.
- LangGraph edges are recorded as step descriptions (sequential order).
- Conditional edges generate a VerificationGate per branch node.
- LangGraph does NOT become a source of truth: all state lives in MissionLedger.
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
    "LangGraphAdapter",
    "LangGraphEdge",
    "LangGraphGraph",
    "LangGraphImportReport",
    "LangGraphNode",
    "normalize_langgraph_trace",
]

_EXPERIMENTAL_TAG = "experimental"
_LANGGRAPH_TAG = "langgraph"


@dataclass(frozen=True, slots=True)
class LangGraphNode:
    id: str
    name: str
    node_type: str = "agent"
    description: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LangGraphNode:
        return cls(
            id=str(d["id"]),
            name=str(d.get("name", d["id"])),
            node_type=str(d.get("type", "agent")),
            description=str(d.get("description", "")),
        )


@dataclass(frozen=True, slots=True)
class LangGraphEdge:
    from_node: str
    to_node: str
    conditions: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LangGraphEdge:
        return cls(
            from_node=str(d.get("from", d.get("from_node", ""))),
            to_node=str(d.get("to", d.get("to_node", ""))),
            conditions=dict(d.get("conditions", {})),
        )


@dataclass(frozen=True, slots=True)
class LangGraphGraph:
    name: str
    nodes: tuple[LangGraphNode, ...]
    edges: tuple[LangGraphEdge, ...]
    conditional_edges: tuple[LangGraphEdge, ...]
    description: str = ""
    version: str = "1.0.0"
    output_schema: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LangGraphGraph:
        return cls(
            name=str(d["name"]),
            nodes=tuple(LangGraphNode.from_dict(n) for n in d.get("nodes", [])),
            edges=tuple(LangGraphEdge.from_dict(e) for e in d.get("edges", [])),
            conditional_edges=tuple(
                LangGraphEdge.from_dict(e) for e in d.get("conditional_edges", [])
            ),
            description=str(d.get("description", "")),
            version=str(d.get("version", "1.0.0")),
            output_schema=dict(d.get("output_schema") or {}),
        )


@dataclass
class LangGraphImportReport:
    graph_name: str
    recipe_id: str
    nodes_converted: int = 0
    conditional_edge_count: int = 0
    missing_output_schema: bool = False
    errors: list[str] = field(default_factory=list)
    trace_id: str = ""

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0 and not self.missing_output_schema

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph_name": self.graph_name,
            "recipe_id": self.recipe_id,
            "nodes_converted": self.nodes_converted,
            "conditional_edge_count": self.conditional_edge_count,
            "missing_output_schema": self.missing_output_schema,
            "errors": self.errors,
            "trace_id": self.trace_id,
        }


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:64]


def _build_edge_description(node: LangGraphNode, graph: LangGraphGraph) -> str:
    successors = [
        e.to_node for e in graph.edges if e.from_node == node.id and e.to_node != "END"
    ]
    cond_nodes = {
        target
        for e in graph.conditional_edges
        if e.from_node == node.id
        for target in e.conditions.values()
        if target != "END"
    }
    parts = [node.description] if node.description else []
    if successors:
        parts.append(f"next: {', '.join(successors)}")
    if cond_nodes:
        parts.append(f"conditional_next: {', '.join(sorted(cond_nodes))}")
    return " | ".join(parts)


class LangGraphAdapter:
    """Import LangGraph StateGraph definitions as Grimoire Recipes.

    Usage::

        adapter = LangGraphAdapter(trace_ledger=trace)
        recipe, report = adapter.import_graph(graph_dict)

        # Normalize a LangGraph execution trace for storage
        safe_trace = LangGraphAdapter.normalize_graph_trace(raw_trace)
    """

    def __init__(self, trace_ledger: TraceLedger | None = None) -> None:
        self._trace = trace_ledger

    def import_graph(
        self,
        graph: dict[str, Any] | LangGraphGraph,
        *,
        recipe_id_prefix: str = "langgraph",
    ) -> tuple[Recipe, LangGraphImportReport]:
        """Convert a LangGraph StateGraph dict to a Grimoire Recipe.

        The imported recipe:
        - Is tagged experimental + langgraph.
        - Requires an output_schema (warned if missing).
        - Cannot close tasks autonomously (no CLOSED state gate).
        - Conditional edges generate non-blocking VerificationGates.
        """
        if isinstance(graph, dict):
            graph = LangGraphGraph.from_dict(graph)

        recipe_id = f"{recipe_id_prefix}.{_slugify(graph.name)}"
        report = LangGraphImportReport(graph_name=graph.name, recipe_id=recipe_id)

        if not graph.output_schema:
            report.missing_output_schema = True
            report.errors.append("output_schema is required for LangGraph import (M2 guardrail)")

        # Build set of conditional branch nodes for gate generation
        conditional_branch_nodes = {e.from_node for e in graph.conditional_edges}
        report.conditional_edge_count = len(graph.conditional_edges)

        steps: list[RecipeStep] = []
        gates: list[VerificationGate] = []

        # Exclude virtual START/END nodes
        real_nodes = [n for n in graph.nodes if n.id not in ("START", "END")]
        for node in real_nodes:
            step = RecipeStep(
                id=node.id,
                name=node.name,
                description=_build_edge_description(node, graph),
                roles=(node.node_type,) if node.node_type else (),
                tools_allowed=(),
                evidence_required=node.id in conditional_branch_nodes,
                outputs=(),
            )
            steps.append(step)
            report.nodes_converted += 1

            if node.id in conditional_branch_nodes:
                gates.append(VerificationGate(
                    step_id=node.id,
                    required_evidence_kinds=("decision",),
                    blocking=False,
                ))

        now = datetime.now(tz=UTC).isoformat()
        recipe = Recipe(
            id=recipe_id,
            name=graph.name,
            version=graph.version,
            description=graph.description,
            steps=tuple(steps),
            verification_gates=tuple(gates),
            output_schema=graph.output_schema,
            evidence_profile=EvidenceProfile.LIGHT,
            tags=(_EXPERIMENTAL_TAG, _LANGGRAPH_TAG),
            created_at=now,
            updated_at=now,
        )

        if self._trace:
            try:
                run_id = f"langgraph-{_slugify(graph.name)}"
                outcome = TraceOutcome.SUCCESS if report.ok else TraceOutcome.PARTIAL
                self._trace.record(
                    run_id=run_id,
                    workflow_instance_id=run_id,
                    mission_id="",
                    task_id="",
                    recipe_id=recipe_id,
                    outcome=outcome,
                    started_at=now,
                    tags=[_LANGGRAPH_TAG, "import", *([] if report.ok else ["guardrail-error"])],
                )
            except Exception:  # noqa: S110
                pass

        return recipe, report

    @staticmethod
    def normalize_graph_trace(raw_trace: dict[str, Any]) -> dict[str, Any]:
        """Normalize a LangGraph execution trace for safe storage.

        Hashes the full `state` blob (may contain sensitive intermediate outputs)
        and strips tool outputs that could contain secrets.
        """
        import hashlib
        import json

        normalized: dict[str, Any] = {
            "run_id": raw_trace.get("run_id", ""),
            "graph_id": raw_trace.get("graph_id", raw_trace.get("name", "")),
            "status": raw_trace.get("status", ""),
            "nodes_executed": raw_trace.get("nodes_executed", []),
            "edge_path": raw_trace.get("edge_path", []),
        }

        state = raw_trace.get("state") or raw_trace.get("output")
        if state is not None:
            normalized["state_digest"] = hashlib.sha256(
                json.dumps(state, sort_keys=True, default=str).encode()
            ).hexdigest()

        if "error" in raw_trace:
            normalized["error"] = str(raw_trace["error"])[:500]

        return normalized


def normalize_langgraph_trace(raw_trace: dict[str, Any]) -> dict[str, Any]:
    """Module-level convenience wrapper for LangGraphAdapter.normalize_graph_trace."""
    return LangGraphAdapter.normalize_graph_trace(raw_trace)
