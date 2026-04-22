#!/usr/bin/env python3
"""memory-lint.py — Thin CLI shim over ``grimoire.tools.memory_lint``.

Historical CLI entrypoint. The canonical implementation now lives in
``grimoire-kit/src/grimoire/tools/memory_lint.py`` as :class:`MemoryLint`.

This shim preserves the legacy surface used by ``tests/test_memory_lint.py``:

  - Callables : ``collect_memory_files``, ``lint_memory``, ``report_to_dict``
  - CLI       : ``--project-root``, ``--json``, ``--emit``, ``--fix``

All detection logic is delegated — no duplication.
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

from grimoire.tools.memory_lint import (  # noqa: E402
    LintReport,
    MemoryLint,
    check_chronological,
    check_contradictions,
    check_duplicates,
    check_freshness,
    check_orphan_decisions,
    collect_memory_files,
)

LINT_VERSION = "2.0.0"


def lint_memory(project_root: Path) -> LintReport:
    """Run the full memory lint and return a :class:`LintReport`."""
    files = collect_memory_files(Path(project_root))
    report = LintReport(
        files_scanned=len(files),
        entries_scanned=sum(len(f.entries) for f in files),
    )
    report.issues.extend(check_contradictions(files))
    report.issues.extend(check_duplicates(files))
    report.issues.extend(check_orphan_decisions(files))
    report.issues.extend(check_chronological(files))
    report.issues.extend(check_freshness(files))
    severity_order = {"error": 0, "warning": 1, "info": 2}
    report.issues.sort(key=lambda i: severity_order.get(i.severity, 9))
    return report


def report_to_dict(report: LintReport) -> dict[str, Any]:
    """Serialize a :class:`LintReport` to a plain dict."""
    return report.to_dict()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="memory-lint",
        description="Memory Lint — cross-file coherence analysis for Grimoire memory",
    )
    parser.add_argument("--project-root", default=".", help="Project root directory")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    parser.add_argument(
        "--emit",
        action="store_true",
        help="(deprecated) formerly emitted stigmergy signals — now a no-op",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Show fix suggestions in human-readable output",
    )
    args = parser.parse_args(argv)

    project_root = Path(args.project_root).resolve()
    report = lint_memory(project_root)

    if args.json:
        print(json.dumps(report_to_dict(report), indent=2, ensure_ascii=False))
        return 0

    print(f"Memory Lint v{LINT_VERSION}")
    print(f"Files scanned: {report.files_scanned} | Entries: {report.entries_scanned}")
    print(
        f"Issues: {len(report.issues)} "
        f"(errors={report.error_count}, warnings={report.warning_count}, info={report.info_count})"
    )
    for issue in report.issues:
        icon = {"error": "🔴", "warning": "🟡", "info": "🔵"}.get(issue.severity, "⚪")
        print(f"  {icon} [{issue.category}] {issue.issue_id} — {issue.title}")
        print(f"      {issue.description}")
        if args.fix and issue.fix_suggestion:
            print(f"      ↳ fix: {issue.fix_suggestion}")
    return 0


__all__ = [
    "LINT_VERSION",
    "LintReport",
    "MemoryLint",
    "collect_memory_files",
    "lint_memory",
    "main",
    "report_to_dict",
]


if __name__ == "__main__":
    raise SystemExit(main())
