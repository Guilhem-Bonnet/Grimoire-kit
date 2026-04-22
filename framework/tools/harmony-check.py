#!/usr/bin/env python3
"""harmony-check.py — Thin CLI shim over ``grimoire.tools.harmony_check``.

Historical location for the Architecture Harmony Check tool. The real
implementation now lives in ``grimoire-kit/src/grimoire/tools/harmony_check.py``
as the canonical :class:`HarmonyCheck` ``GrimoireTool``.

This shim preserves the legacy CLI surface used by ``tests/test_harmony_check.py``
and external scripts:

  - Subcommands : ``scan``, ``check``, ``dissonance``, ``score``, ``report``
  - Flags      : ``--project-root``, ``--json``
  - Callables  : ``build_parser``, ``full_analysis``, ``format_report``, ``main``

All detection logic is delegated — no duplication. Keeping the same
exit-codes as before (0 on success).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Ensure the kit src/ is importable when invoked as a bare script.
_HERE = Path(__file__).resolve()
_KIT_ROOT = _HERE.parent.parent.parent  # grimoire-kit/
_SRC = _KIT_ROOT / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from grimoire.tools.harmony_check import (  # noqa: E402
    HarmonyCheck,
    _compute_score,
    _detect_broken_refs,
    _detect_duplication,
    _detect_manifest_mismatch,
    _detect_naming,
    _detect_orphans,
    _detect_oversized,
    _scan_project,
)

VERSION = "2.0.0"


# ── CLI parser ────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    """Build the legacy CLI parser (kept stable for external callers)."""
    parser = argparse.ArgumentParser(
        prog="harmony-check",
        description="Grimoire Architecture Harmony Check (shim)",
    )
    parser.add_argument("--project-root", default=".", help="Project root directory")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    parser.add_argument(
        "command",
        choices=("scan", "check", "dissonance", "score", "report"),
        help="Subcommand to run",
    )
    return parser


# ── Analysis entrypoints ──────────────────────────────────────────────────────

def full_analysis(project_root: Path) -> dict[str, Any]:
    """Run the full harmony analysis and return a legacy-shaped payload."""
    scan = _scan_project(project_root)
    scan.dissonances.extend(_detect_orphans(scan))
    scan.dissonances.extend(_detect_naming(scan))
    scan.dissonances.extend(_detect_oversized(scan, project_root))
    scan.dissonances.extend(_detect_manifest_mismatch(scan, project_root))
    scan.dissonances.extend(_detect_broken_refs(scan, project_root))
    scan.dissonances.extend(_detect_duplication(scan, project_root))
    score, grade, cats = _compute_score(scan.dissonances, scan.total_files)
    return {
        "score": score,
        "grade": grade,
        "total_files": scan.total_files,
        "agents": len(scan.agents),
        "workflows": len(scan.workflows),
        "tools": len(scan.tools),
        "category_counts": cats,
        "dissonances": [
            {
                "category": d.category,
                "severity": d.severity,
                "file": d.file,
                "message": d.message,
                "suggestion": d.suggestion,
            }
            for d in scan.dissonances
        ],
    }


def format_report(payload: dict[str, Any]) -> str:
    """Render a human-readable report from a ``full_analysis`` payload."""
    lines: list[str] = []
    lines.append(f"Score: {payload['score']}/100 ({payload['grade']})")
    lines.append(f"Files scanned: {payload['total_files']}")
    lines.append(
        f"Agents: {payload['agents']} | Workflows: {payload['workflows']} | Tools: {payload['tools']}"
    )
    diss = payload["dissonances"]
    lines.append(f"Dissonances: {len(diss)}")
    for d in diss:
        icon = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🔵"}.get(d["severity"], "⚪")
        lines.append(f"  {icon} [{d['category']}] {d['file']}: {d['message']}")
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    project_root = Path(args.project_root).resolve()

    if args.command == "scan":
        scan = _scan_project(project_root)
        payload = {
            "agents": len(scan.agents),
            "workflows": len(scan.workflows),
            "tools": len(scan.tools),
            "total_files": scan.total_files,
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(format_report({**payload, "score": 0, "grade": "-", "dissonances": []}))
        return 0

    # check / dissonance / score / report all use the full analysis.
    payload = full_analysis(project_root)

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    if args.command == "score":
        print(f"Score: {payload['score']}/100 ({payload['grade']})")
    elif args.command == "dissonance":
        for d in payload["dissonances"]:
            icon = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🔵"}.get(d["severity"], "⚪")
            print(f"{icon} [{d['category']}] {d['file']}: {d['message']}")
    else:  # check / report
        print(format_report(payload))
    return 0


# Export the real tool class for programmatic consumers.
__all__ = [
    "VERSION",
    "HarmonyCheck",
    "build_parser",
    "format_report",
    "full_analysis",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
