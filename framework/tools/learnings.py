#!/usr/bin/env python3
"""
learnings.py — Operational learnings management CLI.
=================================================

Log, search, and manage operational learnings stored in JSONL format.
Learnings are cross-session knowledge that auto-injects into future
sessions via the preamble system.

Usage :
  python3 learnings.py --project-root . log --key "pytest-xdist" --insight "Never use -n auto"
  python3 learnings.py --project-root . search "pytest"
  python3 learnings.py --project-root . top
  python3 learnings.py --project-root . top --limit 10
  python3 learnings.py --project-root . count
  python3 learnings.py --project-root . prune
  python3 learnings.py --project-root . inject

Stdlib + grimoire SDK.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ── Version ──────────────────────────────────────────────────────────────────

LEARNINGS_CLI_VERSION = "1.0.0"


def _get_tool(project_root: Path):
    """Import and instantiate the Learnings tool."""
    src = project_root / "src"
    if src.exists() and str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from grimoire.tools.learnings import Learnings

    return Learnings(project_root=project_root)


def cmd_log(args: argparse.Namespace) -> int:
    tool = _get_tool(args.project_root)
    entry = tool.log(
        args.key,
        args.insight,
        confidence=args.confidence,
        source=args.source,
        skill=args.skill or "",
        tags=tuple(args.tags.split(",")) if args.tags else (),
    )
    print(f"✅ Learning logged: [{entry.key}] {entry.insight} (conf={entry.confidence}%)")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    tool = _get_tool(args.project_root)
    results = tool.search(args.query, limit=args.limit)
    if not results:
        print("No learnings found.")
        return 0
    for entry in results:
        print(f"  [{entry.confidence:3d}%] {entry.key}: {entry.insight}")
        if entry.tags:
            print(f"         tags: {', '.join(entry.tags)}")
    return 0


def cmd_top(args: argparse.Namespace) -> int:
    tool = _get_tool(args.project_root)
    results = tool.top(limit=args.limit)
    if not results:
        print("No learnings stored yet.")
        return 0
    print(f"Top {len(results)} learnings:")
    for entry in results:
        skill_tag = f" [{entry.skill}]" if entry.skill else ""
        print(f"  [{entry.confidence:3d}%]{skill_tag} {entry.key}: {entry.insight}")
    return 0


def cmd_count(args: argparse.Namespace) -> int:
    tool = _get_tool(args.project_root)
    print(f"Total learnings: {tool.count()}")
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    tool = _get_tool(args.project_root)
    removed = tool.prune()
    if removed:
        print(f"Pruned {removed} low-confidence entries.")
    else:
        print("Nothing to prune — within limits.")
    return 0


def cmd_inject(args: argparse.Namespace) -> int:
    tool = _get_tool(args.project_root)
    ctx = tool.inject_context(limit=args.limit)
    if not ctx:
        print("No learnings to inject.")
        return 0
    if args.json_output:
        entries = tool.top(limit=args.limit)
        print(json.dumps([e.to_dict() for e in entries], indent=2, ensure_ascii=False))
    else:
        print(ctx)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="learnings",
        description="Gestion des learnings opérationnels Grimoire",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(),
        help="Racine du projet",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {LEARNINGS_CLI_VERSION}",
    )

    subs = parser.add_subparsers(dest="command", help="Commande")

    # log
    log_p = subs.add_parser("log", help="Enregistrer un learning")
    log_p.add_argument("--key", required=True, help="Clé unique du learning")
    log_p.add_argument("--insight", required=True, help="Description du learning")
    log_p.add_argument("--confidence", type=int, default=80, help="Confiance 0-100")
    log_p.add_argument("--source", default="observed", help="Source: observed, documented, inferred")
    log_p.add_argument("--skill", default="", help="Skill associée")
    log_p.add_argument("--tags", default="", help="Tags séparés par virgule")

    # search
    search_p = subs.add_parser("search", help="Chercher dans les learnings")
    search_p.add_argument("query", help="Terme de recherche")
    search_p.add_argument("--limit", type=int, default=5)

    # top
    top_p = subs.add_parser("top", help="Afficher les top learnings")
    top_p.add_argument("--limit", type=int, default=5)

    # count
    subs.add_parser("count", help="Nombre total de learnings")

    # prune
    subs.add_parser("prune", help="Nettoyer les learnings en excès")

    # inject
    inject_p = subs.add_parser("inject", help="Générer le bloc d'injection pour LLM")
    inject_p.add_argument("--limit", type=int, default=5)
    inject_p.add_argument("--json", dest="json_output", action="store_true")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    dispatch = {
        "log": cmd_log,
        "search": cmd_search,
        "top": cmd_top,
        "count": cmd_count,
        "prune": cmd_prune,
        "inject": cmd_inject,
    }

    handler = dispatch.get(args.command)
    if handler:
        sys.exit(handler(args))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
