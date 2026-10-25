"""R31 + R27 : événements déclarés avec une décision (trace), jamais émis ; drapeaux de profil lus."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.core.standard_generation import TRACES_DIR
from grimoire.hosts.capabilities import all_profiles, gaps_for, profile_for
from grimoire.hosts.decisions import DEFAULT_DECISION_BY_EVENT
from grimoire.hosts.emitters import claude_code, copilot
from grimoire.hosts.events import NOT_EMITTED_EVENTS, HookEvent
from grimoire.hosts.runtime import parse_event, run_hook

_NEW = ("task_created", "task_completed", "instructions_loaded")


def _traces(root: Path) -> list[dict[str, object]]:
    path = root / TRACES_DIR / "traces.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_the_three_events_exist_and_have_a_default_decision() -> None:
    assert {e.value for e in NOT_EMITTED_EVENTS} == set(_NEW)
    for name in _NEW:
        assert DEFAULT_DECISION_BY_EVENT[HookEvent(name)] == "grimoire.event-trace"


@pytest.mark.parametrize("wire", ["TaskCreated", "TaskCompleted", "InstructionsLoaded"])
def test_wire_names_parse(wire: str) -> None:
    assert parse_event(wire) is not None


@pytest.mark.parametrize("name", _NEW)
def test_event_writes_a_trace_and_never_blocks(tmp_path: Path, name: str) -> None:
    (tmp_path / "_grimoire").mkdir()
    assert _traces(tmp_path) == []
    payload = {"task_id": "42", "task_subject": "écrire", "prompt": "secret à ne pas copier"}
    out, decision, _ = run_hook(payload, host_id=HostId.CLAUDE_CODE_CLI, event=HookEvent(name), project_root=tmp_path)
    assert out == {}
    assert not decision.is_refusal
    traces = _traces(tmp_path)
    assert len(traces) == 1
    assert name in traces[0]["tags"]
    assert "secret" not in json.dumps(traces[0])


def test_no_host_declares_nor_receives_these_events() -> None:
    for profile in all_profiles():
        for name in _NEW:
            assert not profile.supports_event(HookEvent(name)), profile.host_id
        assert not set(_NEW) & {e.value for e in profile.supported_events()}


def test_no_emitter_writes_them() -> None:
    for module in (claude_code, copilot):
        assert not {HookEvent(n) for n in _NEW} & set(module._WIRE_NAMES)


def test_mcp_flag_changes_the_gaps() -> None:
    claude = profile_for(HostId.CLAUDE_CODE_CLI)
    assert all(g.surface != "mcp" for g in gaps_for(claude))
    without = replace(claude, mcp_native=False)
    assert any(g.surface == "mcp" for g in gaps_for(without))
