"""Un refus de la garde sous Copilot : forme documentée par GitHub et code de sortie de refus (#1280).

Harness Copilot (https://docs.github.com/en/copilot/reference/hooks-reference) : la
sortie ``preToolUse`` est un objet à plat (``permissionDecision``,
``permissionDecisionReason``) ; « exit ``2`` is treated as a deny: any ``stdout``
JSON is merged with the deny decision ». Harness Local
(https://code.visualstudio.com/docs/agents/reference/hooks-reference) :
``hookSpecificOutput.permissionDecision`` ; « Exit code ``2``: Treat stderr as a
blocking error and provide it to the model ». Claude Code : exit 0 + JSON, inchangé.

Les tests de bout en bout lancent le vrai point d'entrée dans un sous-processus.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts import hook_entry
from grimoire.hosts.runtime import run_hook

pytestmark = pytest.mark.covers("host:github-copilot", level="real")

DESTRUCTIVE = "rm -rf /"


@pytest.fixture
def governed(tmp_path: Path) -> Path:
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    return tmp_path


def _entry(host: str, root: Path, payload: dict[str, Any], *extra: str) -> subprocess.CompletedProcess[str]:
    """Le point d'entrée réel (``hook_entry.main``), comme le lance l'hôte, dans un sous-processus."""
    probe = (
        "import sys\n"
        f"sys.argv = ['entry', '--host', {host!r}, '--event', 'PreToolUse', '--project-root', {str(root)!r}"
        + "".join(f", {e!r}" for e in extra)
        + "]\n"
        "from grimoire.hosts.hook_entry import main\n"
        "raise SystemExit(main())\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GRIMOIRE_", "CLAUDE_"))}
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    return subprocess.run(
        [sys.executable, "-c", probe],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=120,
    )


def _snake(root: Path, command: str) -> dict[str, Any]:
    return {"hook_event_name": "PreToolUse", "cwd": str(root), "tool_name": "Bash", "tool_input": {"command": command}}


def _camel_batch(root: Path) -> dict[str, Any]:
    return {
        "sessionId": "s",
        "cwd": str(root),
        "toolCalls": [
            {"id": "toolu_REDACTED_01", "name": "view", "args": json.dumps({"path": "README.md"})},
            {"id": "toolu_REDACTED_02", "name": "bash", "args": json.dumps({"command": DESTRUCTIVE})},
        ],
    }


def _assert_copilot_refusal(proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    assert proc.returncode == hook_entry.COPILOT_DENY_EXIT == 2, proc.stderr
    out = json.loads(proc.stdout)
    # Forme du harness Copilot, au premier niveau.
    assert out["permissionDecision"] == "deny"
    assert out["permissionDecisionReason"]
    # Forme du harness Local, gardée.
    specific = out["hookSpecificOutput"]
    assert specific["permissionDecision"] == "deny"
    assert specific["permissionDecisionReason"] == out["permissionDecisionReason"]
    # La raison sur stderr : ce que le harness Local donne au modèle sur un code 2.
    assert out["permissionDecisionReason"] in proc.stderr
    return out


def test_copilot_refusal_has_both_shapes_and_exit_2(governed: Path) -> None:
    _assert_copilot_refusal(_entry("copilot", governed, _snake(governed, DESTRUCTIVE)))


def test_copilot_refused_batch_exits_2(governed: Path) -> None:
    out = _assert_copilot_refusal(_entry("copilot", governed, _camel_batch(governed)))
    assert "appel 2/2" in out["permissionDecisionReason"]


def test_copilot_allow_is_unchanged_and_exits_0(governed: Path) -> None:
    payload = {**_snake(governed, ""), "tool_name": "Read", "tool_input": {"file_path": str(governed / "README.md")}}
    proc = _entry("copilot", governed, payload)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert "permissionDecision" not in out
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_claude_code_refusal_is_strictly_unchanged(governed: Path) -> None:
    proc = _entry("claude", governed, _snake(governed, DESTRUCTIVE))
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert set(out) == {"hookSpecificOutput"}
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_copilot_advisory_dispatch_never_exits_2(governed: Path) -> None:
    payload = {**_snake(governed, ""), "tool_name": "task", "tool_input": {"description": "Find files", "prompt": "x"}}
    proc = _entry("copilot", governed, payload, "--decision", "grimoire.dispatch-model")
    assert proc.returncode == 0, proc.stderr


def test_copilot_ask_is_flat_without_exit_2(governed: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.hosts import runtime
    from grimoire.hosts.decisions import Decision, Outcome

    monkeypatch.setattr(runtime, "_decide", lambda *_a: Decision(outcome=Outcome.ASK, reason="[Grimoire] à toi."))
    rendered, decision, _ = run_hook(_snake(governed, "make deploy"), host_id=HostId.GITHUB_COPILOT)
    assert decision.outcome is Outcome.ASK
    assert rendered["permissionDecision"] == "ask" and rendered["permissionDecisionReason"] == "[Grimoire] à toi."
    assert rendered["hookSpecificOutput"]["permissionDecision"] == "ask"
    rendered, _, _ = run_hook(_snake(governed, "make deploy"), host_id=HostId.CLAUDE_CODE_CLI)
    assert set(rendered) == {"hookSpecificOutput"}


def test_runtime_main_returns_2_only_on_a_copilot_refusal(
    governed: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import io

    from grimoire.hosts.runtime import main

    for host, expected in (("copilot", 2), ("claude", 0)):
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_snake(governed, DESTRUCTIVE))))
        code = main(["--host", host, "--event", "PreToolUse", "--project-root", str(governed)])
        capsys.readouterr()
        assert code == expected, host


def test_import_failure_refuses_with_exit_2_under_copilot(capsys: pytest.CaptureFixture[str]) -> None:
    raw = json.dumps({"hook_event_name": "PreToolUse"})
    code = hook_entry.import_failure(["--host", "copilot", "--event", "PreToolUse"], raw, "ImportError: x")
    captured = capsys.readouterr()
    assert code == 2
    out = json.loads(captured.out)
    assert out["permissionDecision"] == "deny" and out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "garde avant outil en panne" in captured.err
    code = hook_entry.import_failure(["--host", "claude", "--event", "PreToolUse"], raw, "ImportError: x")
    assert code == 0 and "permissionDecision" not in json.loads(capsys.readouterr().out)
