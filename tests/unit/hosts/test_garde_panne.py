"""Une garde en échec fermé qui tombe en panne refuse avec un motif, jamais par un plantage muet.

Avec ``"onFailure": "block"``, un hook qui sort en erreur refuse Bash, Edit et
Write sans dire quoi faire. Le point d'entrée rend donc un refus explicite (le
remède : ``grimoire doctor``, ``grimoire up``, ou retirer ``onFailure``), et
``grimoire doctor`` nomme la garde dont le programme ne se lance pas.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from grimoire.cli.doctor_hooks import check_hook_commands
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts import hook_entry, runtime
from grimoire.hosts.decisions import HookInput, decide_tool_policy, tool_policy
from grimoire.hosts.events import HookEvent

pytestmark = pytest.mark.covers("host:claude-code-cli", level="integration")

_PAYLOAD = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "git status"}})


def _run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], args: list[str], raw: str
) -> tuple[int, str]:
    monkeypatch.setattr(sys, "stdin", io.StringIO(raw))
    code = hook_entry.main(args)
    return code, capsys.readouterr().out


def test_an_internal_failure_of_the_guard_becomes_an_explicit_refusal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("panne interne")

    monkeypatch.setattr(runtime, "run_hook", boom)
    code, out = _run(
        monkeypatch, capsys, ["--host", "claude", "--event", "PreToolUse", "--project-root", str(tmp_path)], _PAYLOAD
    )
    assert code == 0
    verdict = json.loads(out)["hookSpecificOutput"]
    assert verdict["permissionDecision"] == "deny"
    assert "panne interne" in verdict["permissionDecisionReason"]
    assert "grimoire doctor" in verdict["permissionDecisionReason"]
    assert "onFailure" in verdict["permissionDecisionReason"]


def test_an_internal_failure_on_an_advisory_event_still_surfaces(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Hors de la garde, rien n'est émis en échec fermé : la panne remonte comme avant, sans verdict inventé."""

    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("panne interne")

    monkeypatch.setattr(runtime, "run_hook", boom)
    with pytest.raises(RuntimeError):
        _run(monkeypatch, capsys, ["--host", "claude", "--event", "PostToolUse", "--project-root", str(tmp_path)], "{}")


def test_a_runtime_that_no_longer_imports_still_refuses_with_a_remedy(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(sys.modules, "grimoire.hosts.runtime", None)  # l'import lève ImportError
    code, out = _run(monkeypatch, capsys, ["--host", "claude", "--event", "PreToolUse"], _PAYLOAD)
    assert code == 0
    verdict = json.loads(out)["hookSpecificOutput"]
    assert verdict["permissionDecision"] == "deny"
    assert "ModuleNotFoundError" in verdict["permissionDecisionReason"]
    assert "grimoire up" in verdict["permissionDecisionReason"]


def test_a_runtime_that_no_longer_imports_reads_the_event_from_the_payload(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(sys.modules, "grimoire.hosts.runtime", None)
    code, out = _run(monkeypatch, capsys, ["--host", "claude"], _PAYLOAD)
    assert code == 0
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_a_missing_runtime_never_blocks_an_advisory_event(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(sys.modules, "grimoire.hosts.runtime", None)
    code, out = _run(monkeypatch, capsys, ["--host", "claude", "--event", "SessionStart"], "{}")
    assert code == 0
    payload = json.loads(out)
    assert "hookSpecificOutput" not in payload
    assert "hook indisponible" in payload["systemMessage"]


def test_a_missing_runtime_on_gemini_uses_its_documented_refusal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(sys.modules, "grimoire.hosts.runtime", None)
    code, _ = _run(monkeypatch, capsys, ["--host", "gemini", "--event", "BeforeTool"], "{}")
    assert code == 2


def test_doctor_names_a_fail_closed_guard_whose_program_does_not_launch(tmp_path: Path) -> None:
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    missing = (tmp_path / "absent" / "grimoire-hook").as_posix()
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"{missing} --host claude --event PreToolUse",
                                    "timeout": 10,
                                    "onFailure": "block",
                                }
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    (check,) = check_hook_commands(tmp_path)
    assert not check.passed
    assert "onFailure: block" in check.detail


def test_budget_refusals_are_journaled_as_holds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    holds: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "grimoire.hosts.decisions.calibration.record_policy_hold", lambda *_a, **kwargs: holds.append(kwargs)
    )
    command = "echo" + " a" * (tool_policy.MAX_COMMAND_WORDS + 1)
    hook = HookInput(
        event=HookEvent.PRE_TOOL_USE, project_root=tmp_path, tool_name="Bash", tool_input={"command": command}
    )
    decide_tool_policy(hook)
    assert [h["reason"] for h in holds] == ["tool_policy:deny:oversized"]
