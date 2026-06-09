"""In-memory Code Graph — index, query, and impact analysis."""

from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path

from grimoire.codegraph.parser import PythonASTParser
from grimoire.codegraph.schemas import CodeEdge, CodeNode, EdgeKind, ImpactQuery, ImpactResult, NodeKind

__all__ = ["CodeGraph"]


class CodeGraph:
    """In-memory code graph built from Python source files via ast.

    Usage::

        graph = CodeGraph()
        graph.index_directory(Path("src/"), exclude={"__pycache__", ".venv"})
        result = graph.impact_query(ImpactQuery(node_id="grimoire.runtime.kernel:RuntimeKernel"))
        print(result.affected_files)
    """

    def __init__(self) -> None:
        self._nodes: dict[str, CodeNode] = {}
        self._edges: list[CodeEdge] = []
        self._outgoing: dict[str, list[CodeEdge]] = defaultdict(list)
        self._incoming: dict[str, list[CodeEdge]] = defaultdict(list)
        self._by_file: dict[str, list[str]] = defaultdict(list)
        self._ref_count: dict[str, int] = defaultdict(int)

    # ── Indexing ───────────────────────────────────────────────────────────

    def index_directory(
        self,
        path: Path,
        exclude: set[str] | None = None,
        root: str | None = None,
    ) -> int:
        """Parse all .py files under path and add them to the graph. Returns node count."""
        parser = PythonASTParser(root=root or str(path))
        nodes, edges = parser.parse_directory(path, exclude=exclude)
        for node in nodes:
            self.add_node(node)
        for edge in edges:
            self.add_edge(edge)
        return len(nodes)

    def index_file(self, path: Path, root: str | None = None) -> int:
        parser = PythonASTParser(root=root or str(path.parent))
        nodes, edges = parser.parse_file(path)
        for node in nodes:
            self.add_node(node)
        for edge in edges:
            self.add_edge(edge)
        return len(nodes)

    def add_node(self, node: CodeNode) -> None:
        self._nodes[node.id] = node
        self._by_file[node.file_path].append(node.id)

    def add_edge(self, edge: CodeEdge) -> None:
        self._edges.append(edge)
        self._outgoing[edge.from_node].append(edge)
        self._incoming[edge.to_node].append(edge)
        self._ref_count[edge.to_node] += 1

    @property
    def nodes(self) -> tuple[CodeNode, ...]:
        """Indexed code nodes."""
        return tuple(self._nodes.values())

    @property
    def edges(self) -> tuple[CodeEdge, ...]:
        """Indexed code edges."""
        return tuple(self._edges)

    # ── Queries ────────────────────────────────────────────────────────────

    def get_node(self, node_id: str) -> CodeNode | None:
        return self._nodes.get(node_id)

    def nodes_in_file(self, file_path: str) -> list[CodeNode]:
        return [self._nodes[nid] for nid in self._by_file.get(file_path, []) if nid in self._nodes]

    def get_dependencies(self, node_id: str) -> list[CodeNode]:
        """Direct dependencies (outgoing edges) of node_id."""
        deps = []
        for edge in self._outgoing.get(node_id, []):
            target = self._nodes.get(edge.to_node)
            if target:
                deps.append(target)
        return deps

    def get_dependents(self, node_id: str) -> list[CodeNode]:
        """Nodes that depend on node_id (incoming edges)."""
        result = []
        for edge in self._incoming.get(node_id, []):
            src = self._nodes.get(edge.from_node)
            if src:
                result.append(src)
        return result

    def hotspots(self, top_n: int = 10) -> list[CodeNode]:
        """Most-referenced nodes (highest in-degree)."""
        ranked = sorted(
            [(nid, count) for nid, count in self._ref_count.items() if nid in self._nodes],
            key=lambda x: x[1],
            reverse=True,
        )
        return [self._nodes[nid] for nid, _ in ranked[:top_n]]

    def uncovered_nodes(self) -> list[CodeNode]:
        """Public non-test nodes that have no TESTED_BY edges pointing to them."""
        tested = {e.from_node for e in self._edges if e.kind == EdgeKind.TESTED_BY}
        return [
            n for n in self._nodes.values()
            if n.is_public and not n.is_test and n.id not in tested
            and n.kind in (NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS)
        ]

    def impact_query(self, query: ImpactQuery) -> ImpactResult:
        """BFS traversal from root node up to query.depth levels."""
        root = self._nodes.get(query.node_id)
        if root is None:
            return ImpactResult(
                root_node_id=query.node_id,
                affected_nodes=(),
                affected_files=(),
                test_files=(),
            )

        visited: set[str] = set()
        queue: deque[tuple[str, int]] = deque([(query.node_id, 0)])
        affected: list[CodeNode] = []

        while queue:
            current_id, depth = queue.popleft()
            if current_id in visited:
                continue
            visited.add(current_id)
            node = self._nodes.get(current_id)
            if node and node.id != query.node_id:
                affected.append(node)
            if depth >= query.depth:
                continue
            for edge in self._incoming.get(current_id, []):
                if edge.kind in query.edge_kinds and edge.from_node not in visited:
                    queue.append((edge.from_node, depth + 1))

        affected_files = sorted({n.file_path for n in affected})
        test_files: list[str] = []
        if query.include_tests:
            test_files = sorted({
                n.file_path for n in affected if n.is_test
            } | {
                n.file_path
                for edge in self._edges
                if edge.kind == EdgeKind.TESTED_BY and edge.from_node == query.node_id
                for n in [self._nodes.get(edge.to_node)]
                if n
            })

        return ImpactResult(
            root_node_id=query.node_id,
            affected_nodes=tuple(affected),
            affected_files=tuple(affected_files),
            test_files=tuple(test_files),
        )

    def stats(self) -> dict[str, int]:
        return {
            "nodes": len(self._nodes),
            "edges": len(self._edges),
            "files": len(self._by_file),
            "test_nodes": sum(1 for n in self._nodes.values() if n.is_test),
        }
