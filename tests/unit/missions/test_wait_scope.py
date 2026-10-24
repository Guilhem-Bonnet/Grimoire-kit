"""WAIT-SCOPE — une attente porte sur sa tâche, elle ne gèle pas la session (constat du 2026-10-23, kit 3.64.0)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts.events import HookEvent
from grimoire.hosts.runtime import run_hook
from grimoire.missions.lease import LeaseFile, lease_path, read_lease, write_lease
from grimoire.missions.service import TaskService
from grimoire.policies.session_state import load_session_state

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "hosts" / "claude-code"
SESSION = "00000000-0000-4000-8000-0000000000a1"
ACC = "l'attente ne gèle pas la session"
STANDARD = Path("_grimoire/standard")


@pytest.fixture
def governed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "GRIMOIRE_HOST_VERSION"):
        monkeypatch.delenv(var, raising=False)
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    registry = tmp_path / STANDARD / "llm-provider-registry.yaml"
    registry.write_text(registry.read_text(encoding="utf-8").replace("enabled: false", "enabled: true", 1), encoding="utf-8")
    return tmp_path


def _hook(root: Path, event: HookEvent, payload: dict[str, Any]) -> tuple[dict[str, Any], Any]:
    rendered, decision, _ = run_hook(payload, host_id=HostId.CLAUDE_CODE_CLI, event=event, project_root=root)
    return rendered, decision


def _prompt(root: Path) -> None:
    transcript = root / "transcript.jsonl"
    transcript.write_text('{"type":"assistant","version":"2.1.301"}\n', encoding="utf-8")
    payload = {"session_id": SESSION, "hook_event_name": "UserPromptSubmit", "cwd": str(root), "prompt": "go",
               "transcript_path": str(transcript)}
    _hook(root, HookEvent.USER_PROMPT_SUBMIT, payload)


def _bash(root: Path, command: str, *, agent_id: str = "", tool: str = "Bash", tool_input: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.loads((FIXTURES / "01-pretooluse-deny-json.json").read_text(encoding="utf-8"))
    payload = dict(data["payloads"]["PreToolUse"])
    payload.update(cwd=str(root), session_id=SESSION, tool_name=tool)
    payload["tool_input"] = tool_input if tool_input is not None else {**payload["tool_input"], "command": command}
    if agent_id:
        payload["agent_id"] = agent_id
        payload["agent_type"] = "general-purpose"
    return payload


def _verdict(root: Path, payload: dict[str, Any]) -> tuple[str, str]:
    rendered, _ = _hook(root, HookEvent.PRE_TOOL_USE, payload)
    out = rendered["hookSpecificOutput"]
    return str(out["permissionDecision"]), str(out.get("permissionDecisionReason", ""))


def _ready(service: TaskService, title: str) -> str:
    tid = service.add(title, (ACC,), owner="guilhem", actor="claude", ready=True).task.id
    service.context(tid)
    return tid


def _park(root: Path, how: str) -> tuple[str, str]:
    """La séquence vécue : la session tient T, la gare (question humaine ou ci), le bail dépasse le TTL.

    Rend ``(T, autre)`` ; ``autre`` est une seconde tâche prête, sans rapport.
    """
    service = TaskService(root)
    tid = _ready(service, "Tâche de la session")
    other = _ready(service, "Autre tâche")
    service.claim(tid, "claude", host="local")
    _prompt(root)
    assert load_session_state(root, SESSION, now_iso="").lease_epoch == 1
    if how == "human":
        service.ask(tid, "Quelle option ?", "claude", kind="choice", options=("a", "b"))
    else:
        service.wait(tid, how, "claude")
    path = lease_path(service.ledger_root, tid)
    cached = read_lease(path)
    assert cached is not None and cached.waiting
    write_lease(path, LeaseFile(tid, cached.epoch, (datetime.now(UTC) - timedelta(hours=2)).isoformat(), cached.ttl_seconds, "", waiting=True))
    return tid, other


# ── 1. la question humaine ne gèle pas la session ────────────────────────────


@pytest.mark.parametrize("how", ["human", "ci"])
@pytest.mark.parametrize(
    "template",
    ["grimoire task show {other}", "grimoire task claim {other}", "grimoire task attach {other}",
     "grimoire task list", "grimoire cockpit serve", "/opt/venv/bin/grimoire task add 'x; y' --accept z"],
)
def test_parked_session_can_still_use_grimoire_task_on_any_task_and_serve_the_cockpit(governed: Path, how: str, template: str) -> None:
    tid, other = _park(governed, how)
    decision, reason = _verdict(governed, _bash(governed, template.format(other=other, tid=tid)))
    assert decision == "allow", reason


@pytest.mark.parametrize("how", ["human", "ci"])
def test_parked_session_unrelated_write_falls_back_to_the_no_task_guidance(governed: Path, how: str) -> None:
    """Pas de refus sans issue : le message est celui du repli « sans tâche » (prendre ou rattacher), pas « reprends T »."""
    _park(governed, how)
    decision, reason = _verdict(governed, _bash(governed, "touch EXECUTED.marker"))
    assert "seules la reprise et la lecture de cette tâche" not in reason, reason
    if decision != "allow":  # le profil gouverné peut refuser une écriture sans tâche : alors il dit quoi faire
        assert "task claim" in reason or "task attach" in reason, reason
    assert "garée" not in reason


def test_the_human_question_is_still_lifted_by_the_human_only(governed: Path) -> None:
    from grimoire.core.exceptions import GrimoireMissionError

    tid, _ = _park(governed, "human")
    with pytest.raises(GrimoireMissionError, match="seul un humain la lève"):
        TaskService(governed).resume(tid, "claude", epoch=1)
    assert _verdict(governed, _bash(governed, f"grimoire task resume {tid} --epoch 1"))[0] == "allow"  # le hook ne juge pas le ledger


# ── 2. un sous-agent n'est pas gelé par la tâche garée du coordinateur ───────


@pytest.mark.parametrize("how", ["ci", "quota", "paused"])
def test_subagent_is_not_frozen_by_the_coordinators_parked_task(governed: Path, how: str) -> None:
    _park(governed, how)
    decision, reason = _verdict(governed, _bash(governed, "touch EXECUTED.marker", agent_id="agent-1"))
    assert decision == "allow", reason
    assert "seules la reprise et la lecture de cette tâche" not in reason, reason
    assert "garée" not in reason, reason



def test_parking_warns_when_subagents_of_the_session_are_still_working(governed: Path) -> None:
    service = TaskService(governed)
    coordinator = _ready(service, "Coordinateur")
    child = _ready(service, "Sous-tâche")
    service.claim(coordinator, "claude", host="local")
    service.attach_session(coordinator, SESSION, session_host="claude-code-cli")
    assert service.parking_warnings(coordinator) == ()  # personne d'autre : rien à dire
    service.claim(child, "claude-sub", host="local")
    service.attach_session(child, SESSION, session_host="claude-code-cli", agent_id="agent-1")
    [warning] = service.parking_warnings(coordinator)
    assert child in warning and "ne les arrête pas" in warning


def test_mcp_wait_returns_the_subagent_warning(governed: Path) -> None:
    from grimoire.mcp.server import task_update

    service = TaskService(governed)
    coordinator = _ready(service, "Coordinateur")
    child = _ready(service, "Sous-tâche")
    service.claim(coordinator, "claude", host="local")
    service.attach_session(coordinator, SESSION, session_host="claude-code-cli")
    service.claim(child, "claude-sub", host="local")
    service.attach_session(child, SESSION, session_host="claude-code-cli", agent_id="agent-1")
    result = json.loads(task_update(coordinator, "wait", to="ci", actor="claude", project_path=str(governed)))
    assert result["status"] == "waiting" and child in result["warnings"][0]


# ── 3. la commande de réponse proposée est exécutable telle quelle ───────────


def test_refusals_propose_the_exact_answer_command(governed: Path) -> None:
    from grimoire.core.exceptions import GrimoireMissionError

    tid, _ = _park(governed, "human")
    question = TaskService(governed).require(tid).waiting.question  # type: ignore[union-attr]
    assert question is not None
    with pytest.raises(GrimoireMissionError) as refused:
        TaskService(governed).resume(tid, "claude", epoch=1)
    message = str(refused.value)
    assert f'grimoire task answer {tid} "<réponse>" --question {question.id}' in message, message
    assert "binaire du projet" in message
    assert f"answer {tid} {question.id}" not in message


def test_the_proposed_answer_command_parses_on_the_real_cli(governed: Path) -> None:
    """La forme proposée, exécutée telle quelle (réponse substituée), est acceptée par la CLI."""
    from typer.testing import CliRunner

    from grimoire.cli.app import app
    from grimoire.missions.waiting import answer_command

    tid, _ = _park(governed, "human")
    question = TaskService(governed).require(tid).waiting.question  # type: ignore[union-attr]
    assert question is not None
    words = answer_command(tid, question.id).replace('"<réponse>"', "a").split()[1:]
    result = CliRunner().invoke(app, [*words, "--project-root", str(governed), "--actor", "guilhem"])
    assert "No such option" not in result.output and "Missing argument" not in result.output, result.output


# ── 4. MCP : resume sans epoch pour la session qui tient le claim ────────────


def test_mcp_resume_takes_the_epoch_of_the_claim_of_the_same_session(governed: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.mcp.server import task_update

    service = TaskService(governed)
    tid = _ready(service, "Reprise MCP")
    service.claim(tid, "claude", host="local")
    service.attach_session(tid, SESSION, session_host="claude-code-cli")
    service.wait(tid, "ci", "claude")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    resumed = json.loads(task_update(tid, "resume", project_path=str(governed)))  # acteur par défaut : mcp-agent
    assert resumed["status"] == "running", resumed


def test_mcp_resume_of_another_session_keeps_the_named_error(governed: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.mcp.server import task_update

    service = TaskService(governed)
    tid = _ready(service, "Reprise MCP")
    service.claim(tid, "claude", host="local")
    service.attach_session(tid, SESSION, session_host="claude-code-cli")
    service.wait(tid, "ci", "claude")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "00000000-0000-4000-8000-0000000000b2")
    refused = task_update(tid, "resume", project_path=str(governed))
    text = refused if isinstance(refused, str) else "".join(str(getattr(p, "text", "")) for p in refused.content)
    assert "aucun epoch à présenter" in text, text
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    refused = task_update(tid, "resume", project_path=str(governed))
    text = refused if isinstance(refused, str) else "".join(str(getattr(p, "text", "")) for p in refused.content)
    assert "aucun epoch à présenter" in text, text
