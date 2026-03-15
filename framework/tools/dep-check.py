#!/usr/bin/env python3
"""
dep-check.py — Dependency graph & validation for Grimoire tools.
═══════════════════════════════════════════════════════════════════

Analyse les dépendances inter-outils en scannant :
  - Les imports dynamiques (_load_tool, importlib)
  - Les imports statiques (from / import)
  - Les appels subprocess à d'autres outils

Modes :
  graph   — Affiche le graphe de dépendances
  check   — Valide que toutes les dépendances existent
  orphans — Liste les outils sans dépendant (feuilles)
  cycles  — Détecte les cycles de dépendances

Usage :
  python3 dep-check.py --project-root . graph
  python3 dep-check.py --project-root . check
  python3 dep-check.py --project-root . orphans
  python3 dep-check.py --project-root . cycles

Stdlib only — aucune dépendance externe.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

DEP_CHECK_VERSION = "1.0.0"


# ── Dependency Scanner ───────────────────────────────────────────

# Pattern for _load_tool("tool-name") or _load_tool('tool-name')
_LOAD_TOOL_RE = re.compile(r"""_load_tool\(\s*["']([a-zA-Z0-9_-]+)["']\s*\)""")

# Pattern for spec_from_file_location("name", .../tool-name.py)
_SPEC_RE = re.compile(r"""spec_from_file_location\(\s*["'][^"']+["'],\s*[^)]*["']([a-zA-Z0-9_-]+)\.py["']""")

# Pattern for from rnd_core import / from rnd_harvest import etc (local modules)
_LOCAL_IMPORT_RE = re.compile(r"""^from\s+(rnd_\w+)\s+import""", re.MULTILINE)

# Pattern for subprocess calls to other tools
_SUBPROCESS_TOOL_RE = re.compile(r"""["'](?:framework/tools/)?([a-zA-Z0-9_-]+)\.py["']""")


def _strip_docstrings(source: str) -> str:
    """Remove triple-quoted strings and comments to avoid false positives."""
    # Remove triple-quoted strings
    source = re.sub(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'', '""', source)
    # Remove line comments
    source = re.sub(r'#[^\n]*', '', source)
    return source


def scan_dependencies(project_root: Path) -> dict[str, list[str]]:
    """Scan tous les outils et retourne {tool: [dépendances]}."""
    tools_dir = project_root / "framework" / "tools"
    if not tools_dir.exists():
        return {}

    graph: dict[str, list[str]] = {}
    tool_files = sorted(tools_dir.glob("*.py"))
    tool_names = {f.stem for f in tool_files}

    for tool_file in tool_files:
        name = tool_file.stem
        try:
            content = _strip_docstrings(
                tool_file.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue

        deps: set[str] = set()

        # 1. _load_tool() calls
        for match in _LOAD_TOOL_RE.finditer(content):
            dep = match.group(1)
            if dep != name:
                deps.add(dep)

        # 2. Local module imports (rnd_core, rnd_harvest, etc.)
        for match in _LOCAL_IMPORT_RE.finditer(content):
            dep = match.group(1)
            if dep != name and dep in tool_names:
                deps.add(dep)

        # 3. spec_from_file_location references
        for match in _SPEC_RE.finditer(content):
            dep = match.group(1)
            if dep != name and dep in tool_names:
                deps.add(dep)

        graph[name] = sorted(deps)

    return graph


def check_missing(graph: dict[str, list[str]],
                  tool_names: set[str]) -> list[dict[str, str]]:
    """Vérifie que toutes les dépendances référencées existent."""
    issues: list[dict[str, str]] = []
    for tool, deps in graph.items():
        for dep in deps:
            if dep not in tool_names:
                issues.append({
                    "tool": tool,
                    "missing_dep": dep,
                    "severity": "WARNING",
                    "message": f"{tool} references '{dep}' but {dep}.py not found",
                })
    return issues


def find_orphans(graph: dict[str, list[str]]) -> list[str]:
    """Trouve les outils qui ne sont jamais référencés par d'autres."""
    all_deps: set[str] = set()
    for deps in graph.values():
        all_deps.update(deps)

    return sorted(t for t in graph if t not in all_deps)


def find_cycles(graph: dict[str, list[str]]) -> list[list[str]]:
    """Détecte les cycles dans le graphe de dépendances (DFS)."""
    WHITE, GRAY, BLACK = 0, 1, 2  # noqa: N806
    color: dict[str, int] = dict.fromkeys(graph, WHITE)
    cycles: list[list[str]] = []
    path: list[str] = []

    def dfs(node: str) -> None:
        color[node] = GRAY
        path.append(node)
        for dep in graph.get(node, []):
            if dep not in color:
                continue
            if color[dep] == GRAY:
                # Found cycle — extract it
                idx = path.index(dep)
                cycles.append([*path[idx:], dep])
            elif color[dep] == WHITE:
                dfs(dep)
        path.pop()
        color[node] = BLACK

    for node in graph:
        if color[node] == WHITE:
            dfs(node)

    return cycles


# ── Display ──────────────────────────────────────────────────────

def _format_graph(graph: dict[str, list[str]]) -> str:
    """Affiche le graphe en texte."""
    lines: list[str] = []
    lines.append(f"Dependency Graph — {len(graph)} tools\n")

    # Sort by number of deps (most connected first)
    sorted_tools = sorted(graph.items(), key=lambda x: len(x[1]), reverse=True)

    for tool, deps in sorted_tools:
        if deps:
            dep_str = ", ".join(deps)
            lines.append(f"  {tool} → {dep_str}")
        else:
            lines.append(f"  {tool} (standalone)")

    # Summary
    total_edges = sum(len(d) for d in graph.values())
    connected = sum(1 for d in graph.values() if d)
    lines.append(f"\n  Edges: {total_edges} | Connected: {connected}/{len(graph)}")

    return "\n".join(lines)


def _format_mermaid(graph: dict[str, list[str]]) -> str:
    """Génère un diagramme Mermaid du graphe."""
    lines = ["graph LR"]
    for tool, deps in sorted(graph.items()):
        for dep in deps:
            lines.append(f"    {tool.replace('-', '_')} --> {dep.replace('-', '_')}")
    return "\n".join(lines)


# ── Commands ─────────────────────────────────────────────────────

def cmd_graph(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    graph = scan_dependencies(project_root)

    if args.json:
        output: dict[str, Any] = {"graph": graph}
        if args.mermaid:
            output["mermaid"] = _format_mermaid(graph)
        print(json.dumps(output, indent=2, ensure_ascii=False))
    elif args.mermaid:
        print(_format_mermaid(graph))
    else:
        print(_format_graph(graph))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    tools_dir = project_root / "framework" / "tools"
    tool_names = {f.stem for f in tools_dir.glob("*.py")} if tools_dir.exists() else set()

    graph = scan_dependencies(project_root)
    issues = check_missing(graph, tool_names)

    if args.json:
        print(json.dumps({"issues": issues, "ok": len(issues) == 0},
                          indent=2, ensure_ascii=False))
    else:
        if not issues:
            print("  All dependencies resolved.")
        else:
            print(f"  {len(issues)} issue(s):\n")
            for iss in issues:
                print(f"  [{iss['severity']}] {iss['message']}")
    return 1 if issues else 0


def cmd_orphans(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    graph = scan_dependencies(project_root)
    orphans = find_orphans(graph)

    if args.json:
        print(json.dumps({"orphans": orphans}, indent=2, ensure_ascii=False))
    else:
        print(f"  {len(orphans)} orphan tool(s) (not depended on by any other):\n")
        for o in orphans:
            print(f"    • {o}")
    return 0


def cmd_cycles(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    graph = scan_dependencies(project_root)
    cycles = find_cycles(graph)

    if args.json:
        print(json.dumps({"cycles": cycles, "has_cycles": len(cycles) > 0},
                          indent=2, ensure_ascii=False))
    else:
        if not cycles:
            print("  No dependency cycles detected.")
        else:
            print(f"  {len(cycles)} cycle(s) detected:\n")
            for cyc in cycles:
                print(f"    {' → '.join(cyc)}")
    return 1 if cycles else 0


# ── Main ─────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Grimoire Tool Dependency Checker")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--json", action="store_true")

    subs = parser.add_subparsers(dest="command")

    p_graph = subs.add_parser("graph", help="Show dependency graph")
    p_graph.add_argument("--mermaid", action="store_true",
                         help="Output as Mermaid diagram")
    p_graph.set_defaults(func=cmd_graph)

    p_check = subs.add_parser("check", help="Validate all dependencies exist")
    p_check.set_defaults(func=cmd_check)

    p_orphans = subs.add_parser("orphans", help="List orphan tools")
    p_orphans.set_defaults(func=cmd_orphans)

    p_cycles = subs.add_parser("cycles", help="Detect dependency cycles")
    p_cycles.set_defaults(func=cmd_cycles)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
