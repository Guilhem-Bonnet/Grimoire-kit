"""Gate Stop et modèle des sous-agents rendus selon le harness Copilot (#1280, lots P1-3 et P1-4).

Références : harness Copilot — https://docs.github.com/en/copilot/reference/hooks-reference
(``agentStop``/``subagentStop`` : ``decision``/``reason`` au premier niveau,
8 refus consécutifs au plus ; ``preToolUse`` : ``modifiedArgs``) ; harness Local —
https://code.visualstudio.com/docs/agents/reference/hooks-reference (``Stop`` :
``hookSpecificOutput.decision`` ; ``SubagentStop`` : ``decision`` au premier
niveau ; ``PreToolUse`` : ``hookSpecificOutput.updatedInput``).

Les charges camelCase rejouent les fixtures relevées sur traces réelles
(``tests/fixtures/hosts/copilot/copilot-harness/``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.cli.doctor_hooks import check_copilot_guard_timeout
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts import copilot_wire
from grimoire.hosts.collect import build_surface
from grimoire.hosts.decisions import Decision, HookInput, Outcome
from grimoire.hosts.decisions import dispatch_model as dm
from grimoire.hosts.decisions.done_gate import _STOP_CAP_DEFAULT, _STOP_CAPS
from grimoire.hosts.emitters import emitter_for
from grimoire.hosts.emitters.base import apply_plan
from grimoire.hosts.events import HookEvent
from grimoire.hosts.runtime import render, run_hook

pytestmark = pytest.mark.covers("host:github-copilot", level="integration")

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "hosts" / "copilot" / "copilot-harness"
DECISION = "grimoire.dispatch-model"
REASON = "[Grimoire] Tâche T non terminée : gates rouges."


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    home = str(tmp_path_factory.mktemp("home"))
    monkeypatch.setenv("HOME", home)
    monkeypatch.setenv("USERPROFILE", home)
    for var in (
        "GRIMOIRE_TASK_ID",
        "GRIMOIRE_ACTOR",
        "GRIMOIRE_SESSION_ID",
        "CLAUDE_CODE_SESSION_ID",
        "CLAUDE_PROJECT_DIR",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "_grimoire").mkdir()
    return tmp_path


def _fixture_input(name: str) -> dict[str, Any]:
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return dict(data["payloads"][0]["input"])


# ── P1-3 : refus de clôture ─────────────────────────────────────────────────

_CAMEL_STOP = {"sessionId": "s", "cwd": "/p", "stopHookActive": False, "hookEventName": "agentStop"}
_SNAKE_STOP = {"session_id": "s", "cwd": "/p", "stop_hook_active": False, "hook_event_name": "Stop"}


def _stop(event: HookEvent, raw: dict[str, Any], *, blocks: int | None = None) -> tuple[Decision, HookInput]:
    detail: dict[str, Any] = {"ok": False}
    if blocks is not None:
        detail["stop_signature"] = {"release": False, "blocks": blocks, "cap": 8}
    decision = Decision(outcome=Outcome.BLOCK, reason=REASON, detail=detail)
    return decision, HookInput(event=event, project_root=Path("/p"), raw=raw)


def test_copilot_harness_stop_block_is_top_level_only() -> None:
    out = render(*_stop(HookEvent.STOP, _CAMEL_STOP), HostId.GITHUB_COPILOT)
    assert out == {"decision": "block", "reason": REASON}


@pytest.mark.parametrize("raw", [_SNAKE_STOP, {}], ids=["snake", "indecidable"])
def test_snake_or_unknown_stop_block_carries_both_shapes(raw: dict[str, Any]) -> None:
    out = render(*_stop(HookEvent.STOP, raw), HostId.GITHUB_COPILOT)
    # Harness Copilot : premier niveau. Harness Local : hookSpecificOutput. Même verdict.
    assert out["decision"] == "block" and out["reason"] == REASON
    assert out["hookSpecificOutput"] == {"hookEventName": "Stop", "decision": "block", "reason": REASON}


@pytest.mark.parametrize("raw", [_CAMEL_STOP, _SNAKE_STOP, {}], ids=["camel", "snake", "indecidable"])
def test_subagent_stop_block_is_top_level_for_both_harnesses(raw: dict[str, Any]) -> None:
    out = render(*_stop(HookEvent.SUBAGENT_STOP, raw), HostId.GITHUB_COPILOT)
    assert out == {"decision": "block", "reason": REASON}


def test_claude_code_stop_block_is_unchanged() -> None:
    for raw in (_CAMEL_STOP, _SNAKE_STOP):
        assert render(*_stop(HookEvent.STOP, raw), HostId.CLAUDE_CODE_CLI) == {"decision": "block", "reason": REASON}


def test_the_eighth_consecutive_block_is_still_emitted() -> None:
    out = render(*_stop(HookEvent.STOP, _CAMEL_STOP, blocks=copilot_wire.STOP_BLOCK_LIMIT), HostId.GITHUB_COPILOT)
    assert out["decision"] == "block"


def test_a_block_past_the_copilot_limit_is_not_emitted_and_says_so() -> None:
    out = render(*_stop(HookEvent.STOP, _SNAKE_STOP, blocks=copilot_wire.STOP_BLOCK_LIMIT + 1), HostId.GITHUB_COPILOT)
    assert "decision" not in out and "hookSpecificOutput" not in out
    assert "limite de 8 refus" in out["systemMessage"] and "gates toujours rouges" in out["systemMessage"]


def test_the_kit_stop_chain_never_exceeds_the_copilot_limit() -> None:
    # Le (cap+1)-ième Stop passe toujours (done_gate.stop_step) : cap ≤ 8 tient la borne sur les deux harness.
    assert max(*_STOP_CAPS.values(), _STOP_CAP_DEFAULT) <= copilot_wire.STOP_BLOCK_LIMIT


def test_camel_stop_hook_active_is_read() -> None:
    from grimoire.hosts.runtime import normalize_input

    hook = normalize_input(
        {**_CAMEL_STOP, "stopHookActive": True}, project_root=Path("/p"), host_id=HostId.GITHUB_COPILOT
    )
    assert hook.event is HookEvent.STOP and hook.stop_active


# ── P1-4 : modèle imposé aux sous-agents ────────────────────────────────────


def _set_models(root: Path, body: str) -> None:
    path = root / dm.POLICY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


_MAPPED = (
    "dispatch:\n  impose_model: on\n  copilot_models:\n    haiku: claude-haiku-4.5\n    sonnet: claude-sonnet-4.5\n"
)
_RESEARCH = {"description": "Find hook emitters", "prompt": "Locate the code."}


def _camel_task(root: Path, args: dict[str, Any], *, name: str = "task") -> dict[str, Any]:
    payload = _fixture_input("pretooluse-single.json")
    payload["cwd"] = str(root)
    payload["toolCalls"] = [{"id": "toolu_REDACTED_09", "name": name, "args": json.dumps(args)}]
    return payload


def _snake(root: Path, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"hook_event_name": "PreToolUse", "session_id": "s", "cwd": str(root), "tool_name": tool, "tool_input": args}


def _dispatch(
    root: Path, payload: dict[str, Any], host: HostId = HostId.GITHUB_COPILOT
) -> tuple[dict[str, Any], Decision]:
    rendered, decision, _ = run_hook(
        payload, host_id=host, event=HookEvent.PRE_TOOL_USE, project_root=root, decision_id=DECISION
    )
    return rendered, decision


def test_copilot_harness_rewrite_is_modified_args_without_permission(project: Path) -> None:
    _set_models(project, _MAPPED)
    rendered, decision = _dispatch(project, _camel_task(project, _RESEARCH))
    assert rendered["modifiedArgs"] == {**_RESEARCH, "model": "claude-haiku-4.5"}
    # Consultatif : aucune permission affirmée, nulle part.
    assert "permissionDecision" not in rendered
    specific = rendered["hookSpecificOutput"]
    assert "permissionDecision" not in specific and "updatedInput" not in specific
    assert "claude-haiku-4.5" in specific["additionalContext"]
    assert decision.detail["imposed_model"] == "claude-haiku-4.5"
    assert decision.detail["recommended_model"] == "haiku"


@pytest.mark.parametrize("tool", ["Agent", "Task", "runSubagent", "task"])
def test_snake_rewrite_carries_both_fields(project: Path, tool: str) -> None:
    _set_models(project, _MAPPED)
    rendered, _ = _dispatch(project, _snake(project, tool, _RESEARCH))
    expected = {**_RESEARCH, "model": "claude-haiku-4.5"}
    assert rendered["modifiedArgs"] == expected
    assert rendered["hookSpecificOutput"]["updatedInput"] == expected
    assert "permissionDecision" not in rendered["hookSpecificOutput"]


def test_without_a_copilot_model_name_nothing_is_rewritten(project: Path) -> None:
    rendered, decision = _dispatch(project, _camel_task(project, _RESEARCH))
    assert "modifiedArgs" not in rendered
    assert "updatedInput" not in rendered.get("hookSpecificOutput", {})
    context = rendered["hookSpecificOutput"]["additionalContext"]
    assert "palier haiku recommandé" in context and "dispatch.copilot_models.haiku" in context
    assert "imposed_model" not in decision.detail


def test_a_batch_is_never_rewritten_only_advised(project: Path) -> None:
    _set_models(project, _MAPPED)
    payload = _fixture_input("pretooluse-batch.json")
    payload["cwd"] = str(project)
    payload["toolCalls"] = [
        *payload["toolCalls"][:1],
        {"id": "toolu_REDACTED_19", "name": "task", "args": json.dumps(_RESEARCH)},
    ]
    rendered, decision = _dispatch(project, payload)
    assert "modifiedArgs" not in rendered and "permissionDecision" not in rendered
    specific = rendered["hookSpecificOutput"]
    assert "updatedInput" not in specific and "permissionDecision" not in specific
    assert "appel 2/2 d'un lot (task)" in specific["additionalContext"]
    assert "model=claude-haiku-4.5 recommandé" in specific["additionalContext"]
    assert decision.updated_input is None
    assert decision.detail["batch_recommended"] == [{"index": 2, "tool": "task", "model": "claude-haiku-4.5"}]


def test_a_batch_without_delegation_abstains(project: Path) -> None:
    payload = _fixture_input("pretooluse-batch.json")
    payload["cwd"] = str(project)
    rendered, decision = _dispatch(project, payload)
    assert rendered == {} and decision.abstain


@pytest.mark.parametrize(
    "args",
    [
        {**_RESEARCH, "model": "gpt-5"},  # model explicite
        {"description": "Audit de sécurité", "prompt": "x"},  # jugement fort
        {"description": "Corriger le bug", "prompt": "x"},  # écriture
        {**_RESEARCH, "agent_type": "plugin:reviewer"},  # agent de plugin
    ],
    ids=["model-explicite", "audit", "ecriture", "plugin"],
)
def test_same_exclusions_as_claude_code(project: Path, args: dict[str, Any]) -> None:
    _set_models(project, _MAPPED)
    rendered, decision = _dispatch(project, _camel_task(project, args))
    assert rendered == {} and decision.abstain


def test_a_vscode_persona_that_declares_its_model_keeps_it(project: Path) -> None:
    _set_models(project, _MAPPED)
    agents = project / ".github" / "agents"
    agents.mkdir(parents=True)
    (agents / "explorer.agent.md").write_text("---\nmodel: GPT-5 mini\n---\nCorps\n", encoding="utf-8")
    rendered, _ = _dispatch(project, _camel_task(project, {**_RESEARCH, "agent_type": "explorer"}))
    assert rendered == {}


def test_policy_off_disables_copilot_too(project: Path) -> None:
    _set_models(project, _MAPPED.replace("impose_model: on", "impose_model: off"))
    rendered, _ = _dispatch(project, _camel_task(project, _RESEARCH))
    assert rendered == {}


def test_a_claimed_v2_task_imposes_nothing_on_copilot(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_models(project, _MAPPED)
    monkeypatch.setattr(dm, "_class_model", lambda _hook: (None, {"task_id": "T", "task_class": "V2"}))
    rendered, _ = _dispatch(project, _camel_task(project, _RESEARCH))
    assert rendered == {}


def test_claude_code_ignores_copilot_tool_names_and_keeps_its_rendering(project: Path) -> None:
    _set_models(project, _MAPPED)
    rendered, _ = _dispatch(project, _snake(project, "task", _RESEARCH), HostId.CLAUDE_CODE_CLI)
    assert rendered == {}
    rendered, _ = _dispatch(project, _snake(project, "Agent", _RESEARCH), HostId.CLAUDE_CODE_CLI)
    specific = rendered["hookSpecificOutput"]
    assert specific["permissionDecision"] == "allow"
    assert specific["updatedInput"] == {**_RESEARCH, "model": "haiku"}  # alias Claude, pas le nom Copilot
    assert "modifiedArgs" not in rendered


# ── Émission : entrée consultative du fichier PreToolUse ────────────────────


def _pre_tool_file(root: Path) -> list[dict[str, Any]]:
    plan = emitter_for(HostId.GITHUB_COPILOT).plan(build_surface(root), root)
    (pre,) = [f for f in plan.files if f.relpath.as_posix() == ".github/hooks/grimoire-pre-tool-use.json"]
    return json.loads(pre.content)["hooks"]["PreToolUse"]


@pytest.fixture
def governed(tmp_path: Path) -> Path:
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    return tmp_path


def test_the_dispatch_entry_comes_after_the_guard_and_is_short(governed: Path) -> None:
    named, unknown, dispatch = _pre_tool_file(governed)
    for guard in (named, unknown):
        assert "--decision" not in guard["command"]
        assert guard["timeoutSec"] == 30
    assert dispatch["matcher"] == "Agent|Task|task"
    assert dispatch["timeoutSec"] == 3
    for key in ("command", "bash", "powershell"):
        assert dispatch[key].endswith("--event PreToolUse --decision grimoire.dispatch-model")


def test_the_plan_declares_the_dispatch_degradation(governed: Path) -> None:
    plan = emitter_for(HostId.GITHUB_COPILOT).plan(build_surface(governed), governed)
    (gap,) = [d for d in plan.degradations if d.surface == "dispatch model"]
    assert "dispatch.copilot_models" in gap.fallback


def test_doctor_does_not_mistake_the_dispatch_entry_for_a_short_guard(governed: Path) -> None:
    apply_plan(emitter_for(HostId.GITHUB_COPILOT).plan(build_surface(governed), governed), governed)
    checks = check_copilot_guard_timeout(governed)
    assert checks and all(c.passed for c in checks), [c.detail for c in checks]


def test_claude_code_ignores_a_copilot_agent_file_model(project: Path) -> None:
    """R1 : sous Claude Code, `.github/agents/*.agent.md` n'est pas lu (Claude ne charge pas ce fichier)."""
    agents = project / ".github" / "agents"
    agents.mkdir(parents=True)
    (agents / "explorer.agent.md").write_text("---\nmodel: GPT-5 mini\n---\nCorps\n", encoding="utf-8")
    rendered, _ = _dispatch(
        project, _snake(project, "Agent", {**_RESEARCH, "subagent_type": "explorer"}), HostId.CLAUDE_CODE_CLI
    )
    assert rendered["hookSpecificOutput"]["updatedInput"]["model"] == "haiku"


def test_an_unreadable_batch_is_never_rewritten(project: Path) -> None:
    """R2 : un lot dont un appel est illisible ne rend aucune réécriture, même si un appel `task` y est lisible."""
    _set_models(project, _MAPPED)
    payload = _camel_task(project, _RESEARCH)
    payload["toolCalls"].append({"id": "toolu_REDACTED_10", "args": "{}"})  # appel sans nom
    rendered, decision = _dispatch(project, payload)
    assert rendered == {} and decision.abstain
