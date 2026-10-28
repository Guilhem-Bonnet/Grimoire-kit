"""Sources runtime de l'observatoire : TraceLedger et journal d'événements."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from grimoire.traces.observatory_feed import load_journal_events, load_ledger_traces

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "traces_ledgers" / "01_dispatch_only.jsonl"
OBSERVATORY = Path(__file__).resolve().parents[2] / "framework" / "tools" / "observatory.py"


def _write_traces(root: Path, count: int) -> None:
    base = json.loads(FIXTURE.read_text(encoding="utf-8").splitlines()[0])
    traces = root / "_grimoire-output" / "traces"
    traces.mkdir(parents=True)
    lines = [json.dumps({**base, "id": f"TRC-{i}"}) for i in range(count)]
    (traces / "traces.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_journal(root: Path) -> None:
    events = root / "_grimoire-output" / "events"
    events.mkdir(parents=True)
    rows = [
        {"event_type": "gate.checked", "task_id": "t1", "timestamp": "2026-01-01T00:00:00+00:00", "details": {"ok": True}},
        {"event_type": "gate.checked", "task_id": "t2", "timestamp": "2026-01-02T00:00:00+00:00"},
    ]
    (events / "runtime-journal.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\nnot json\n", encoding="utf-8"
    )


def test_three_ledger_lines_give_three_traces(tmp_path: Path) -> None:
    _write_traces(tmp_path, 3)
    traces = load_ledger_traces(tmp_path)
    assert len(traces) == 3
    assert traces[0]["agent"] == "concierge"


def test_missing_sources_are_empty(tmp_path: Path) -> None:
    assert load_ledger_traces(tmp_path) == []
    assert load_journal_events(tmp_path) == []


def test_journal_skips_malformed_lines(tmp_path: Path) -> None:
    _write_journal(tmp_path)
    events = load_journal_events(tmp_path)
    assert [e["correlation_id"] for e in events] == ["t1", "t2"]
    assert events[0]["payload"]["ok"] is True


def test_observatory_export_reads_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_traces(tmp_path, 3)
    _write_journal(tmp_path)
    spec = importlib.util.spec_from_file_location("observatory_under_test", OBSERVATORY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)  # les dataclasses l'exigent
    spec.loader.exec_module(module)
    exported = json.loads(module.data_to_json(module.load_all(tmp_path)))
    assert len(exported["traces"]) == 3
    assert len(exported["events"]) == 2
