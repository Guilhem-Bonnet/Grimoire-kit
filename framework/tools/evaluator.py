#!/usr/bin/env python3
"""evaluator.py — CLI pour évaluer des outputs d'agents multi-dimensions.

Utilise le module `grimoire.core.evaluator` pour scorer des outputs selon
completeness, safety, style, relevance, et tests.

Usage :
    python3 evaluator.py --project-root . eval --agent dev --output "def foo(): pass" --task "implement foo"
    python3 evaluator.py --project-root . recent
    python3 evaluator.py --project-root . recent --agent dev --limit 5
    python3 evaluator.py --project-root . scores
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

EVALUATOR_CLI_VERSION = "1.0.0"


def _ensure_sdk(project_root: Path) -> None:
    """Add SDK src to path if available."""
    src = project_root / "src"
    if src.exists() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


def cmd_eval(args: argparse.Namespace) -> None:
    """Evaluate an agent output."""
    root = Path(args.project_root).resolve()
    _ensure_sdk(root)
    from grimoire.core.evaluator import EvalCriteria, Evaluator

    criteria = EvalCriteria(check_tests=args.check_tests)
    ev = Evaluator(root)

    # Read from stdin if output is "-"
    output = sys.stdin.read() if args.output == "-" else args.output

    result = ev.evaluate(
        agent=args.agent,
        output=output,
        task=args.task or "",
        criteria=criteria,
    )

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(f"Agent: {result.agent}")
        print(f"Score: {result.score:.2f} (Grade: {result.grade})")
        print(f"Passed: {'✅' if result.passed else '❌'}")
        print()
        for dim in result.dimensions:
            icon = "✅" if dim.score >= 0.7 else "⚠️" if dim.score >= 0.4 else "❌"
            print(f"  {icon} {dim.dimension:15s} {dim.score:.2f}  {dim.reason}")


def cmd_recent(args: argparse.Namespace) -> None:
    """Show recent evaluations."""
    root = Path(args.project_root).resolve()
    _ensure_sdk(root)
    from grimoire.core.evaluator import Evaluator

    ev = Evaluator(root)
    entries = ev.recent(agent=args.agent or "", limit=args.limit)

    if not entries:
        print("Aucune évaluation enregistrée.")
        return

    if args.json:
        print(json.dumps(entries, indent=2, ensure_ascii=False))
    else:
        for e in entries:
            grade = e.get("grade", "?")
            score = e.get("score", 0)
            agent = e.get("agent", "?")
            ts = e.get("timestamp", "")[:10]
            print(f"  {ts}  {agent:15s}  {score:.2f} ({grade})")


def cmd_scores(args: argparse.Namespace) -> None:
    """Show aggregate scores by agent."""
    root = Path(args.project_root).resolve()
    _ensure_sdk(root)
    from grimoire.core.evaluator import Evaluator

    ev = Evaluator(root)
    scores = ev.agent_scores()

    if not scores:
        print("Aucune donnée d'évaluation.")
        return

    if args.json:
        print(json.dumps(scores, indent=2, ensure_ascii=False))
    else:
        print("Scores par agent :\n")
        for agent, data in sorted(scores.items()):
            avg = data["avg_score"]
            grade = data["grade"]
            count = data["count"]
            print(f"  {agent:15s}  avg={avg:.2f} ({grade})  {count} évaluations")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Grimoire Evaluator CLI",
    )
    parser.add_argument(
        "--project-root",
        default=".",
        help="Project root directory",
    )
    sub = parser.add_subparsers(dest="command")

    eval_p = sub.add_parser("eval", help="Evaluate an agent output")
    eval_p.add_argument("--agent", required=True, help="Agent identifier")
    eval_p.add_argument("--output", required=True, help="Output text (use '-' for stdin)")
    eval_p.add_argument("--task", default="", help="Task description for relevance")
    eval_p.add_argument("--check-tests", action="store_true", help="Check test presence")
    eval_p.add_argument("--json", action="store_true", help="Output as JSON")

    recent_p = sub.add_parser("recent", help="Recent evaluations")
    recent_p.add_argument("--agent", default="", help="Filter by agent")
    recent_p.add_argument("--limit", type=int, default=10, help="Max results")
    recent_p.add_argument("--json", action="store_true", help="Output as JSON")

    scores_p = sub.add_parser("scores", help="Aggregate scores by agent")
    scores_p.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "eval": cmd_eval,
        "recent": cmd_recent,
        "scores": cmd_scores,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
