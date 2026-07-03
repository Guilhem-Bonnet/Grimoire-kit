#!/usr/bin/env python3
"""ref-validator.py — CLI pour valider les références cross-fichiers Markdown.

Scanne les fichiers .md du projet pour détecter les liens cassés et les
références obsolètes (staleness).

Usage :
    python3 ref-validator.py --project-root . validate
    python3 ref-validator.py --project-root . validate --check-stale
    python3 ref-validator.py --project-root . validate --staleness-days 60
    python3 ref-validator.py --project-root . validate --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REF_VALIDATOR_CLI_VERSION = "1.0.0"


def _ensure_sdk(project_root: Path) -> None:
    """Add SDK src to path if available."""
    src = project_root / "src"
    if src.exists() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


def cmd_validate(args: argparse.Namespace) -> None:
    """Validate cross-references in Markdown files."""
    root = Path(args.project_root).resolve()
    _ensure_sdk(root)
    from grimoire.core.ref_validator import RefValidator

    scan_dirs = [d.strip() for d in args.dirs.split(",")] if args.dirs else None
    validator = RefValidator(
        project_root=root,
        scan_dirs=scan_dirs,
        staleness_days=args.staleness_days,
    )
    report = validator.validate(check_stale=args.check_stale)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(report.to_markdown())

    if report.broken_count > 0:
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Grimoire Ref Validator CLI",
    )
    parser.add_argument(
        "--project-root",
        default=".",
        help="Project root directory",
    )
    sub = parser.add_subparsers(dest="command")

    validate_p = sub.add_parser("validate", help="Validate references")
    validate_p.add_argument(
        "--check-stale",
        action="store_true",
        help="Also check for stale references",
    )
    validate_p.add_argument(
        "--staleness-days",
        type=int,
        default=90,
        help="Days before a reference is considered stale (default: 90)",
    )
    validate_p.add_argument(
        "--dirs",
        default=None,
        help="Comma-separated list of directories to scan (default: docs,_grimoire-runtime)",
    )
    validate_p.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "validate":
        cmd_validate(args)


if __name__ == "__main__":
    main()
