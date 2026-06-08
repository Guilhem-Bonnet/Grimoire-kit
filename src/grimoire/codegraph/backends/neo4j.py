"""Neo4j backend for Code Graph — persist nodes/edges and run Cypher impact queries.

Requires: pip install grimoire-kit[neo4j]
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from grimoire.codegraph.schemas import CodeEdge, CodeNode, ImpactQuery, ImpactResult, NodeKind

if TYPE_CHECKING:
    from grimoire.codegraph.graph import CodeGraph

__all__ = ["Neo4jCodeGraphBackend"]

_NEO4J_AVAILABLE = False
try:
    import neo4j as _neo4j_mod  # noqa: F401

    _NEO4J_AVAILABLE = True
except ImportError:
    pass


def _require_neo4j() -> Any:
    if not _NEO4J_AVAILABLE:
        raise ImportError(
            "neo4j driver not installed. Run:\n  pip install grimoire-kit[neo4j]"
        )
    import neo4j

    return neo4j


_MERGE_NODE_CYPHER = """
MERGE (n:CodeNode {id: $id})
SET n.kind = $kind,
    n.name = $name,
    n.file_path = $file_path,
    n.line_start = $line_start,
    n.line_end = $line_end,
    n.module = $module,
    n.docstring = $docstring,
    n.is_test = $is_test,
    n.is_public = $is_public
"""

_MERGE_EDGE_CYPHER = """
MATCH (a:CodeNode {id: $from_node})
MATCH (b:CodeNode {id: $to_node})
MERGE (a)-[r:CODE_EDGE {kind: $kind}]->(b)
"""

_IMPACT_CYPHER = """
MATCH path = (root:CodeNode {id: $node_id})<-[:CODE_EDGE*1..$depth]-(dependent:CodeNode)
RETURN DISTINCT dependent
"""

_STATS_CYPHER = """
MATCH (n:CodeNode) RETURN count(n) AS node_count
UNION ALL
MATCH ()-[r:CODE_EDGE]->() RETURN count(r) AS node_count
"""


class Neo4jCodeGraphBackend:
    """Persist a CodeGraph to Neo4j and run impact queries via Cypher.

    Usage::

        backend = Neo4jCodeGraphBackend("bolt://localhost:7687", auth=("neo4j", "password"))
        backend.push_graph(graph)
        result = backend.query_impact(ImpactQuery(node_id="grimoire.runtime.kernel:RuntimeKernel"))
    """

    def __init__(self, uri: str, *, auth: tuple[str, str] | None = None, database: str = "neo4j") -> None:
        neo4j = _require_neo4j()
        self._driver = neo4j.GraphDatabase.driver(uri, auth=auth)
        self._database = database

    def close(self) -> None:
        self._driver.close()

    def __enter__(self) -> Neo4jCodeGraphBackend:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def push_graph(self, graph: CodeGraph, *, batch_size: int = 500) -> dict[str, int]:
        """Upsert all nodes and edges from graph into Neo4j. Returns counts."""
        nodes = list(graph._nodes.values())
        edges = list(graph._edges)
        node_count = 0
        edge_count = 0

        with self._driver.session(database=self._database) as session:
            # Ensure constraint exists
            session.run("CREATE CONSTRAINT code_node_id IF NOT EXISTS FOR (n:CodeNode) REQUIRE n.id IS UNIQUE")

            for i in range(0, len(nodes), batch_size):
                batch = nodes[i : i + batch_size]
                session.execute_write(self._write_nodes, batch)
                node_count += len(batch)

            for i in range(0, len(edges), batch_size):
                edge_batch = edges[i : i + batch_size]
                session.execute_write(self._write_edges, edge_batch)
                edge_count += len(edge_batch)

        return {"nodes": node_count, "edges": edge_count}

    @staticmethod
    def _write_nodes(tx: Any, nodes: list[CodeNode]) -> None:
        for node in nodes:
            tx.run(_MERGE_NODE_CYPHER, id=node.id, kind=node.kind.value, name=node.name, file_path=node.file_path, line_start=node.line_start, line_end=node.line_end, module=node.module, docstring=node.docstring, is_test=node.is_test, is_public=node.is_public)

    @staticmethod
    def _write_edges(tx: Any, edges: list[CodeEdge]) -> None:
        for edge in edges:
            tx.run(_MERGE_EDGE_CYPHER, from_node=edge.from_node, to_node=edge.to_node, kind=edge.kind.value)

    def query_impact(self, query: ImpactQuery) -> ImpactResult:
        """Run a BFS impact query via Cypher. Faster than in-memory for large graphs."""
        with self._driver.session(database=self._database) as session:
            result = session.run(_IMPACT_CYPHER, node_id=query.node_id, depth=query.depth)
            affected: list[CodeNode] = []
            for record in result:
                neo_node = record["dependent"]
                try:
                    node = CodeNode(
                        id=neo_node["id"],
                        kind=NodeKind(neo_node["kind"]),
                        name=neo_node["name"],
                        file_path=neo_node["file_path"],
                        line_start=int(neo_node["line_start"]),
                        line_end=int(neo_node["line_end"]),
                        module=neo_node.get("module", ""),
                        docstring=neo_node.get("docstring", ""),
                        is_test=bool(neo_node.get("is_test", False)),
                        is_public=bool(neo_node.get("is_public", True)),
                    )
                    affected.append(node)
                except (KeyError, ValueError):
                    pass

        affected_files = sorted({n.file_path for n in affected})
        test_files = sorted({n.file_path for n in affected if n.is_test}) if query.include_tests else []

        return ImpactResult(
            root_node_id=query.node_id,
            affected_nodes=tuple(affected),
            affected_files=tuple(affected_files),
            test_files=tuple(test_files),
        )

    def clear(self) -> None:
        """Delete all CodeNode and CODE_EDGE data from Neo4j."""
        with self._driver.session(database=self._database) as session:
            session.run("MATCH (n:CodeNode) DETACH DELETE n")

    def node_count(self) -> int:
        with self._driver.session(database=self._database) as session:
            result = session.run("MATCH (n:CodeNode) RETURN count(n) AS c")
            record = result.single()
            return int(record["c"]) if record else 0
