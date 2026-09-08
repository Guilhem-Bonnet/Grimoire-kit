"""Tests for the P2P exchange guard module (B11 — MAST FM-1.3/FM-1.5).

`framework/tools/message-bus.py` is frozen (framework/FREEZE.md): this guard
never imports it. It only consumes the same sender/recipient shape that a
caller already has in hand from a bus message (``AgentMessage.sender`` /
``.recipient`` in ``framework/tools/message-bus.py``), matching the
governance rule declared in ``framework/agent-mesh-network.md:244``
(``max_p2p_without_sog: 5``).
"""

from __future__ import annotations

import json

import pytest

from grimoire.runtime.p2p_guard import (
    DEFAULT_MAX_P2P_WITHOUT_SOG,
    P2PGuard,
    load_p2p_guard_config,
    observe_p2p_message,
)


@pytest.fixture
def guard(tmp_path):
    return P2PGuard(tmp_path / "runtime")


def test_default_threshold_matches_the_declared_limit():
    # framework/agent-mesh-network.md:244
    assert DEFAULT_MAX_P2P_WITHOUT_SOG == 5


def test_four_exchanges_between_two_agents_do_not_escalate(guard):
    for _ in range(4):
        assert guard.observe("dev", "architect") == "ok"
    assert guard.count_for("dev", "architect") == 4


def test_fifth_consecutive_exchange_escalates(guard):
    for _ in range(4):
        guard.observe("dev", "architect")
    assert guard.observe("dev", "architect") == "escalate"


def test_direction_does_not_matter_for_the_pair_count(guard):
    guard.observe("dev", "architect")
    guard.observe("architect", "dev")
    guard.observe("dev", "architect")
    guard.observe("architect", "dev")
    assert guard.observe("dev", "architect") == "escalate"


def test_message_via_sog_resets_the_counter(guard):
    for _ in range(4):
        guard.observe("dev", "architect")
    assert guard.count_for("dev", "architect") == 4
    # dev loops the SOG in — the p2p clock between dev and architect resets.
    assert guard.observe("dev", "grimoire-master") == "ok"
    assert guard.count_for("dev", "architect") == 0
    # Fresh run of 4 after the reset still doesn't escalate.
    for _ in range(4):
        assert guard.observe("dev", "architect") == "ok"


def test_escalation_resets_the_pair_so_it_does_not_re_escalate_every_message(guard):
    for _ in range(4):
        guard.observe("dev", "architect")
    assert guard.observe("dev", "architect") == "escalate"
    assert guard.count_for("dev", "architect") == 0
    assert guard.observe("dev", "architect") == "ok"


def test_independent_pairs_are_tracked_separately(guard):
    for _ in range(4):
        guard.observe("dev", "architect")
    assert guard.observe("dev", "qa") == "ok"
    assert guard.count_for("dev", "qa") == 1
    assert guard.count_for("dev", "architect") == 4


def test_escalation_emits_a_journal_event(guard):
    for _ in range(4):
        guard.observe("dev", "architect")
    guard.observe("dev", "architect")
    events = guard.list_events()
    assert len(events) == 1
    event = events[0]
    assert event["event_type"] == "p2p.sog_escalation"
    assert event["decision"] == "escalate"
    assert sorted(event["pair"]) == ["architect", "dev"]
    assert event["threshold"] == DEFAULT_MAX_P2P_WITHOUT_SOG


def test_state_persists_across_separate_guard_instances(tmp_path):
    root = tmp_path / "runtime"
    P2PGuard(root).observe("dev", "architect")
    P2PGuard(root).observe("dev", "architect")
    assert P2PGuard(root).count_for("dev", "architect") == 2


def test_broadcast_recipient_is_not_a_p2p_exchange(guard):
    assert guard.observe("dev", "*") == "ok"
    assert guard.count_for("dev", "*") == 0


def test_custom_threshold_and_sog_ids(tmp_path):
    g = P2PGuard(tmp_path / "runtime", max_p2p_without_sog=2, sog_agent_ids={"concierge"})
    assert g.observe("dev", "architect") == "ok"
    assert g.observe("dev", "architect") == "escalate"
    assert g.observe("dev", "concierge") == "ok"
    assert g.count_for("dev", "architect") == 0


def test_observe_p2p_message_simple_entry_point(tmp_path):
    root = tmp_path / "runtime"
    for _ in range(4):
        assert observe_p2p_message("dev", "architect", state_root=root) == "ok"
    assert observe_p2p_message("dev", "architect", state_root=root) == "escalate"


def test_journal_events_are_valid_jsonl(guard):
    for _ in range(5):
        guard.observe("dev", "architect")
    journal = guard._events_path  # internal, but the on-disk shape is the contract
    lines = journal.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    json.loads(lines[0])  # must not raise


def test_load_config_reads_governance_override(tmp_path):
    (tmp_path / "project-context.yaml").write_text(
        "messaging:\n  governance:\n    max_p2p_without_sog: 3\n",
        encoding="utf-8",
    )
    assert load_p2p_guard_config(tmp_path) == {"max_p2p_without_sog": 3}


def test_load_config_returns_empty_when_no_config_file(tmp_path):
    assert load_p2p_guard_config(tmp_path) == {}


def test_load_config_falls_back_silently_on_malformed_yaml(tmp_path, caplog):
    # A YAMLError (not OSError/ValueError) must not propagate: the docstring
    # promises a silent fallback to {} for "PyYAML, the files, or the section
    # absent" — malformed YAML belongs in that same bucket, logged instead
    # of raised.
    (tmp_path / "project-context.yaml").write_text(
        "messaging:\n  governance: [unterminated\n",
        encoding="utf-8",
    )
    with caplog.at_level("WARNING"):
        assert load_p2p_guard_config(tmp_path) == {}
    assert "p2p_guard" in caplog.text
