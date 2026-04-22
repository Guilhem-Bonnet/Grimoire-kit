"""Tests for grimoire.tools.events — GrimoireEvent contract + ledger IO."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grimoire.tools.events import (
    SCHEMA_VERSION,
    VALID_PHASES,
    VALID_SCOPES,
    GrimoireEvent,
    GrimoireEventError,
    counters,
    emit_event,
    parse_line,
    read_ledger,
    write_error,
    write_event,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "_grimoire-runtime" / "_memory").mkdir(parents=True)
    (tmp_path / "_grimoire-runtime-output" / "hook-runtime").mkdir(parents=True)
    return tmp_path


@pytest.fixture()
def sample_event() -> GrimoireEvent:
    return GrimoireEvent(
        scope="tool",
        phase="start",
        source_hook="grimoire-post-edit.sh",
        payload={"file": "foo.py"},
    )


# ── GrimoireEvent validation ──────────────────────────────────────────────────

class TestGrimoireEventValidation:
    def test_valid_event(self, sample_event: GrimoireEvent) -> None:
        assert sample_event.scope == "tool"
        assert sample_event.phase == "start"
        assert sample_event.schema_version == SCHEMA_VERSION
        assert sample_event.event_id  # UUID generated
        assert sample_event.ts.endswith("Z")

    def test_invalid_scope(self) -> None:
        with pytest.raises(GrimoireEventError, match="invalid scope"):
            GrimoireEvent(scope="bogus", phase="start", source_hook="x.sh")

    def test_invalid_phase(self) -> None:
        with pytest.raises(GrimoireEventError, match="invalid phase"):
            GrimoireEvent(scope="tool", phase="bogus", source_hook="x.sh")

    def test_empty_source_hook(self) -> None:
        with pytest.raises(GrimoireEventError, match="source_hook"):
            GrimoireEvent(scope="tool", phase="start", source_hook="")

    def test_payload_must_be_dict(self) -> None:
        with pytest.raises(GrimoireEventError, match="payload"):
            GrimoireEvent(
                scope="tool",
                phase="start",
                source_hook="x.sh",
                payload="not a dict",  # type: ignore[arg-type]
            )

    def test_agent_must_be_dict_or_none(self) -> None:
        with pytest.raises(GrimoireEventError, match="agent"):
            GrimoireEvent(
                scope="tool",
                phase="start",
                source_hook="x.sh",
                agent="oops",  # type: ignore[arg-type]
            )

    def test_all_scopes_accepted(self) -> None:
        for scope in VALID_SCOPES:
            ev = GrimoireEvent(scope=scope, phase="info", source_hook="s.sh")
            assert ev.scope == scope

    def test_all_phases_accepted(self) -> None:
        for phase in VALID_PHASES:
            ev = GrimoireEvent(scope="tool", phase=phase, source_hook="s.sh")
            assert ev.phase == phase


# ── Serialization round-trip ──────────────────────────────────────────────────

class TestSerialization:
    def test_to_dict_keys_stable(self, sample_event: GrimoireEvent) -> None:
        data = sample_event.to_dict()
        expected_keys = {
            "schema_version",
            "event_id",
            "ts",
            "scope",
            "phase",
            "source_hook",
            "agent",
            "correlation_id",
            "payload",
        }
        assert set(data.keys()) == expected_keys

    def test_to_json_is_single_line(self, sample_event: GrimoireEvent) -> None:
        assert "\n" not in sample_event.to_json()

    def test_round_trip(self, sample_event: GrimoireEvent) -> None:
        reconstructed = GrimoireEvent.from_dict(json.loads(sample_event.to_json()))
        assert reconstructed.to_dict() == sample_event.to_dict()

    def test_from_dict_rejects_missing_fields(self) -> None:
        with pytest.raises(GrimoireEventError, match="missing required fields"):
            GrimoireEvent.from_dict({"scope": "tool", "phase": "start"})

    def test_from_dict_rejects_non_dict(self) -> None:
        with pytest.raises(GrimoireEventError, match="JSON object"):
            GrimoireEvent.from_dict("oops")  # type: ignore[arg-type]

    def test_from_dict_drops_unknown_fields(self) -> None:
        data = {
            "schema_version": SCHEMA_VERSION,
            "event_id": "e1",
            "ts": "2026-04-21T10:00:00.000Z",
            "scope": "tool",
            "phase": "start",
            "source_hook": "s.sh",
            "extra_future_field": "ignored",
        }
        ev = GrimoireEvent.from_dict(data)
        assert ev.event_id == "e1"

    def test_parse_line(self, sample_event: GrimoireEvent) -> None:
        parsed = parse_line(sample_event.to_json())
        assert parsed.event_id == sample_event.event_id


# ── Ledger I/O ────────────────────────────────────────────────────────────────

class TestLedger:
    def test_write_creates_file(self, project: Path, sample_event: GrimoireEvent) -> None:
        path = write_event(sample_event, project)
        assert path.is_file()
        assert path.read_text(encoding="utf-8").strip() == sample_event.to_json()

    def test_write_appends(self, project: Path) -> None:
        ev1 = GrimoireEvent(scope="tool", phase="start", source_hook="a.sh")
        ev2 = GrimoireEvent(scope="tool", phase="end", source_hook="a.sh")
        write_event(ev1, project)
        write_event(ev2, project)
        lines = (project / "_grimoire-runtime" / "_memory" / "activity.jsonl").read_text().strip().split("\n")
        assert len(lines) == 2

    def test_read_empty_returns_empty_list(self, project: Path) -> None:
        assert read_ledger(project) == []

    def test_read_filters_by_scope(self, project: Path) -> None:
        write_event(GrimoireEvent(scope="tool", phase="start", source_hook="a.sh"), project)
        write_event(GrimoireEvent(scope="subagent", phase="start", source_hook="b.sh"), project)
        events = read_ledger(project, scope="tool")
        assert len(events) == 1
        assert events[0].scope == "tool"

    def test_read_filters_by_since_ts(self, project: Path) -> None:
        early = GrimoireEvent(scope="tool", phase="start", source_hook="a.sh", ts="2026-01-01T00:00:00.000Z")
        late = GrimoireEvent(scope="tool", phase="end", source_hook="a.sh", ts="2026-06-01T00:00:00.000Z")
        write_event(early, project)
        write_event(late, project)
        events = read_ledger(project, since_ts="2026-03-01T00:00:00.000Z")
        assert len(events) == 1
        assert events[0].phase == "end"

    def test_read_applies_limit(self, project: Path) -> None:
        for i in range(5):
            write_event(
                GrimoireEvent(scope="tool", phase="info", source_hook=f"h{i}.sh"),
                project,
            )
        events = read_ledger(project, limit=2)
        assert len(events) == 2

    def test_read_skips_invalid_lines(self, project: Path) -> None:
        ledger = project / "_grimoire-runtime" / "_memory" / "activity.jsonl"
        ledger.write_text(
            '{"not":"valid"}\n'
            + GrimoireEvent(scope="tool", phase="info", source_hook="x.sh").to_json()
            + "\n"
            + "not even json\n"
        )
        events = read_ledger(project)
        assert len(events) == 1
        assert events[0].source_hook == "x.sh"

    def test_write_error_quarantines(self, project: Path) -> None:
        path = write_error("garbage line", "json decode error", project)
        assert path.is_file()
        entry = json.loads(path.read_text().strip())
        assert entry["raw"] == "garbage line"
        assert entry["reason"] == "json decode error"


# ── emit_event (stdout contract) ──────────────────────────────────────────────

class TestEmit:
    def test_emit_prints_jsonl(self, capsys: pytest.CaptureFixture[str], sample_event: GrimoireEvent) -> None:
        emit_event(sample_event)
        captured = capsys.readouterr()
        assert captured.out.strip() == sample_event.to_json()
        reparsed = GrimoireEvent.from_dict(json.loads(captured.out.strip()))
        assert reparsed.event_id == sample_event.event_id


# ── counters aggregation ──────────────────────────────────────────────────────

class TestCounters:
    def test_empty(self) -> None:
        assert counters([]) == {}

    def test_groups_by_scope_phase(self) -> None:
        events = [
            GrimoireEvent(scope="tool", phase="start", source_hook="a.sh"),
            GrimoireEvent(scope="tool", phase="start", source_hook="a.sh"),
            GrimoireEvent(scope="tool", phase="block", source_hook="a.sh"),
            GrimoireEvent(scope="subagent", phase="start", source_hook="b.sh"),
        ]
        result = counters(events)
        assert result == {
            "tool": {"start": 2, "block": 1},
            "subagent": {"start": 1},
        }


# ── CLI smoke ────────────────────────────────────────────────────────────────

class TestCli:
    def test_main_no_args(self, capsys: pytest.CaptureFixture[str]) -> None:
        from grimoire.tools.events import main

        rc = main([])
        captured = capsys.readouterr()
        assert rc == 2
        assert "usage" in captured.out

    def test_main_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        from grimoire.tools.events import main

        rc = main(["--help"])
        assert rc == 0

    def test_cli_emit(self, capsys: pytest.CaptureFixture[str]) -> None:
        from grimoire.tools.events import main

        rc = main(
            [
                "emit",
                "--scope",
                "tool",
                "--phase",
                "start",
                "--source-hook",
                "x.sh",
                "--payload-json",
                '{"k":"v"}',
            ]
        )
        assert rc == 0
        captured = capsys.readouterr()
        ev = GrimoireEvent.from_dict(json.loads(captured.out.strip()))
        assert ev.payload == {"k": "v"}

    def test_cli_emit_invalid_payload(self, capsys: pytest.CaptureFixture[str]) -> None:
        from grimoire.tools.events import main

        rc = main(
            [
                "emit",
                "--scope",
                "tool",
                "--phase",
                "start",
                "--source-hook",
                "x.sh",
                "--payload-json",
                "not json",
            ]
        )
        assert rc == 2

    def test_cli_read_and_counters(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from grimoire.tools.events import main

        write_event(GrimoireEvent(scope="tool", phase="start", source_hook="a.sh"), project)
        write_event(GrimoireEvent(scope="tool", phase="end", source_hook="a.sh"), project)
        rc = main(["read", "--project-root", str(project), "--limit", "10"])
        assert rc == 0
        out = capsys.readouterr().out.strip().split("\n")
        assert len(out) == 2

        rc = main(["counters", "--project-root", str(project)])
        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["tool"]["start"] == 1
        assert data["tool"]["end"] == 1
