"""Tests for grimoire.tools.anomaly_burst — V4.6 BM-anomaly."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grimoire.tools.anomaly_burst import (
    DEFAULT_COOLDOWN_SECONDS,
    DEFAULT_THRESHOLD,
    DEFAULT_WINDOW_SECONDS,
    BurstState,
    RecordedEvent,
    detect_burst,
    load_state,
    main,
    prune_cooldowns,
    prune_events,
    record,
    save_state,
)


def _ts(seconds: int) -> str:
    return (datetime(2026, 4, 22, 10, 0, 0, tzinfo=UTC) + timedelta(seconds=seconds)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )


def _now(seconds: int) -> datetime:
    return datetime(2026, 4, 22, 10, 0, 0, tzinfo=UTC) + timedelta(seconds=seconds)


# ── pruning ──────────────────────────────────────────────────────────────────

class TestPruneEvents:
    def test_drops_events_outside_window(self) -> None:
        events = [
            RecordedEvent(ts=_ts(0), agent="dev", event="SubagentStop"),
            RecordedEvent(ts=_ts(150), agent="dev", event="SubagentStop"),
        ]
        kept = prune_events(events, now=_now(200), window_seconds=120)
        assert len(kept) == 1
        assert kept[0].ts == _ts(150)

    def test_caps_retention(self) -> None:
        events = [
            RecordedEvent(ts=_ts(i), agent="dev", event="SubagentStop") for i in range(2000)
        ]
        kept = prune_events(events, now=_now(2000), window_seconds=10_000, max_retained=500)
        assert len(kept) == 500
        # Most recent kept.
        assert kept[-1].ts == _ts(1999)

    def test_skips_invalid_ts(self) -> None:
        events = [
            RecordedEvent(ts="not-a-date", agent="dev", event="SubagentStop"),
            RecordedEvent(ts=_ts(0), agent="dev", event="SubagentStop"),
        ]
        kept = prune_events(events, now=_now(0), window_seconds=120)
        assert len(kept) == 1


class TestPruneCooldowns:
    def test_drops_expired_cooldowns(self) -> None:
        cooldowns = {"dev::SubagentStop": _ts(0), "qa::SubagentStop": _ts(200)}
        pruned = prune_cooldowns(cooldowns, now=_now(300), cooldown_seconds=120)
        assert "dev::SubagentStop" not in pruned
        assert "qa::SubagentStop" in pruned


# ── detection ────────────────────────────────────────────────────────────────

class TestDetectBurst:
    def test_returns_none_below_threshold(self) -> None:
        events = [RecordedEvent(ts=_ts(i * 5), agent="dev", event="SubagentStop") for i in range(3)]
        anomaly = detect_burst(events, agent="dev", event_name="SubagentStop", threshold=8, window_seconds=120)
        assert anomaly is None

    def test_returns_anomaly_at_threshold(self) -> None:
        events = [RecordedEvent(ts=_ts(i), agent="dev", event="SubagentStop") for i in range(8)]
        anomaly = detect_burst(events, agent="dev", event_name="SubagentStop", threshold=8, window_seconds=120)
        assert anomaly is not None
        assert anomaly.count == 8
        assert anomaly.first_ts == _ts(0)
        assert anomaly.last_ts == _ts(7)

    def test_only_counts_matching_pair(self) -> None:
        events = [
            RecordedEvent(ts=_ts(i), agent="dev", event="SubagentStop") for i in range(4)
        ] + [
            RecordedEvent(ts=_ts(i + 4), agent="qa", event="SubagentStop") for i in range(4)
        ]
        anomaly = detect_burst(events, agent="dev", event_name="SubagentStop", threshold=5, window_seconds=120)
        assert anomaly is None


# ── record ───────────────────────────────────────────────────────────────────

class TestRecord:
    def test_no_anomaly_below_threshold(self) -> None:
        state = BurstState()
        for i in range(3):
            state, anomaly = record(state, agent="dev", event_name="SubagentStop", now=_now(i), threshold=5, window_seconds=120)
            assert anomaly is None
        assert len(state.events) == 3

    def test_emits_anomaly_at_threshold(self) -> None:
        state = BurstState()
        anomaly = None
        for i in range(5):
            state, anomaly = record(state, agent="dev", event_name="SubagentStop", now=_now(i), threshold=5, window_seconds=120)
        assert anomaly is not None
        assert anomaly.count == 5

    def test_cooldown_prevents_duplicate_anomalies(self) -> None:
        state = BurstState()
        anomaly = None
        for i in range(5):
            state, anomaly = record(
                state, agent="dev", event_name="SubagentStop",
                now=_now(i), threshold=5, window_seconds=120, cooldown_seconds=300,
            )
        assert anomaly is not None
        # Next event still inside cooldown — no anomaly.
        state, anomaly = record(
            state, agent="dev", event_name="SubagentStop",
            now=_now(6), threshold=5, window_seconds=120, cooldown_seconds=300,
        )
        assert anomaly is None

    def test_cooldown_expires(self) -> None:
        state = BurstState()
        anomaly = None
        for i in range(5):
            state, anomaly = record(
                state, agent="dev", event_name="SubagentStop",
                now=_now(i), threshold=5, window_seconds=120, cooldown_seconds=10,
            )
        assert anomaly is not None
        # Wait past cooldown and beyond original window — events expire too,
        # so we must accumulate again before seeing a new anomaly.
        for i in range(5):
            state, anomaly = record(
                state, agent="dev", event_name="SubagentStop",
                now=_now(200 + i), threshold=5, window_seconds=120, cooldown_seconds=10,
            )
        assert anomaly is not None

    def test_isolated_agents_do_not_trigger_each_other(self) -> None:
        state = BurstState()
        for i in range(4):
            state, _ = record(state, agent="dev", event_name="SubagentStop", now=_now(i), threshold=5, window_seconds=120)
        for i in range(4):
            state, anomaly = record(state, agent="qa", event_name="SubagentStop", now=_now(10 + i), threshold=5, window_seconds=120)
        assert anomaly is None


# ── state I/O ────────────────────────────────────────────────────────────────

class TestStateIO:
    def test_load_state_missing_file(self, tmp_path: Path) -> None:
        state = load_state(tmp_path / "missing.json")
        assert state.events == []
        assert state.cooldowns == {}

    def test_load_state_corrupt_file(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        path.write_text("not json", encoding="utf-8")
        state = load_state(path)
        assert state.events == []

    def test_save_then_load_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        original = BurstState(
            events=[RecordedEvent(ts=_ts(0), agent="dev", event="SubagentStop")],
            cooldowns={"dev::SubagentStop": _ts(0)},
        )
        save_state(path, original)
        loaded = load_state(path)
        assert loaded.events == original.events
        assert loaded.cooldowns == original.cooldowns


# ── CLI ──────────────────────────────────────────────────────────────────────

class TestCLI:
    def test_record_no_anomaly_returns_null(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        state_file = tmp_path / "state.json"
        rc = main([
            "record",
            "--agent", "dev",
            "--event", "SubagentStop",
            "--state-file", str(state_file),
            "--threshold", "5",
        ])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload == {"anomaly": None}
        assert state_file.exists()

    def test_record_emits_anomaly_after_threshold(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        state_file = tmp_path / "state.json"
        anomaly_payload: dict[str, object] | None = None
        for i in range(5):
            rc = main([
                "record",
                "--agent", "dev",
                "--event", "SubagentStop",
                "--state-file", str(state_file),
                "--threshold", "5",
                "--window-seconds", "120",
                "--now", _ts(i),
            ])
            assert rc == 0
            anomaly_payload = json.loads(capsys.readouterr().out)
        assert anomaly_payload is not None
        assert anomaly_payload["anomaly"] is not None
        assert anomaly_payload["anomaly"]["agent"] == "dev"
        assert anomaly_payload["anomaly"]["count"] == 5

    def test_inspect_returns_state(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        state_file = tmp_path / "state.json"
        main([
            "record",
            "--agent", "dev",
            "--event", "SubagentStop",
            "--state-file", str(state_file),
            "--now", _ts(0),
        ])
        capsys.readouterr()  # discard
        rc = main(["inspect", "--state-file", str(state_file)])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["version"]
        assert len(payload["events"]) == 1


def test_defaults_are_sane() -> None:
    assert DEFAULT_THRESHOLD >= 3
    assert DEFAULT_WINDOW_SECONDS >= 30
    assert DEFAULT_COOLDOWN_SECONDS >= DEFAULT_WINDOW_SECONDS
