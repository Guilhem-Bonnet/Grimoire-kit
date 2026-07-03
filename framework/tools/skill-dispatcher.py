#!/usr/bin/env python3
"""skill-dispatcher — CLI pour invoquer des skills avec injection de contexte.

Usage :
    python3 skill-dispatcher.py --project-root . list
    python3 skill-dispatcher.py --project-root . prepare grimoire-tdd
    python3 skill-dispatcher.py --project-root . prepare grimoire-tdd --no-preamble
    python3 skill-dispatcher.py --project-root . complete grimoire-tdd --outcome success --duration 12.5
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SKILL_DISPATCHER_CLI_VERSION = "1.0.0"


def _ensure_sdk(project_root: Path) -> None:
    """Add SDK src to path if available."""
    src = project_root / "src"
    if src.exists() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


def cmd_list(args: argparse.Namespace) -> None:
    """List all available skills."""
    root = Path(args.project_root).resolve()
    _ensure_sdk(root)
    from grimoire.core.skill_dispatcher import SkillDispatcher

    dispatcher = SkillDispatcher(root)
    skills = dispatcher.list_skills()
    if not skills:
        print("Aucune skill trouvée.")
        return
    print(f"{len(skills)} skill(s) disponible(s) :\n")
    for sk in skills:
        print(f"  - {sk}")


def cmd_prepare(args: argparse.Namespace) -> None:
    """Prepare a skill with optional preamble injection."""
    root = Path(args.project_root).resolve()
    _ensure_sdk(root)
    from grimoire.core.skill_dispatcher import SkillDispatcher

    dispatcher = SkillDispatcher(root)
    content, inv = dispatcher.prepare(
        args.skill,
        inject_preamble=not args.no_preamble,
        resolve_templates=not args.no_templates,
    )

    if not inv.found:
        print(f"Skill '{args.skill}' non trouvée.", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps({
            "skill": inv.skill,
            "path": str(inv.path),
            "preamble_injected": inv.preamble_injected,
            "template_resolved": inv.template_resolved,
            "content_length": inv.content_length,
            "timestamp": inv.timestamp,
        }, indent=2, ensure_ascii=False))
    else:
        print(content)


def cmd_complete(args: argparse.Namespace) -> None:
    """Record skill completion telemetry."""
    root = Path(args.project_root).resolve()
    _ensure_sdk(root)
    from grimoire.core.skill_dispatcher import SkillDispatcher

    dispatcher = SkillDispatcher(root)
    dispatcher.complete(
        args.skill,
        outcome=args.outcome,
        duration_s=args.duration,
        message=args.message or "",
    )
    print(f"Telemetry enregistrée : {args.skill} → {args.outcome}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="CLI pour le Skill Dispatcher Grimoire",
    )
    parser.add_argument("--project-root", default=".", help="Racine du projet")
    parser.add_argument("--version", action="version", version=f"%(prog)s {SKILL_DISPATCHER_CLI_VERSION}")

    sub = parser.add_subparsers(dest="command", required=True)

    # list
    sub.add_parser("list", help="Lister les skills disponibles")

    # prepare
    p_prepare = sub.add_parser("prepare", help="Préparer une skill avec injection")
    p_prepare.add_argument("skill", help="Nom de la skill")
    p_prepare.add_argument("--no-preamble", action="store_true", help="Désactiver l'injection de preamble")
    p_prepare.add_argument("--no-templates", action="store_true", help="Désactiver la résolution de templates")
    p_prepare.add_argument("--json", action="store_true", help="Output metadata en JSON")

    # complete
    p_complete = sub.add_parser("complete", help="Enregistrer la complétion d'une skill")
    p_complete.add_argument("skill", help="Nom de la skill")
    p_complete.add_argument("--outcome", default="success", choices=["success", "failure", "skipped", "timeout"])
    p_complete.add_argument("--duration", type=float, default=0.0, help="Durée en secondes")
    p_complete.add_argument("--message", help="Message descriptif")

    args = parser.parse_args()

    handlers = {
        "list": cmd_list,
        "prepare": cmd_prepare,
        "complete": cmd_complete,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
