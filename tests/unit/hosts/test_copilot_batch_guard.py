"""La garde avant outil juge chaque appel d'un lot Copilot (épique #1280).

Le harness Copilot peut annoncer plusieurs appels d'outil dans une seule
invocation de ``preToolUse`` (liste ``toolCalls``, fixture
``copilot-harness/pretooluse-batch.json``). La garde ne jugeait pas ces appels
groupés : sans nom d'outil lisible, elle rendait une autorisation. Elle juge
désormais chaque appel, rend le verdict le plus restrictif, et refuse ce
qu'elle ne sait pas lire.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts.decisions import Outcome
from grimoire.hosts.events import HookEvent
from grimoire.hosts.runtime import run_hook

DESTRUCTIVE = "rm -rf /"


@pytest.fixture
def governed(tmp_path: Path) -> Path:
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    return tmp_path


def _call(index: int, name: str, args: dict | str) -> dict:
    return {
        "id": f"toolu_REDACTED_{index:02d}",
        "name": name,
        "args": args if isinstance(args, str) else json.dumps(args),
    }


def _pre(root: Path, calls: list[dict]) -> tuple[dict, object]:
    payload = {"sessionId": "00000000-0000-4000-8000-0000000000f1", "cwd": str(root), "toolCalls": calls}
    rendered, decision, _hook = run_hook(payload, host_id=HostId.GITHUB_COPILOT, event=HookEvent.PRE_TOOL_USE)
    return rendered, decision


def _verdict(rendered: dict) -> str:
    return rendered["hookSpecificOutput"]["permissionDecision"]


def test_a_batch_with_one_destructive_call_is_denied(governed: Path) -> None:
    rendered, decision = _pre(
        governed,
        [_call(1, "view", {"path": str(governed / "README.md")}), _call(2, "bash", {"command": DESTRUCTIVE})],
    )
    assert decision.outcome is Outcome.DENY
    assert _verdict(rendered) == "deny"
    assert "2/2" in decision.reason and "bash" in decision.reason


def test_the_destructive_call_is_found_wherever_it_sits(governed: Path) -> None:
    rendered, _ = _pre(
        governed,
        [
            _call(1, "bash", {"command": DESTRUCTIVE}),
            _call(2, "view", {"path": str(governed / "README.md")}),
            _call(3, "view", {"path": str(governed / "docs")}),
        ],
    )
    assert _verdict(rendered) == "deny"


def test_a_batch_of_reads_is_allowed(governed: Path) -> None:
    rendered, decision = _pre(
        governed,
        [_call(1, "view", {"path": str(governed / "README.md")}), _call(2, "view", {"path": str(governed / "docs")})],
    )
    assert decision.outcome is Outcome.ALLOW
    assert _verdict(rendered) == "allow"


def test_a_single_camel_case_destructive_call_is_denied(governed: Path) -> None:
    rendered, _ = _pre(governed, [_call(1, "bash", {"command": DESTRUCTIVE})])
    assert _verdict(rendered) == "deny"


def test_tool_name_and_tool_args_pair_is_judged(governed: Path) -> None:
    payload = {
        "sessionId": "s",
        "cwd": str(governed),
        "toolName": "bash",
        "toolArgs": json.dumps({"command": DESTRUCTIVE}),
    }
    rendered, _, _ = run_hook(payload, host_id=HostId.GITHUB_COPILOT, event=HookEvent.PRE_TOOL_USE)
    assert _verdict(rendered) == "deny"


@pytest.mark.parametrize(
    "calls",
    [
        [{"id": "toolu_REDACTED_01", "name": "bash", "args": "{not json"}],
        [{"id": "toolu_REDACTED_01", "name": "bash", "args": "[1, 2]"}],
        [{"id": "toolu_REDACTED_01", "args": "{}"}],
        [{"id": "toolu_REDACTED_01", "name": "", "args": "{}"}],
        ["bash"],
        [],
        [_call(1, "view", {"path": "README.md"}), {"id": "toolu_REDACTED_02", "name": "bash", "args": "{oops"}],
    ],
    ids=[
        "args-invalid-json",
        "args-not-object",
        "name-missing",
        "name-empty",
        "entry-not-object",
        "empty-list",
        "one-bad-in-batch",
    ],
)
def test_an_unreadable_call_is_denied(governed: Path, calls: list) -> None:
    rendered, decision = _pre(governed, calls)
    assert decision.outcome is Outcome.DENY
    assert _verdict(rendered) == "deny"
    assert decision.detail.get("unreadable_tool_call")


def test_tool_calls_that_is_not_a_list_is_denied(governed: Path) -> None:
    payload = {"sessionId": "s", "cwd": str(governed), "toolCalls": "bash"}
    rendered, _, _ = run_hook(payload, host_id=HostId.GITHUB_COPILOT, event=HookEvent.PRE_TOOL_USE)
    assert _verdict(rendered) == "deny"


@pytest.mark.parametrize("host", [HostId.GITHUB_COPILOT, HostId.CLAUDE_CODE_CLI])
def test_a_pre_tool_use_without_a_tool_name_is_never_allowed(governed: Path, host: HostId) -> None:
    payload = {"hook_event_name": "PreToolUse", "cwd": str(governed), "tool_input": {"command": "ls"}}
    rendered, decision, _ = run_hook(payload, host_id=host)
    assert decision.outcome is Outcome.DENY
    assert _verdict(rendered) == "deny"


def test_a_pre_tool_use_outside_a_project_without_a_tool_name_is_denied(tmp_path: Path) -> None:
    payload = {"sessionId": "s", "cwd": str(tmp_path), "toolCalls": [{"id": "toolu_REDACTED_01", "args": "{}"}]}
    rendered, _, _ = run_hook(payload, host_id=HostId.GITHUB_COPILOT, event=HookEvent.PRE_TOOL_USE)
    assert _verdict(rendered) == "deny"


class TestSnakeCaseUnchanged:
    def test_snake_case_destructive_call_is_still_denied(self, governed: Path) -> None:
        payload = {
            "hook_event_name": "PreToolUse",
            "cwd": str(governed),
            "tool_name": "Bash",
            "tool_input": {"command": DESTRUCTIVE},
        }
        for host in (HostId.GITHUB_COPILOT, HostId.CLAUDE_CODE_CLI):
            rendered, _, _ = run_hook(payload, host_id=host)
            assert _verdict(rendered) == "deny"

    def test_snake_case_read_is_still_allowed(self, governed: Path) -> None:
        payload = {
            "hook_event_name": "PreToolUse",
            "cwd": str(governed),
            "tool_name": "Read",
            "tool_input": {"file_path": str(governed / "README.md")},
        }
        rendered, _, _ = run_hook(payload, host_id=HostId.CLAUDE_CODE_CLI)
        assert _verdict(rendered) == "allow"


def test_advisory_decision_is_not_turned_into_a_refusal(governed: Path) -> None:
    payload = {"sessionId": "s", "cwd": str(governed), "toolCalls": [{"id": "toolu_REDACTED_01", "args": "{}"}]}
    _rendered, decision, _ = run_hook(
        payload, host_id=HostId.GITHUB_COPILOT, event=HookEvent.PRE_TOOL_USE, decision_id="grimoire.dispatch-model"
    )
    assert decision.outcome is not Outcome.DENY


def test_batch_decisions_are_traced_per_call(governed: Path) -> None:
    _pre(governed, [_call(1, "view", {"path": "README.md"}), _call(2, "bash", {"command": DESTRUCTIVE})])
    traces = list(governed.rglob("traces.jsonl"))
    assert traces, "la décision doit laisser une trace"
    tools = [
        call["tool"]
        for line in traces[0].read_text(encoding="utf-8").splitlines()
        for call in json.loads(line).get("tool_calls", [])
    ]
    assert "bash" in tools


def test_post_tool_use_with_a_batch_payload_does_not_crash(governed: Path) -> None:
    payload = {
        "sessionId": "s",
        "timestamp": 1781000000000,
        "cwd": str(governed),
        "toolCalls": [_call(1, "view", {"path": "README.md"}), _call(2, "bash", {"command": "ls"})],
    }
    _rendered, decision, hook = run_hook(payload, host_id=HostId.GITHUB_COPILOT, event=HookEvent.POST_TOOL_USE)
    assert hook.event is HookEvent.POST_TOOL_USE
    assert decision.outcome is not Outcome.DENY
