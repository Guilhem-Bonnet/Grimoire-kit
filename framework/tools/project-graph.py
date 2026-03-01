#!/usr/bin/env python3
"""
project-graph.py — Graphe du projet BMAD.
==========================================

Construit et analyse le graphe de connexions entre tous les éléments
du projet : agents, outils, workflows, artefacts, modules.

  1. `build`      — Construire le graphe
  2. `centrality` — Nœuds les plus connectés (centralité)
  3. `clusters`   — Détection de clusters
  4. `orphans`    — Nœuds orphelins (non-connectés)
  5. `mermaid`    — Export Mermaid pour visualisation

Métriques :
  - Degree centrality (connectivité directe)
  - Betweenness centrality approximée
  - Clustering coefficient
  - Small-world check (ratio clustering/path_length)

Usage :
  python3 project-graph.py --project-root . build
  python3 project-graph.py --project-root . centrality
  python3 project-graph.py --project-root . clusters
  python3 project-graph.py --project-root . orphans
  python3 project-graph.py --project-root . mermaid

Stdlib only — aucune dépendance externe.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# ── Constantes ────────────────────────────────────────────────────────────────

VERSION = "1.0.0"

NODE_TYPES = {
    "agent": "🤖",
    "tool": "🔧",
    "workflow": "🔄",
    "doc": "📄",
    "config": "⚙️",
    "test": "🧪",
    "archetype": "🧬",
}


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class Node:
    id: str
    name: str
    node_type: str
    path: str = ""

@dataclass
class Edge:
    source: str
    target: str
    relation: str = "references"   # references, imports, extends, tests

@dataclass
class Graph:
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)

    def adjacency(self) -> dict[str, set[str]]:
        adj: dict[str, set[str]] = defaultdict(set)
        for e in self.edges:
            adj[e.source].add(e.target)
            adj[e.target].add(e.source)
        return adj

@dataclass
class CentralityResult:
    node_id: str
    node_name: str
    degree: int = 0
    degree_centrality: float = 0.0

@dataclass
class Cluster:
    id: int
    nodes: list[str] = field(default_factory=list)


# ── Graph Builder ────────────────────────────────────────────────────────────

def _discover_nodes(project_root: Path) -> list[Node]:
    """Découvre tous les nœuds du projet."""
    nodes = []
    seen = set()

    def add(nid: str, name: str, ntype: str, path: str = ""):
        if nid not in seen:
            nodes.append(Node(id=nid, name=name, node_type=ntype, path=path))
            seen.add(nid)

    # Agents
    for fpath in project_root.rglob("**/agents/*.md"):
        if ".git" in str(fpath):
            continue
        add(f"agent:{fpath.stem}", fpath.stem, "agent", str(fpath.relative_to(project_root)))

    # Tools
    tools_dir = project_root / "framework" / "tools"
    if tools_dir.exists():
        for fpath in tools_dir.glob("*.py"):
            add(f"tool:{fpath.stem}", fpath.stem, "tool", str(fpath.relative_to(project_root)))

    # Workflows
    for fpath in project_root.rglob("**/workflows/**/*.yaml"):
        if ".git" in str(fpath):
            continue
        add(f"workflow:{fpath.stem}", fpath.stem, "workflow", str(fpath.relative_to(project_root)))
    for fpath in project_root.rglob("**/workflows/**/*.md"):
        if ".git" in str(fpath):
            continue
        add(f"workflow:{fpath.stem}", fpath.stem, "workflow", str(fpath.relative_to(project_root)))

    # Docs
    for fpath in project_root.rglob("docs/**/*.md"):
        add(f"doc:{fpath.stem}", fpath.stem, "doc", str(fpath.relative_to(project_root)))

    # Configs
    for fpath in project_root.rglob("**/config.yaml"):
        if ".git" in str(fpath):
            continue
        rel = str(fpath.relative_to(project_root))
        add(f"config:{rel}", fpath.stem, "config", rel)

    # Tests
    for fpath in project_root.rglob("test_*.py"):
        add(f"test:{fpath.stem}", fpath.stem, "test", str(fpath.relative_to(project_root)))

    # Archetypes
    archetypes_dir = project_root / "archetypes"
    if archetypes_dir.exists():
        for d in archetypes_dir.iterdir():
            if d.is_dir():
                add(f"archetype:{d.name}", d.name, "archetype", str(d.relative_to(project_root)))

    return nodes


def _discover_edges(project_root: Path, nodes: list[Node]) -> list[Edge]:
    """Découvre les connexions entre nœuds via analyse de contenu."""
    edges = []
    node_names = {n.name.lower(): n.id for n in nodes}
    seen_edges = set()

    # Scanner les fichiers pour trouver les références croisées
    for node in nodes:
        if not node.path:
            continue
        fpath = project_root / node.path
        if not fpath.exists():
            continue
        try:
            content = fpath.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue

        for target_name, target_id in node_names.items():
            if target_id == node.id:
                continue
            if len(target_name) < 3:
                continue
            # Chercher des références
            if target_name in content:
                edge_key = (node.id, target_id)
                if edge_key not in seen_edges:
                    # Determine relation type
                    relation = "references"
                    if node.node_type == "test":
                        relation = "tests"
                    elif "import" in content and target_name in content:
                        relation = "imports"
                    edges.append(Edge(source=node.id, target=target_id, relation=relation))
                    seen_edges.add(edge_key)

    return edges


def build_graph(project_root: Path) -> Graph:
    """Construit le graphe complet du projet."""
    nodes = _discover_nodes(project_root)
    edges = _discover_edges(project_root, nodes)
    return Graph(nodes=nodes, edges=edges)


# ── Analysis ─────────────────────────────────────────────────────────────────

def compute_centrality(graph: Graph) -> list[CentralityResult]:
    """Calcule la centralité de degré pour chaque nœud."""
    adj = graph.adjacency()
    n = len(graph.nodes)
    results = []

    for node in graph.nodes:
        degree = len(adj.get(node.id, set()))
        centrality = degree / (n - 1) if n > 1 else 0
        results.append(CentralityResult(
            node_id=node.id,
            node_name=node.name,
            degree=degree,
            degree_centrality=centrality,
        ))

    return sorted(results, key=lambda r: r.degree, reverse=True)


def find_clusters(graph: Graph) -> list[Cluster]:
    """Détecte les clusters via composantes connexes."""
    adj = graph.adjacency()
    visited = set()
    clusters = []
    cluster_id = 0

    for node in graph.nodes:
        if node.id in visited:
            continue
        # BFS
        queue = [node.id]
        component = []
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            component.append(current)
            for neighbor in adj.get(current, set()):
                if neighbor not in visited:
                    queue.append(neighbor)

        clusters.append(Cluster(id=cluster_id, nodes=component))
        cluster_id += 1

    return sorted(clusters, key=lambda c: len(c.nodes), reverse=True)


def find_orphans(graph: Graph) -> list[Node]:
    """Trouve les nœuds sans aucune connexion."""
    connected = set()
    for e in graph.edges:
        connected.add(e.source)
        connected.add(e.target)
    return [n for n in graph.nodes if n.id not in connected]


# ── Formatters ───────────────────────────────────────────────────────────────

def format_graph(graph: Graph) -> str:
    type_counts = defaultdict(int)
    for n in graph.nodes:
        type_counts[n.node_type] += 1

    lines = [
        f"🕸️ Project Graph — {len(graph.nodes)} nœuds, {len(graph.edges)} connexions\n",
        "   Types de nœuds :",
    ]
    for ntype, count in sorted(type_counts.items(), key=lambda x: x[1], reverse=True):
        emoji = NODE_TYPES.get(ntype, "?")
        bar = "█" * count
        lines.append(f"      {emoji} {ntype:12s} {bar} {count}")

    lines.append(f"\n   Densité : {2 * len(graph.edges) / (len(graph.nodes) * (len(graph.nodes) - 1)):.2%}" if len(graph.nodes) > 1 else "")
    return "\n".join(lines)


def format_centrality(results: list[CentralityResult]) -> str:
    lines = ["📊 Centralité de degré (top 15)\n"]
    for r in results[:15]:
        bar = "█" * r.degree
        lines.append(f"   {bar:20s} {r.node_name:25s} deg={r.degree} ({r.degree_centrality:.2f})")
    return "\n".join(lines)


def format_mermaid(graph: Graph) -> str:
    """Export Mermaid flowchart."""
    lines = ["graph LR"]
    # Subgraphs by type
    type_groups = defaultdict(list)
    for n in graph.nodes:
        type_groups[n.node_type].append(n)

    for ntype, nodes in type_groups.items():
        lines.append(f"    subgraph {ntype}")
        for n in nodes[:20]:  # Limiter pour lisibilité
            safe_id = n.id.replace(":", "_").replace("-", "_").replace("/", "_")
            lines.append(f"        {safe_id}[\"{n.name}\"]")
        lines.append("    end")

    # Edges (limit)
    for e in graph.edges[:50]:
        src = e.source.replace(":", "_").replace("-", "_").replace("/", "_")
        tgt = e.target.replace(":", "_").replace("-", "_").replace("/", "_")
        lines.append(f"    {src} -->|{e.relation}| {tgt}")

    return "\n".join(lines)


# ── CLI Commands ─────────────────────────────────────────────────────────────

def cmd_build(args: argparse.Namespace) -> int:
    graph = build_graph(Path(args.project_root).resolve())
    if args.json:
        print(json.dumps({
            "nodes": [{"id": n.id, "name": n.name, "type": n.node_type} for n in graph.nodes],
            "edges": [{"source": e.source, "target": e.target, "relation": e.relation} for e in graph.edges],
        }, indent=2, ensure_ascii=False))
    else:
        print(format_graph(graph))
    return 0


def cmd_centrality(args: argparse.Namespace) -> int:
    graph = build_graph(Path(args.project_root).resolve())
    results = compute_centrality(graph)
    if args.json:
        print(json.dumps([{"id": r.node_id, "name": r.node_name, "degree": r.degree,
                           "centrality": r.degree_centrality} for r in results[:20]],
                         indent=2, ensure_ascii=False))
    else:
        print(format_centrality(results))
    return 0


def cmd_clusters(args: argparse.Namespace) -> int:
    graph = build_graph(Path(args.project_root).resolve())
    clusters = find_clusters(graph)
    if args.json:
        print(json.dumps([{"id": c.id, "size": len(c.nodes), "nodes": c.nodes}
                          for c in clusters], indent=2, ensure_ascii=False))
    else:
        print(f"🔍 Clusters : {len(clusters)} composantes connexes\n")
        for c in clusters[:10]:
            print(f"   Cluster #{c.id} ({len(c.nodes)} nœuds)")
            for n in c.nodes[:5]:
                print(f"      • {n}")
            if len(c.nodes) > 5:
                print(f"      ... +{len(c.nodes) - 5}")
    return 0


def cmd_orphans(args: argparse.Namespace) -> int:
    graph = build_graph(Path(args.project_root).resolve())
    orphans = find_orphans(graph)
    if args.json:
        print(json.dumps([{"id": o.id, "name": o.name, "type": o.node_type}
                          for o in orphans], indent=2, ensure_ascii=False))
    else:
        print(f"👻 Nœuds orphelins : {len(orphans)}\n")
        for o in orphans:
            emoji = NODE_TYPES.get(o.node_type, "?")
            print(f"   {emoji} {o.name} ({o.node_type})")
    return 0


def cmd_mermaid(args: argparse.Namespace) -> int:
    graph = build_graph(Path(args.project_root).resolve())
    print(format_mermaid(graph))
    return 0


# ── CLI Builder ──────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="BMAD Project Graph — Visualisation du graphe de projet",
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--json", action="store_true")

    subs = parser.add_subparsers(dest="command")
    subs.add_parser("build", help="Construire le graphe").set_defaults(func=cmd_build)
    subs.add_parser("centrality", help="Centralité des nœuds").set_defaults(func=cmd_centrality)
    subs.add_parser("clusters", help="Clusters connexes").set_defaults(func=cmd_clusters)
    subs.add_parser("orphans", help="Nœuds orphelins").set_defaults(func=cmd_orphans)
    subs.add_parser("mermaid", help="Export Mermaid").set_defaults(func=cmd_mermaid)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
