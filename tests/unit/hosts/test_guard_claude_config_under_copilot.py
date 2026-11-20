"""Hooks de ``.claude/settings.json`` exécutés par Copilot, et recherche jugée comme une lecture (#1280).

Copilot exécute aussi les hooks de ``.claude/settings.json`` (référence GitHub :
« Cross-tool ``.claude/settings.json`` [...] files in the repository are also
read »), avec ``--host claude-code`` dans la commande. La sortie Claude Code
(code 0 + ``hookSpecificOutput``) n'est pas une forme que la référence GitHub
documente. La charge dit qui l'envoie :

- camelCase, ou snake_case avec ``timestamp`` (entrée PascalCase documentée par
  GitHub, absente de l'entrée Claude Code) : harness Copilot → rendu Copilot, code 2 ;
- clé propre à Claude Code (``permission_mode``…) : rendu Claude Code strict ;
- sinon indécidable : verdict aussi à plat, code 0 (Claude Code ignore la clé inconnue).

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
from grimoire.cli.doctor_copilot import check_copilot_hooks
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts import copilot_wire, hook_entry
from grimoire.hosts.decisions.tool_facts import classify_tool
from grimoire.hosts.emitters.copilot import matcher_for
from grimoire.hosts.runtime import run_hook
from grimoire.hosts.surface import Enforcement, HookEvent, HookSpec
from grimoire.policies.schemas import ActionKind

pytestmark = pytest.mark.covers("host:github-copilot", level="real")

DESTRUCTIVE = "rm -rf /"


@pytest.fixture
def governed(tmp_path: Path) -> Path:
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    return tmp_path


def _entry(host: str, root: Path, payload: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    """Le point d'entrée réel (``hook_entry.main``), comme le lance l'hôte, dans un sous-processus."""
    probe = (
        "import sys\n"
        f"sys.argv = ['entry', '--host', {host!r}, '--event', 'PreToolUse', '--project-root', {str(root)!r}]\n"
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


def _camel(root: Path, command: str) -> dict[str, Any]:
    """Harness Copilot, événement camelCase."""
    return {"sessionId": "s", "timestamp": 1, "cwd": str(root), "toolName": "bash", "toolArgs": {"command": command}}


def _pascal(root: Path, command: str) -> dict[str, Any]:
    """Harness Copilot, événement PascalCase (celui de ``.claude/settings.json``) : entrée documentée par GitHub."""
    return {
        "hook_event_name": "PreToolUse",
        "session_id": "s",
        "timestamp": "2026-11-20T10:00:00.000Z",
        "cwd": str(root),
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }


def _claude(root: Path, command: str) -> dict[str, Any]:
    """Claude Code réel : champs communs de sa référence des hooks."""
    return {
        "session_id": "s",
        "transcript_path": "/t.jsonl",
        "cwd": str(root),
        "permission_mode": "default",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_use_id": "toolu_01",
    }


def _undecidable(root: Path, command: str) -> dict[str, Any]:
    return {"hook_event_name": "PreToolUse", "cwd": str(root), "tool_name": "Bash", "tool_input": {"command": command}}


# ── A. rendu du refus ───────────────────────────────────────────────────────


@pytest.mark.parametrize("shape", [_camel, _pascal])
def test_claude_config_run_by_copilot_refuses_the_copilot_way(governed: Path, shape: Any) -> None:
    proc = _entry("claude-code", governed, shape(governed, DESTRUCTIVE))
    assert proc.returncode == hook_entry.COPILOT_DENY_EXIT == 2, proc.stderr
    out = json.loads(proc.stdout)
    assert out["permissionDecision"] == "deny"
    assert out["permissionDecisionReason"] in proc.stderr
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("extra", [{}, {"timestamp": "2026-11-20T10:00:00.000Z"}])
def test_real_claude_code_refusal_is_strictly_unchanged(governed: Path, extra: dict[str, Any]) -> None:
    """Une clé propre à Claude Code l'emporte, même à côté d'un ``timestamp``."""
    proc = _entry("claude-code", governed, {**_claude(governed, DESTRUCTIVE), **extra})
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert set(out) == {"hookSpecificOutput"}
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert proc.stderr == ""


def test_undecidable_payload_gets_the_flat_verdict_without_exit_2(governed: Path) -> None:
    proc = _entry("claude-code", governed, _undecidable(governed, DESTRUCTIVE))
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert out["permissionDecision"] == "deny"
    assert out["permissionDecisionReason"] == out["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.parametrize("shape", [_camel, _pascal, _claude, _undecidable])
def test_an_allowed_call_never_exits_2_nor_gains_a_flat_verdict(governed: Path, shape: Any) -> None:
    proc = _entry("claude-code", governed, shape(governed, "git status"))
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout) if proc.stdout.strip() else {}
    assert "permissionDecision" not in out


def test_payload_markers() -> None:
    assert copilot_wire.copilot_harness_payload({"sessionId": "s", "toolName": "bash"})
    assert copilot_wire.copilot_harness_payload({"session_id": "s", "timestamp": "t", "tool_name": "Bash"})
    assert not copilot_wire.copilot_harness_payload({"session_id": "s", "tool_name": "Bash"})
    assert not copilot_wire.copilot_harness_payload({"session_id": "s", "timestamp": "t", "permission_mode": "default"})
    assert copilot_wire.claude_code_payload({"permission_mode": "default"})
    assert not copilot_wire.claude_code_payload({"session_id": "s", "tool_name": "Bash"})


def test_import_failure_follows_the_payload_under_claude(capsys: pytest.CaptureFixture[str]) -> None:
    args = ["--host", "claude-code", "--event", "PreToolUse"]
    assert hook_entry.import_failure(args, json.dumps({"sessionId": "s", "toolName": "bash"}), "ImportError: x") == 2
    assert json.loads(capsys.readouterr().out)["permissionDecision"] == "deny"
    raw = json.dumps({"hook_event_name": "PreToolUse", "permission_mode": "default"})
    assert hook_entry.import_failure(args, raw, "ImportError: x") == 0
    assert set(json.loads(capsys.readouterr().out)) == {"hookSpecificOutput"}
    assert hook_entry.import_failure(args, json.dumps({"hook_event_name": "PreToolUse"}), "ImportError: x") == 0
    assert json.loads(capsys.readouterr().out)["permissionDecision"] == "deny"


# ── A. doctor ───────────────────────────────────────────────────────────────


def _write_claude_hooks(project: Path) -> None:
    settings = project / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    entry = {"matcher": "Bash", "hooks": [{"type": "command", "command": "grimoire-hook --host claude-code"}]}
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [entry]}}), encoding="utf-8")


def test_doctor_warns_when_copilot_is_detected_but_only_claude_hooks_exist(tmp_path: Path) -> None:
    _write_claude_hooks(tmp_path)
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "copilot-instructions.md").write_text("x", encoding="utf-8")
    checks = {c.name: c for c in check_copilot_hooks(tmp_path)}
    check = checks["copilot_claude_hooks_only"]
    assert check.passed and check.level == "warn"
    assert "grimoire up" in check.remedy


def test_doctor_is_silent_without_copilot(tmp_path: Path) -> None:
    _write_claude_hooks(tmp_path)
    assert check_copilot_hooks(tmp_path) == []


def test_doctor_reads_the_declared_hosts(tmp_path: Path) -> None:
    _write_claude_hooks(tmp_path)
    (tmp_path / "project-context.yaml").write_text(
        "project:\n  name: p\nhosts:\n  enabled: [claude, copilot]\n", encoding="utf-8"
    )
    assert "copilot_claude_hooks_only" in {c.name for c in check_copilot_hooks(tmp_path)}


# ── B. la recherche est une lecture pour le refus des secrets ───────────────


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("Grep", {"pattern": "KEY", "path": ".env"}),
        ("Grep", {"pattern": "KEY", "path": "config/secrets"}),
        ("Grep", {"pattern": "KEY", "glob": ".env.local"}),
        ("Grep", {"pattern": "KEY", "glob": "**/*.pem"}),
        ("Glob", {"pattern": "**/.env*"}),
        ("Glob", {"pattern": "secrets/**"}),
        ("grep", {"pattern": "KEY", "path": "app/.env"}),
        ("glob", {"pattern": "**/id_rsa"}),
        ("rg", {"pattern": "KEY", "path": ".npmrc"}),
    ],
)
def test_a_search_into_secrets_is_a_secret_read(tool: str, args: dict[str, Any]) -> None:
    facts = classify_tool(tool, args)
    assert facts.kind is ActionKind.SECRET_ACCESS, facts
    assert facts.secret_target


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("Grep", {"pattern": r"\.env", "path": "src"}),
        ("Grep", {"pattern": "secrets/", "glob": "*.py"}),
        ("Grep", {"pattern": "x", "path": "src/grimoire/hosts/secrets.py"}),
        ("Glob", {"pattern": "**/*.py"}),
        ("Glob", {"pattern": "src/**/*.ts", "path": "."}),
        ("grep", {"pattern": "TODO"}),
    ],
)
def test_legitimate_searches_stay_read_only(tool: str, args: dict[str, Any]) -> None:
    facts = classify_tool(tool, args)
    assert facts.kind is not ActionKind.SECRET_ACCESS, facts
    assert not facts.secret_target


@pytest.mark.parametrize(
    ("host", "payload"),
    [
        (HostId.CLAUDE_CODE_CLI, {"tool_name": "Grep", "tool_input": {"pattern": "KEY", "path": ".env"}}),
        (HostId.CLAUDE_CODE_CLI, {"tool_name": "Glob", "tool_input": {"pattern": "**/.env"}}),
        (HostId.GITHUB_COPILOT, {"sessionId": "s", "toolName": "grep", "toolArgs": {"pattern": "K", "path": ".env"}}),
        (HostId.GITHUB_COPILOT, {"sessionId": "s", "toolName": "glob", "toolArgs": {"pattern": "**/.env"}}),
    ],
)
def test_the_guard_refuses_a_search_into_secrets_like_a_read(
    governed: Path, host: HostId, payload: dict[str, Any]
) -> None:
    base = {"hook_event_name": "PreToolUse", "cwd": str(governed)} if "tool_name" in payload else {"cwd": str(governed)}
    _, decision, _ = run_hook({**base, **payload}, host_id=host, event=HookEvent.PRE_TOOL_USE, project_root=governed)
    assert decision.is_refusal, decision
    read = {"cwd": str(governed), "tool_name": "Read", "tool_input": {"file_path": ".env"}}
    _, read_decision, _ = run_hook(
        read, host_id=HostId.CLAUDE_CODE_CLI, event=HookEvent.PRE_TOOL_USE, project_root=governed
    )
    assert read_decision.outcome is decision.outcome


def test_copilot_guard_matcher_covers_search_tools() -> None:
    hook = HookSpec(
        event=HookEvent.PRE_TOOL_USE,
        decision="grimoire.pre-tool-use",
        matcher=("secret",),
        enforcement=Enforcement.BLOCKING,
    )
    tools = set(matcher_for(hook).split("|"))
    assert {"Grep", "grep", "rg", "Glob", "glob", "Read", "view"} <= tools
