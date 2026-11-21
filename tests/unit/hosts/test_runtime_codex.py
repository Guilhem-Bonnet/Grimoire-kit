"""Verdicts rendus au format Codex, par le vrai point d'entrée ``grimoire-hook`` (#1281, lot L2).

Source du protocole : https://learn.chatgpt.com/docs/hooks (lue le 2026-11-21).
Codex n'est pas installé sur le poste : les charges ci-dessous sont des
fixtures tirées de cette page (champs communs ``session_id``, ``transcript_path``,
``cwd``, ``hook_event_name``, ``model`` ; ``turn_id``, ``tool_name``,
``tool_use_id``, ``tool_input`` pour ``PreToolUse`` ; ``stop_hook_active`` et
``last_assistant_message`` pour ``Stop``), pas des captures.

Ce qui est verrouillé : un refus sort en ``hookSpecificOutput.permissionDecision:
"deny"`` plus code 2 et raison sur stderr ; un ``ask`` — que Codex ne prend pas
sur ``PreToolUse`` (le hook échouerait et l'outil passerait) — sort en refus
motivé ; une autorisation ne sort aucun champ non documenté ; ``Stop`` refuse
par ``decision: "block"`` plus ``reason``.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

from grimoire.bridges.schemas import HostId
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts import codex_wire, hook_entry
from grimoire.hosts.decisions import Decision, HookInput, Outcome
from grimoire.hosts.events import HookEvent
from grimoire.hosts.runtime import render, run_hook

pytestmark = pytest.mark.covers("host:codex", level="integration")

#: Champs que la page n'accepte pas sur PreToolUse : ils font échouer le hook, l'outil passe.
_PRE_TOOL_UNSUPPORTED = {"continue", "stopReason", "suppressOutput"}


def _codex_pre_tool(root: Path, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": "00000000-0000-4000-8000-00000000c0de",
        "transcript_path": "/tmp/codex-transcript.jsonl",
        "cwd": str(root),
        "hook_event_name": "PreToolUse",
        "model": "gpt-5.5",
        "permission_mode": "default",
        "turn_id": "turn-1",
        "tool_name": tool_name,
        "tool_use_id": "call-1",
        "tool_input": tool_input,
    }


def _codex_stop(root: Path) -> dict[str, Any]:
    return {
        "session_id": "00000000-0000-4000-8000-00000000c0de",
        "transcript_path": "/tmp/codex-transcript.jsonl",
        "cwd": str(root),
        "hook_event_name": "Stop",
        "model": "gpt-5.5",
        "turn_id": "turn-2",
        "stop_hook_active": False,
        "last_assistant_message": "Terminé.",
    }


def _entry(root: Path, event: str, payload: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    """``hook_entry.main`` dans un sous-processus, avec les arguments que `.codex/hooks.json` écrit."""
    probe = (
        "import sys\n"
        f"sys.argv = ['grimoire-hook', '--host', 'codex', '--event', {event!r}, '--project-root', {str(root)!r}]\n"
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


def _profile(tmp_path: Path, profile: str) -> Path:
    setup_standard_profile(tmp_path, profile_id=profile, task_id="bootstrap")
    return tmp_path


def _assert_refusal(proc: subprocess.CompletedProcess[str]) -> str:
    assert proc.returncode == codex_wire.DENY_EXIT == 2, proc.stderr
    out = json.loads(proc.stdout)
    assert set(out) == {"hookSpecificOutput"}, "Codex ne documente que hookSpecificOutput sur un refus PreToolUse"
    specific = out["hookSpecificOutput"]
    assert specific["hookEventName"] == "PreToolUse"
    assert specific["permissionDecision"] == "deny"
    reason = specific["permissionDecisionReason"]
    assert reason and reason in proc.stderr
    return reason


# ── refus, demande, autorisation : sous-processus réel ────────────────────────


def test_a_refusal_is_a_documented_deny_with_exit_2(tmp_path: Path) -> None:
    root = _profile(tmp_path, "governed")
    reason = _assert_refusal(_entry(root, "PreToolUse", _codex_pre_tool(root, "Bash", {"command": "rm -rf /"})))
    assert "refusé" in reason


def test_an_ask_is_rendered_as_a_reasoned_refusal_not_an_unsupported_ask(tmp_path: Path) -> None:
    """Profil production : une commande destructrice demande confirmation ; Codex ne sait pas demander."""
    root = _profile(tmp_path, "production")
    proc = _entry(root, "PreToolUse", _codex_pre_tool(root, "Bash", {"command": "git reset --hard"}))
    reason = _assert_refusal(proc)
    assert "ne peut pas la demander" in reason
    assert '"ask"' not in proc.stdout


def test_an_allow_exits_0_without_any_undocumented_field(tmp_path: Path) -> None:
    root = _profile(tmp_path, "governed")
    proc = _entry(root, "PreToolUse", _codex_pre_tool(root, "Bash", {"command": "ls"}))
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert not _PRE_TOOL_UNSUPPORTED & set(out)
    specific = out.get("hookSpecificOutput", {})
    assert specific.get("permissionDecision") in (None, "allow")
    assert specific.get("permissionDecision") != "allow" or "updatedInput" in specific


def _set_task_in_progress(root: Path) -> None:
    board_path = root / "_grimoire/standard/task-board.yaml"
    yaml = YAML()
    data = yaml.load(board_path.read_text(encoding="utf-8"))
    for task in data.get("tasks", []):
        if task.get("task_id") == "bootstrap":
            task["status"] = "in_progress"
    stream = io.StringIO()
    yaml.dump(data, stream)
    board_path.write_text(stream.getvalue(), encoding="utf-8")


def test_stop_with_red_gates_is_a_block_with_reason(tmp_path: Path) -> None:
    root = _profile(tmp_path, "governed")
    _set_task_in_progress(root)
    (root / "_grimoire-output/context/bootstrap/context-bundle.yaml").unlink(missing_ok=True)
    proc = _entry(root, "Stop", _codex_stop(root))
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["decision"] == "block"
    assert "non terminée" in out["reason"]
    assert set(out) == {"decision", "reason"}


# ── apply_patch : chaque fichier touché est jugé ─────────────────────────────


def _patch(*sections: str) -> str:
    return "*** Begin Patch\n" + "".join(sections) + "*** End Patch\n"


def test_apply_patch_is_judged_file_by_file_and_the_strictest_wins(tmp_path: Path) -> None:
    root = _profile(tmp_path, "governed")
    patch = _patch(
        "*** Add File: notes.md\n+bonjour\n",
        "*** Update File: _grimoire/standard/standard-profile.yaml\n@@\n-profile: governed\n+profile: minimal\n",
    )
    rendered, decision, _ = run_hook(
        _codex_pre_tool(root, "apply_patch", {"command": patch}), host_id=HostId.CODEX, project_root=root
    )
    # L'abaissement de profil demande confirmation (`ask`) : sous Codex, un refus motivé.
    assert codex_wire.is_pre_tool_refusal(decision)
    assert "appel 2/2" in decision.reason
    assert rendered["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_a_harmless_patch_passes(tmp_path: Path) -> None:
    root = _profile(tmp_path, "governed")
    patch = _patch("*** Add File: notes.md\n+bonjour\n")
    rendered, decision, _ = run_hook(
        _codex_pre_tool(root, "apply_patch", {"command": patch}), host_id=HostId.CODEX, project_root=root
    )
    assert not codex_wire.is_pre_tool_refusal(decision)
    assert rendered.get("hookSpecificOutput", {}).get("permissionDecision") != "deny"


def test_a_patch_without_a_readable_file_is_refused(tmp_path: Path) -> None:
    root = _profile(tmp_path, "governed")
    proc = _entry(root, "PreToolUse", _codex_pre_tool(root, "apply_patch", {"command": "rm -rf / (pas un correctif)"}))
    assert "aucun en-tête de fichier" in _assert_refusal(proc)


def test_patch_calls_reads_every_documented_header() -> None:
    patch = _patch(
        "*** Add File: a.txt\n+x\n",
        "*** Update File: b.txt\n*** Move to: c d.txt\n@@\n-y\n+z\n",
        "*** Update File: e.txt\n@@\n-u\n+v\n",
        "*** Delete File: d.txt\n",
    )
    parsed = codex_wire.patch_calls("apply_patch", {"command": patch})
    assert parsed is not None
    calls, problem = parsed
    assert problem == ""
    assert [(c.name, c.args) for c in calls] == [
        ("Write", {"file_path": "a.txt", "content": "x"}),
        # Un déplacement : les lignes ajoutées vont à la destination, et la source disparaît.
        ("Edit", {"file_path": "c d.txt", "new_string": "z"}),
        ("Bash", {"command": "mv -- b.txt 'c d.txt'"}),
        ("Edit", {"file_path": "e.txt", "new_string": "v"}),
        # Une suppression : écriture vide et `rm` équivalent.
        ("Write", {"file_path": "d.txt", "content": ""}),
        ("Bash", {"command": "rm -- d.txt"}),
    ]
    assert codex_wire.patch_calls("Bash", {"command": "ls"}) is None
    assert codex_wire.patch_calls("Edit", {"file_path": "a.txt", "new_string": "x"}) is None


_PROFILE = "_grimoire/standard/standard-profile.yaml"


def _judge(root: Path, tool_name: str, tool_input: dict[str, Any]) -> tuple[dict[str, Any], Decision]:
    rendered, decision, _ = run_hook(
        _codex_pre_tool(root, tool_name, tool_input), host_id=HostId.CODEX, project_root=root
    )
    return rendered, decision


def _assert_patch_refused(root: Path, patch: str, tool_name: str = "apply_patch") -> Decision:
    rendered, decision = _judge(root, tool_name, {"command": patch})
    assert codex_wire.is_pre_tool_refusal(decision), decision
    assert rendered["hookSpecificOutput"]["permissionDecision"] == "deny"
    return decision


@pytest.mark.parametrize("profile", ["governed", "production"])
def test_moving_a_file_onto_the_profile_is_refused_like_mv(tmp_path: Path, profile: str) -> None:
    """Un déplacement par apply_patch échappait à la garde qui refuse le même `mv` par Bash."""
    root = _profile(tmp_path, profile)
    assert codex_wire.is_pre_tool_refusal(_judge(root, "Bash", {"command": f"mv notes.md {_PROFILE}"})[1])
    _assert_patch_refused(root, _patch(f"*** Update File: notes.md\n*** Move to: {_PROFILE}\n@@\n+profile: minimal\n"))
    # Même sans contenu qui abaisse le profil : le `mv` équivalent suffit à refuser.
    _assert_patch_refused(root, _patch(f"*** Update File: notes.md\n*** Move to: {_PROFILE}\n"))


@pytest.mark.parametrize("profile", ["governed", "production"])
def test_deleting_the_profile_is_refused_like_rm(tmp_path: Path, profile: str) -> None:
    """Une suppression par apply_patch échappait à la garde qui refuse le même `rm` par Bash."""
    root = _profile(tmp_path, profile)
    assert codex_wire.is_pre_tool_refusal(_judge(root, "Bash", {"command": f"rm -- {_PROFILE}"})[1])
    _assert_patch_refused(root, _patch(f"*** Delete File: {_PROFILE}\n"))


def test_an_indented_header_is_still_judged(tmp_path: Path) -> None:
    root = _profile(tmp_path, "governed")
    _assert_patch_refused(
        root,
        _patch(
            "*** Add File: notes.md\n+x\n",
            f"   *** Update File: {_PROFILE}\n@@\n-profile: governed\n+profile: minimal\n",
        ),
    )


@pytest.mark.parametrize(
    "line",
    ["*** Frobnicate File: _grimoire/standard/standard-profile.yaml", "  *** Rename to: x", "*** Move to: x"],
)
def test_an_unknown_control_line_fails_closed(tmp_path: Path, line: str) -> None:
    """Une ligne `*** ` inconnue (ou un Move hors d'un Update) refuse le correctif."""
    root = _profile(tmp_path, "governed")
    decision = _assert_patch_refused(root, _patch("*** Add File: notes.md\n+x\n", f"{line}\n"))
    assert "apply_patch" in decision.reason


@pytest.mark.parametrize("tool_name", ["Edit", "Write"])
def test_a_patch_carried_by_edit_or_write_is_read_as_a_patch(tmp_path: Path, tool_name: str) -> None:
    """Codex fait correspondre Edit/Write à apply_patch : le correctif porté par `command` est jugé."""
    root = _profile(tmp_path, "governed")
    _assert_patch_refused(root, _patch(f"*** Delete File: {_PROFILE}\n"), tool_name=tool_name)


# ── rendu : seuls les champs documentés par événement ────────────────────────


def _hook(event: HookEvent) -> HookInput:
    return HookInput(event=event, project_root=Path("/p"), tool_name="Bash")


def test_rewrite_is_allow_plus_updated_input() -> None:
    out = render(
        Decision(outcome=Outcome.ALLOW, updated_input={"command": "ls -la"}),
        _hook(HookEvent.PRE_TOOL_USE),
        HostId.CODEX,
    )
    assert out == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": {"command": "ls -la"},
        }
    }


@pytest.mark.parametrize("event", [HookEvent.STOP, HookEvent.SUBAGENT_STOP])
def test_stop_context_without_block_is_a_system_message_only(event: HookEvent) -> None:
    out = render(Decision(context="gates rouges"), _hook(event), HostId.CODEX)
    assert out == {"systemMessage": "gates rouges"}


def test_pre_compact_never_stops_the_compaction() -> None:
    out = render(Decision(context="capsule"), _hook(HookEvent.PRE_COMPACT), HostId.CODEX)
    assert set(out) == {"systemMessage"}


def test_session_start_context_is_additional_context() -> None:
    out = render(Decision(context="directive"), _hook(HookEvent.SESSION_START), HostId.CODEX)
    assert out == {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "directive"}}


def test_import_failure_refuses_with_exit_2_under_codex(capsys: pytest.CaptureFixture[str]) -> None:
    raw = json.dumps({"hook_event_name": "PreToolUse"})
    code = hook_entry.import_failure(["--host", "codex", "--event", "PreToolUse"], raw, "ImportError: x")
    captured = capsys.readouterr()
    assert code == 2
    out = json.loads(captured.out)
    assert set(out) == {"hookSpecificOutput"}
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "garde avant outil en panne" in captured.err
