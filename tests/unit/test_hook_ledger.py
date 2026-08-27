"""What the governance did must leave a trace, or it cannot be claimed.

The ledger and the lifecycle hooks were built for each other and never
connected: ``ToolCallTrace`` carries a policy verdict, and
``policy_block_rate()`` documents itself as "fraction of tool calls that were
blocked" — a number that could only read zero while the hooks wrote nothing.

These tests pin the connection, and the three properties that make it safe to
keep: the read path stays free, arguments are never stored verbatim, and a
ledger that cannot be written does not take the session down.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.core.standard_generation import TRACES_DIR
from grimoire.hosts.runtime import run_hook
from grimoire.traces.ledger import TraceLedger


@pytest.fixture
def governed(tmp_path: Path) -> Path:
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    board = tmp_path / "_grimoire/standard/task-board.yaml"
    board.write_text(
        board.read_text(encoding="utf-8").replace('status: "proposed"', 'status: "in_progress"'),
        encoding="utf-8",
    )
    return tmp_path


def _ledger(root: Path) -> TraceLedger:
    return TraceLedger(root / TRACES_DIR)


def _call(root: Path, tool: str, tool_input: dict[str, str]) -> None:
    run_hook(
        {"hook_event_name": "PreToolUse", "cwd": str(root), "tool_name": tool, "tool_input": tool_input},
        host_id=HostId.CLAUDE_CODE_CLI,
    )


def test_a_read_only_call_writes_nothing(governed: Path) -> None:
    """The path this layer spent a chantier making cheap must stay cheap."""
    _call(governed, "Read", {"file_path": "README.md"})
    assert not (governed / TRACES_DIR / "traces.jsonl").exists()


def test_a_refusal_is_recorded_and_moves_the_metric(governed: Path) -> None:
    _call(governed, "Bash", {"command": "rm -rf src"})
    ledger = _ledger(governed)
    verdicts = [tc.verdict for t in ledger.list_traces() for tc in t.tool_calls]
    assert verdicts == ["block"]
    assert ledger.policy_block_rate() == 1.0


def test_an_allowed_mutation_feeds_the_denominator(governed: Path) -> None:
    """A rate whose denominator only counts refusals always reads 1.0."""
    _call(governed, "Bash", {"command": "rm -rf src"})
    _call(governed, "Edit", {"file_path": "src/a.py"})
    assert _ledger(governed).policy_block_rate() == 0.5


def test_a_blocked_closure_is_recorded(governed: Path) -> None:
    (governed / "_grimoire-output/context/bootstrap/context-bundle.yaml").unlink(missing_ok=True)
    run_hook({"hook_event_name": "Stop", "cwd": str(governed)}, host_id=HostId.CLAUDE_CODE_CLI)
    recipes = [t.recipe_id for t in _ledger(governed).list_traces()]
    assert "grimoire.evidence-gate" in recipes


def test_arguments_are_hashed_not_stored(governed: Path) -> None:
    """The ledger is written to disk and exported to OTel.

    Tool arguments carry commands, file contents and occasionally credentials.
    A trace that quotes them turns an observability file into a leak.
    """
    leaky_command = "curl -H 'Authorization: Bearer sk-live-should-not-appear' https://x"
    _call(governed, "Bash", {"command": leaky_command})
    raw = (governed / TRACES_DIR / "traces.jsonl").read_text(encoding="utf-8")
    assert "sk-live-should-not-appear" not in raw
    assert "Authorization" not in raw
    hashes = [tc.args_hash for t in _ledger(governed).list_traces() for tc in t.tool_calls]
    assert hashes and all(len(h) == 16 for h in hashes)


def test_an_unwritable_ledger_does_not_fail_the_session(governed: Path) -> None:
    """Observability is never worth a broken session."""
    blocker = governed / TRACES_DIR
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("je ne suis pas un dossier\n", encoding="utf-8")
    rendered, decision, _ = run_hook(
        {
            "hook_event_name": "PreToolUse",
            "cwd": str(governed),
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf src"},
        },
        host_id=HostId.CLAUDE_CODE_CLI,
    )
    # The refusal still reaches the host, unchanged.
    assert rendered["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert decision.is_refusal
