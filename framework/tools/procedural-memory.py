#!/usr/bin/env python3
"""
procedural-memory.py — Procedural Memory Layer 4 for Grimoire agents (D10).
═══════════════════════════════════════════════════════════════════

Index des patterns par type de tâche. Permet aux agents de retrouver
les approches qui ont fonctionné pour des tâches similaires.

Stockage : _grimoire/_memory/procedural/
  - patterns.json — Index des patterns
  - Chaque pattern : {task_type, pattern, success_count, last_used, tags}

Modes :
  record  — Enregistrer un nouveau pattern
  lookup  — Chercher des patterns pour un type de tâche
  list    — Lister tous les patterns indexés
  stats   — Statistiques sur les patterns

MCP interface :
  mcp_procedural_lookup(task_type, tags) → list[patterns]
  mcp_procedural_record(task_type, pattern, tags) → {ok}

Usage :
  python3 procedural-memory.py --project-root . record --task "bug-fix" --pattern "1. reproduce 2. isolate 3. fix 4. test"
  python3 procedural-memory.py --project-root . lookup --task "bug-fix"
  python3 procedural-memory.py --project-root . list
  python3 procedural-memory.py --project-root . stats

Stdlib only — aucune dépendance externe.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROCEDURAL_MEMORY_VERSION = "1.0.0"

# ── Storage ──────────────────────────────────────────────────────

def _patterns_file(project_root: Path) -> Path:
    return project_root / "_grimoire" / "_memory" / "procedural" / "patterns.json"


def _load_patterns(project_root: Path) -> list[dict[str, Any]]:
    pf = _patterns_file(project_root)
    if not pf.exists():
        return []
    try:
        return json.loads(pf.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save_patterns(project_root: Path, patterns: list[dict[str, Any]]) -> None:
    pf = _patterns_file(project_root)
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(json.dumps(patterns, indent=2, ensure_ascii=False) + "\n",
                  encoding="utf-8")


# ── Core Operations ──────────────────────────────────────────────

def record_pattern(project_root: Path, task_type: str, pattern: str,
                   tags: list[str] | None = None,
                   source: str = "manual") -> dict[str, Any]:
    """Enregistre un nouveau pattern ou incrémente si existant."""
    patterns = _load_patterns(project_root)
    tags = tags or []
    now = datetime.now(UTC).isoformat()

    # Check if similar pattern already exists
    for p in patterns:
        if p["task_type"] == task_type and p["pattern"] == pattern:
            p["success_count"] += 1
            p["last_used"] = now
            # Merge tags
            existing_tags = set(p.get("tags", []))
            existing_tags.update(tags)
            p["tags"] = sorted(existing_tags)
            _save_patterns(project_root, patterns)
            return {"status": "incremented", "success_count": p["success_count"]}

    entry = {
        "id": len(patterns) + 1,
        "task_type": task_type,
        "pattern": pattern,
        "tags": sorted(set(tags)),
        "success_count": 1,
        "source": source,
        "created": now,
        "last_used": now,
    }
    patterns.append(entry)
    _save_patterns(project_root, patterns)
    return {"status": "created", "id": entry["id"]}


def lookup_patterns(project_root: Path, task_type: str,
                    tags: list[str] | None = None,
                    limit: int = 10) -> list[dict[str, Any]]:
    """Cherche des patterns pour un type de tâche."""
    patterns = _load_patterns(project_root)
    tags = tags or []

    matches = []
    for p in patterns:
        # Match by task_type (exact or substring)
        if task_type.lower() not in p["task_type"].lower():
            continue
        # If tags specified, require at least one match
        if tags and not set(tags) & set(p.get("tags", [])):
            continue
        matches.append(p)

    # Sort by success_count desc, then last_used desc
    matches.sort(key=lambda x: (x["success_count"], x["last_used"]), reverse=True)
    return matches[:limit]


def get_stats(project_root: Path) -> dict[str, Any]:
    """Statistiques sur les patterns stockés."""
    patterns = _load_patterns(project_root)

    if not patterns:
        return {"total": 0, "task_types": {}, "top_tags": {}}

    task_types: dict[str, int] = {}
    all_tags: dict[str, int] = {}

    for p in patterns:
        tt = p["task_type"]
        task_types[tt] = task_types.get(tt, 0) + 1
        for tag in p.get("tags", []):
            all_tags[tag] = all_tags.get(tag, 0) + 1

    total_uses = sum(p["success_count"] for p in patterns)

    return {
        "total": len(patterns),
        "total_uses": total_uses,
        "task_types": dict(sorted(task_types.items(), key=lambda x: x[1], reverse=True)),
        "top_tags": dict(sorted(all_tags.items(), key=lambda x: x[1], reverse=True)[:10]),
    }


# ── MCP Interface ────────────────────────────────────────────────

def mcp_procedural_lookup(task_type: str, tags: str = "",
                          project_root: str = ".") -> dict[str, Any]:
    """MCP tool: cherche des patterns procéduraux.

    Args:
        task_type: Type de tâche (ex: bug-fix, refactor, feature).
        tags: Tags séparés par des virgules.
        project_root: Racine du projet.

    Returns:
        {patterns: [...], count: N}
    """
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    results = lookup_patterns(Path(project_root), task_type, tag_list)
    return {"patterns": results, "count": len(results), "task_type": task_type}


def mcp_procedural_record(task_type: str, pattern: str,
                          tags: str = "", project_root: str = ".") -> dict[str, Any]:
    """MCP tool: enregistre un pattern procédural.

    Args:
        task_type: Type de tâche.
        pattern: Description du pattern/approach.
        tags: Tags séparés par des virgules.
        project_root: Racine du projet.

    Returns:
        {status, id_or_count}
    """
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    return record_pattern(Path(project_root), task_type, pattern, tag_list, source="mcp")


# ── Commands ─────────────────────────────────────────────────────

def cmd_record(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    tags = [t.strip() for t in args.tags.split(",")] if args.tags else []
    result = record_pattern(project_root, args.task, args.pattern, tags)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        if result["status"] == "created":
            print(f"  ✅ Pattern #{result['id']} created for task '{args.task}'")
        else:
            print(f"  ✅ Pattern incremented (count: {result['success_count']})")
    return 0


def cmd_lookup(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    tags = [t.strip() for t in args.tags.split(",")] if args.tags else []
    results = lookup_patterns(project_root, args.task, tags)

    if args.json:
        print(json.dumps({"results": results}, indent=2, ensure_ascii=False))
    else:
        print(f"\n  🔍 Patterns for '{args.task}' ({len(results)} found)\n")
        if not results:
            print("  No patterns found.")
        for p in results:
            tags_str = f" [{', '.join(p['tags'])}]" if p.get("tags") else ""
            print(f"  #{p['id']} (×{p['success_count']}) {p['task_type']}{tags_str}")
            print(f"     {p['pattern'][:120]}")
            print()
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    patterns = _load_patterns(project_root)

    if args.json:
        print(json.dumps({"patterns": patterns}, indent=2, ensure_ascii=False))
    else:
        print(f"\n  📚 Procedural Memory — {len(patterns)} pattern(s)\n")
        for p in patterns:
            tags_str = f" [{', '.join(p['tags'])}]" if p.get("tags") else ""
            print(f"  #{p['id']} {p['task_type']}{tags_str} (×{p['success_count']})")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    stats = get_stats(project_root)

    if args.json:
        print(json.dumps(stats, indent=2, ensure_ascii=False))
    else:
        print("\n  📊 Procedural Memory Stats\n")
        print(f"  Total patterns: {stats['total']}")
        print(f"  Total uses:     {stats.get('total_uses', 0)}")
        if stats["task_types"]:
            print("\n  Task Types:")
            for tt, count in stats["task_types"].items():
                print(f"    {tt}: {count}")
        if stats["top_tags"]:
            print("\n  Top Tags:")
            for tag, count in stats["top_tags"].items():
                print(f"    {tag}: {count}")
    return 0


# ── Main ─────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Grimoire Procedural Memory")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--json", action="store_true")

    subs = parser.add_subparsers(dest="command")

    p_rec = subs.add_parser("record", help="Record a pattern")
    p_rec.add_argument("--task", required=True, help="Task type")
    p_rec.add_argument("--pattern", required=True, help="Pattern description")
    p_rec.add_argument("--tags", default="", help="Comma-separated tags")
    p_rec.set_defaults(func=cmd_record)

    p_look = subs.add_parser("lookup", help="Lookup patterns")
    p_look.add_argument("--task", required=True, help="Task type to search")
    p_look.add_argument("--tags", default="", help="Filter by tags")
    p_look.set_defaults(func=cmd_lookup)

    p_list = subs.add_parser("list", help="List all patterns")
    p_list.set_defaults(func=cmd_list)

    p_stats = subs.add_parser("stats", help="Show statistics")
    p_stats.set_defaults(func=cmd_stats)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
