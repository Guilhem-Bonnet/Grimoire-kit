"""``bmad merge`` — merge BMAD files from a source into a project.

Wraps :class:`bmad.core.merge.MergeEngine` with CLI output and
confirmation prompts.
"""

from __future__ import annotations

from pathlib import Path

from bmad.core.merge import MergeEngine, MergePlan, MergeResult


def run_merge(
    source: Path,
    target: Path,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> tuple[MergePlan, MergeResult]:
    """Analyse and execute a merge.

    Returns the plan and result for the CLI to display.
    """
    engine = MergeEngine(source, target)
    plan = engine.analyze()
    result = engine.execute(plan, dry_run=dry_run, force=force)
    return plan, result


def run_undo(target: Path) -> list[str]:
    """Undo the last merge in *target* using its log file.

    Returns the list of deleted file paths.
    """
    log_path = target / ".bmad-merge-log.json"
    return MergeEngine.undo(log_path)
