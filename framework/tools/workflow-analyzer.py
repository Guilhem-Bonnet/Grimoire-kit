#!/usr/bin/env python3
"""workflow-analyzer.py — CLI pour analyser les métriques de workflow/skill.

Lit les données de télémétrie JSONL et produit des insights sur l'utilisation
des skills, les patterns de failure, et les recommandations d'optimisation.

Usage :
    python3 workflow-analyzer.py --project-root . analyze
    python3 workflow-analyzer.py --project-root . analyze --json
    python3 workflow-analyzer.py --project-root . top
    python3 workflow-analyzer.py --project-root . failures
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

WORKFLOW_ANALYZER_CLI_VERSION = "1.0.0"


def _ensure_sdk(project_root: Path) -> None:
    """Add SDK src to path if available."""
    src = project_root / "src"
    if src.exists() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


def cmd_analyze(args: argparse.Namespace) -> None:
    """Run full workflow analysis."""
    root = Path(args.project_root).resolve()
    _ensure_sdk(root)
    from grimoire.core.workflow_analyzer import WorkflowAnalyzer

    analyzer = WorkflowAnalyzer(root)
    report = analyzer.analyze()

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(report.to_markdown())


def cmd_top(args: argparse.Namespace) -> None:
    """Show top skills by invocation count."""
    root = Path(args.project_root).resolve()
    _ensure_sdk(root)
    from grimoire.core.workflow_analyzer import WorkflowAnalyzer

    analyzer = WorkflowAnalyzer(root)
    report = analyzer.analyze()
    limit = args.limit

    sorted_skills = sorted(
        report.skills.items(),
        key=lambda kv: kv[1].invocations,
        reverse=True,
    )[:limit]

    if not sorted_skills:
        print("Aucune donnée de télémétrie.")
        return

    print(f"Top {len(sorted_skills)} skills :\n")
    for name, metrics in sorted_skills:
        rate = f"{metrics.success_rate:.0%}"
        avg = f"{metrics.avg_duration:.1f}s" if metrics.avg_duration else "n/a"
        print(f"  {name:40s}  {metrics.invocations:4d} invocations  {rate:>5s} success  {avg:>7s} avg")


def cmd_failures(args: argparse.Namespace) -> None:
    """Show skills with highest failure rates."""
    root = Path(args.project_root).resolve()
    _ensure_sdk(root)
    from grimoire.core.workflow_analyzer import WorkflowAnalyzer

    analyzer = WorkflowAnalyzer(root)
    report = analyzer.analyze()

    failures = [
        (name, m)
        for name, m in report.skills.items()
        if m.success_rate < 1.0 and m.invocations > 0
    ]
    failures.sort(key=lambda kv: kv[1].success_rate)

    if not failures:
        print("Aucun échec détecté.")
        return

    print("Skills avec échecs :\n")
    for name, metrics in failures:
        rate = f"{metrics.success_rate:.0%}"
        print(f"  {name:40s}  {metrics.invocations:4d} invocations  {rate:>5s} success")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Grimoire Workflow Analyzer CLI",
    )
    parser.add_argument(
        "--project-root",
        default=".",
        help="Project root directory",
    )
    sub = parser.add_subparsers(dest="command")

    analyze_p = sub.add_parser("analyze", help="Full workflow analysis")
    analyze_p.add_argument("--json", action="store_true", help="Output as JSON")

    top_p = sub.add_parser("top", help="Top skills by usage")
    top_p.add_argument("--limit", type=int, default=10, help="Number of results")

    sub.add_parser("failures", help="Skills with failures")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "analyze": cmd_analyze,
        "top": cmd_top,
        "failures": cmd_failures,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
