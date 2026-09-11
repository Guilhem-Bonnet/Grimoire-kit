"""Evidence-gate lookup shared by the three closure-adjacent decisions.

Used by :mod:`.evidence_gate` (``Stop``), :mod:`.subagent_gate`
(``SubagentStop``) and :mod:`.context_capsule` (``PreCompact``) — never by
:mod:`.tool_policy` or :mod:`.evidence_trace``, which run on every tool call
and must not pay for the standard engine this function reaches into.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _gate_summary(project_root: Path, task_id: str) -> tuple[bool, str, dict[str, Any]]:
    # Imported here, not at module scope: evaluating gates needs the standard
    # engine, but deciding a tool call does not. The tool-policy decision runs
    # on every action of every session, and paying 48 ms to import a module it
    # never calls is the difference between a guardrail and a tax.
    from grimoire.core.agentic_standard import check_evidence_gates

    result = check_evidence_gates(project_root, task_id=task_id)
    missing = list(result.missing)
    lines = [f"  - {item}" for item in missing[:6]]
    if len(missing) > 6:
        lines.append(f"  - … {len(missing) - 6} autre(s)")
    summary = "\n".join(lines)
    detail = {
        "task_id": result.task_id,
        "profile": result.profile,
        "state": result.state,
        "ok": result.ok,
        "missing": missing,
    }
    return result.ok, summary, detail
