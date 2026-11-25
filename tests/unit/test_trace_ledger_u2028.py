"""#1277 — U+2028 non échappé dans une ligne JSONL ne doit pas la couper à la relecture."""

from __future__ import annotations

from typing import TYPE_CHECKING

from grimoire.traces.ledger import TraceLedger, TraceOutcome

if TYPE_CHECKING:
    from pathlib import Path


def _record(ledger: TraceLedger, run_id: str):
    return ledger.record(
        run_id=run_id,
        workflow_instance_id="WFI-1",
        mission_id="MIS-1",
        task_id="GAO-1",
        recipe_id="recipe.test",
        outcome=TraceOutcome.SUCCESS,
        started_at="2026-01-01T00:00:00+00:00",
    )


def test_trace_ledger_run_id_with_u2028_recorded_twice(tmp_path: Path) -> None:
    ledger = TraceLedger(tmp_path)
    run_id = "RUN-a b"
    first = _record(ledger, run_id)
    second = _record(ledger, run_id)
    assert first.id != second.id
    fresh = TraceLedger(tmp_path)
    assert {t.id for t in fresh.list_traces()} == {first.id, second.id}
    assert fresh.append_new([first, second]) == []  # _known_ids voit les deux
