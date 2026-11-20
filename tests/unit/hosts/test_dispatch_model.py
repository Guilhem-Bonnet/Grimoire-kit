"""Leviers 2-3 de l'audit de coût — un appel ``Agent`` sans ``model`` reçoit le modèle de la politique.

La « Politique de dispatch » injectée au ``SessionStart`` n'était que du texte :
``model=`` omis, le sous-agent tournait sur le modèle de la session. Le hook
consultatif ``grimoire.dispatch-model`` (matcher ``Agent|Task``) rend la règle
mécanique, sans jamais toucher un ``model`` explicite, un fork ou une politique à
``off``, et sans jamais bloquer : toute panne laisse passer l'appel tel quel.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Any

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts import hook_entry
from grimoire.hosts.collect import build_surface
from grimoire.hosts.decisions import HookInput, run_decision
from grimoire.hosts.decisions import dispatch_model as dm
from grimoire.hosts.emitters import emitter_for
from grimoire.hosts.events import HookEvent
from grimoire.hosts.runtime import main as runtime_main
from grimoire.hosts.runtime import render, run_hook
from grimoire.missions.dispatch_advice import dispatch_advice
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService

pytestmark = pytest.mark.covers("host:claude-code-cli", level="integration")

DECISION = "grimoire.dispatch-model"
SESSION = "aaaaaaaa-0000-4000-8000-0000000000l3"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    # Les définitions d'agents de l'utilisateur sont lues sous HOME : jamais celui du poste.
    home = str(tmp_path_factory.mktemp("home"))
    monkeypatch.setenv("HOME", home)
    monkeypatch.setenv("USERPROFILE", home)  # Path.home() lit USERPROFILE sous Windows
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


def _agent_payload(root: Path, tool_input: Any, *, tool: str = "Agent", session: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "hook_event_name": "PreToolUse",
        "cwd": str(root),
        "tool_name": tool,
        "tool_input": tool_input,
    }
    if session:
        payload["session_id"] = session
    return payload


def _run(root: Path, tool_input: Any, **kwargs: Any) -> tuple[dict[str, Any], Any]:
    rendered, decision, _ = run_hook(
        _agent_payload(root, tool_input, **kwargs),
        host_id=HostId.CLAUDE_CODE_CLI,
        project_root=root,
        decision_id=DECISION,
    )
    return rendered, decision


def _set_policy(root: Path, text: str) -> None:
    path = root / dm.POLICY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ── model omis : imposé ─────────────────────────────────────────────────────


def test_a_research_call_without_model_gets_haiku_with_the_full_input(project: Path) -> None:
    tool_input = {"description": "Find the emitters", "prompt": "Locate the code.", "subagent_type": "general-purpose"}
    rendered, decision = _run(project, tool_input)
    specific = rendered["hookSpecificOutput"]
    assert specific["permissionDecision"] == "allow"
    # L'hôte remplace l'entrée : chaque champ d'origine doit y être.
    assert specific["updatedInput"] == {**tool_input, "model": "haiku"}
    assert "model=haiku imposé" in specific["additionalContext"]
    assert decision.detail["imposed_model"] == "haiku"
    assert decision.detail["dispatch_source"] == "heuristic"


def test_a_review_call_without_model_gets_sonnet(project: Path) -> None:
    rendered, _ = _run(
        project, {"description": "Revue du diff", "prompt": "Relis.", "subagent_type": "general-purpose"}
    )
    assert rendered["hookSpecificOutput"]["updatedInput"]["model"] == "sonnet"


def test_the_older_task_tool_name_is_handled_too(project: Path) -> None:
    rendered, _ = _run(project, {"description": "Search the docs", "prompt": "x"}, tool="Task")
    assert rendered["hookSpecificOutput"]["updatedInput"]["model"] == "haiku"


def test_the_class_of_the_claimed_task_wins_over_the_heuristic(tmp_path: Path) -> None:
    """Tâche réclamée : le ``recommended_model`` de sa classe, même quand l'heuristique dirait autre chose."""
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    registry = tmp_path / "_grimoire/standard/llm-provider-registry.yaml"
    registry.write_text(
        registry.read_text(encoding="utf-8").replace("enabled: false", "enabled: true", 1), encoding="utf-8"
    )
    service = TaskService(tmp_path)
    tid = service.add("Travail", ("la suite de tests passe",), owner="guilhem", actor="claude", ready=True).task.id
    service.context(tid)
    service.claim(tid, "claude", host="claude-code-cli")
    service.transition(tid, TaskState.RUNNING, "claude")
    service.attach_session(tid, SESSION, session_host="claude-code-cli", actor="claude")

    advice = dispatch_advice(service.require(tid))
    expected = advice["recommended_model"]
    assert expected  # V0 ou V1 : un modèle existe
    # La description contredit la classe : l'heuristique dirait l'autre palier.
    description = "Revue du diff" if expected == "haiku" else "Find the emitters"
    assert dm.heuristic_model(description) not in (None, expected)

    rendered, decision = _run(tmp_path, {"description": description, "prompt": "x"}, session=SESSION)
    assert rendered["hookSpecificOutput"]["updatedInput"]["model"] == expected
    assert decision.detail["dispatch_source"] == "class"
    assert decision.detail["task_class"] == advice["verifiability"]["class"]
    assert decision.detail["task_id"] == tid


def test_a_claimed_v2_task_imposes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """V2 : la politique veut le modèle de la session, l'heuristique n'est pas tentée."""
    monkeypatch.setattr(dm, "_class_model", lambda _hook: (None, {"task_id": "GAO-x-001", "task_class": "V2"}))
    rendered, decision = _run(tmp_path, {"description": "Find the emitters", "prompt": "x"})
    assert rendered == {}
    assert decision.abstain


def test_a_claimed_v2_task_from_the_ledger_imposes_nothing(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    registry = tmp_path / "_grimoire/standard/llm-provider-registry.yaml"
    registry.write_text(
        registry.read_text(encoding="utf-8").replace("enabled: false", "enabled: true", 1), encoding="utf-8"
    )
    service = TaskService(tmp_path)
    tid = service.add("Travail", ("le code est propre",), owner="guilhem", actor="claude", ready=True).task.id
    assert dispatch_advice(service.require(tid))["verifiability"]["class"] == "V2"
    service.context(tid)
    service.claim(tid, "claude", host="claude-code-cli")
    service.transition(tid, TaskState.RUNNING, "claude")
    service.attach_session(tid, SESSION, session_host="claude-code-cli", actor="claude")
    rendered, decision = _run(tmp_path, {"description": "Find the emitters", "prompt": "x"}, session=SESSION)
    assert rendered == {}
    assert decision.abstain


def test_an_unrecognised_or_writing_call_is_left_alone(project: Path) -> None:
    for description in ("Implémente le lot B", "Corrige le test rouge", "Lot 4", ""):
        rendered, decision = _run(
            project, {"description": description, "prompt": "", "subagent_type": "general-purpose"}
        )
        assert rendered == {}, description
        assert decision.abstain


# ── intact ─────────────────────────────────────────────────────────────────


def test_an_explicit_model_is_never_replaced(project: Path) -> None:
    rendered, decision = _run(project, {"description": "Find the emitters", "prompt": "x", "model": "opus"})
    assert rendered == {}
    assert decision.abstain and decision.updated_input is None


def test_a_fork_is_left_intact(project: Path) -> None:
    rendered, _ = _run(project, {"description": "Find the emitters", "prompt": "x", "subagent_type": "fork"})
    assert rendered == {}


def test_a_persona_that_declares_its_model_keeps_it(project: Path) -> None:
    agents = project / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "architect.md").write_text("---\nname: architect\nmodel: opus\n---\nCorps\n", encoding="utf-8")
    (agents / "helper.md").write_text("---\nname: helper\nmodel: 'inherit'\n---\nCorps\n", encoding="utf-8")
    assert _run(project, {"description": "Analyse the diff", "prompt": "x", "subagent_type": "architect"})[0] == {}
    rendered, _ = _run(project, {"description": "Analyse the diff", "prompt": "x", "subagent_type": "helper"})
    assert rendered["hookSpecificOutput"]["updatedInput"]["model"] == "sonnet"


def test_a_user_level_persona_that_declares_its_model_keeps_it(project: Path, tmp_path: Path) -> None:
    home = Path(os.environ["HOME"])
    agents = home / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "reviewer.md").write_text("---\nname: reviewer\nmodel: sonnet\n---\nCorps\n", encoding="utf-8")
    rendered, decision = _run(project, {"description": "Find the emitters", "prompt": "x", "subagent_type": "reviewer"})
    assert rendered == {}
    assert decision.abstain


def test_a_plugin_agent_is_left_intact(project: Path) -> None:
    rendered, _ = _run(project, {"description": "Find the emitters", "prompt": "x", "subagent_type": "myplugin:finder"})
    assert rendered == {}


@pytest.mark.parametrize("value", ["off", "false", "no", "désactivé"])
def test_policy_off_leaves_the_call_intact(project: Path, value: str) -> None:
    _set_policy(project, f"dispatch:\n  impose_model: {value}\n")
    rendered, _ = _run(project, {"description": "Find the emitters", "prompt": "x"})
    assert rendered == {}


def test_policy_on_absent_or_without_block_imposes(project: Path) -> None:
    assert dm.impose_model_enabled(project)
    _set_policy(project, "wip:\n  limit_global: 2\n")
    assert dm.impose_model_enabled(project)
    _set_policy(project, "dispatch:\n  impose_model: on\n")
    assert dm.impose_model_enabled(project)
    _set_policy(project, "dispatch:\n  impose_model: true\n")
    assert dm.impose_model_enabled(project)


def test_an_unreadable_policy_imposes_nothing(project: Path) -> None:
    _set_policy(project, "dispatch: [unclosed\n")
    assert not dm.impose_model_enabled(project)


def test_other_tools_are_ignored(project: Path) -> None:
    rendered, _ = _run(project, {"command": "ls"}, tool="Bash")
    assert rendered == {}


# ── entrées malformées, pannes : jamais de blocage ─────────────────────────


@pytest.mark.parametrize(
    "tool_input",
    [None, [], "texte", {}, {"description": 42, "prompt": ["x"]}, {"subagent_type": {"a": 1}, "description": ["Find"]}],
)
def test_malformed_input_never_crashes_nor_blocks(project: Path, tool_input: Any) -> None:
    rendered, decision = _run(project, tool_input)
    assert rendered == {}
    assert decision.abstain


def test_a_malformed_subagent_type_is_ignored_not_fatal(project: Path) -> None:
    rendered, _ = _run(project, {"subagent_type": {"a": 1}, "description": "Find the emitters"})
    assert rendered["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert rendered["hookSpecificOutput"]["updatedInput"]["model"] == "haiku"


def test_an_internal_error_abstains(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_root: Path) -> bool:
        raise RuntimeError("panne simulée")

    monkeypatch.setattr(dm, "impose_model_enabled", boom)
    rendered, decision = _run(project, {"description": "Find the emitters", "prompt": "x"})
    assert rendered == {}
    assert decision.abstain
    assert "panne simulée" in decision.detail["error"]


def test_run_decision_turns_a_crash_into_an_abstention_not_an_ask(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_hook: HookInput) -> None:
        raise RuntimeError("panne simulée")

    from grimoire.hosts import decisions

    monkeypatch.setitem(decisions.DECISIONS, DECISION, boom)
    hook = HookInput(event=HookEvent.PRE_TOOL_USE, project_root=project, tool_name="Agent", tool_input={"prompt": "x"})
    decision = run_decision(DECISION, hook)
    assert decision.abstain
    assert render(decision, hook, HostId.CLAUDE_CODE_CLI) == {}


def test_the_entry_point_lets_the_call_through_when_the_runtime_crashes(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("panne simulée")

    monkeypatch.setattr("grimoire.hosts.runtime.run_hook", boom)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_agent_payload(project, {"prompt": "x"}))))
    code = runtime_main(["--host", "claude", "--event", "PreToolUse", "--decision", DECISION])
    assert code == 0
    assert json.loads(capsys.readouterr().out) == {}


def test_an_import_failure_lets_the_call_through(capsys: pytest.CaptureFixture[str]) -> None:
    code = hook_entry.import_failure(
        ["--host", "claude", "--event", "PreToolUse", "--decision", DECISION], "{}", "ImportError: x"
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out) == {}


# ── heuristique ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("description", "prompt", "expected"),
    [
        ("Find the emitters", "", "haiku"),
        ("Lire les fichiers de config", "", "haiku"),
        ("Garde : Claude-only sous Copilot, grep/glob", "", None),
        ("Relecture adversariale PR 12", "", None),
        ("Corriger le hook", "", None),
        ("Cherche la faille", "", None),
        ("CI : lire les logs", "", None),
        ("Mesure du coût", "", "haiku"),
        ("Lis le changelog", "", "haiku"),
        ("Audit sécurité", "", None),
        ("Revue d'architecture", "", None),
        ("Plan de migration", "", None),
        ("Analyse du design", "", None),
        ("Revue du diff", "", "sonnet"),
        ("Analyse puis liste", "", "sonnet"),
        ("Cherche et corrige", "", None),
        ("", "Recherche les appels de la fonction", "haiku"),
        ("Lot 4", "", None),
    ],
)
def test_heuristic(description: str, prompt: str, expected: str | None) -> None:
    assert dm.heuristic_model(description, prompt) == expected


SENSITIVE = (
    "Garde : Claude-only sous Copilot, grep/glob",
    "Relecture adversariale PR 1293",
    "Corriger le hook",
    "Chercher la faille",
    "CI : lire les logs",
)


@pytest.mark.parametrize("description", SENSITIVE)
@pytest.mark.parametrize("host", [HostId.CLAUDE_CODE_CLI, HostId.GITHUB_COPILOT])
def test_a_sensitive_description_abstains_under_both_hosts(project: Path, description: str, host: HostId) -> None:
    """#1293 : garde, hook, politique, CI, faille — jamais un palier faible deviné sur la description."""
    tool = "Agent" if host == HostId.CLAUDE_CODE_CLI else "task"
    hook = HookInput(
        event=HookEvent.PRE_TOOL_USE,
        project_root=project,
        tool_name=tool,
        tool_input={"description": description, "prompt": "x"},
        host=host.value,
    )
    assert dm.decide_dispatch_model(hook).abstain


def test_a_plain_research_description_still_gets_haiku_under_both_hosts(project: Path) -> None:
    rendered, _ = _run(project, {"description": "Lire les fichiers de config", "prompt": "x"})
    assert rendered["hookSpecificOutput"]["updatedInput"]["model"] == "haiku"


# ── émission ───────────────────────────────────────────────────────────────


def test_the_claude_emitter_adds_an_advisory_bounded_dispatch_hook(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    emitter = emitter_for(HostId.CLAUDE_CODE_CLI)
    assert emitter is not None
    (merge,) = emitter.plan(build_surface(tmp_path), tmp_path).merges
    entries = merge.merge({})["hooks"]["PreToolUse"]
    dispatch = [e for e in entries if "--decision grimoire.dispatch-model" in e["hooks"][0]["command"]]
    assert len(dispatch) == 1
    assert dispatch[0]["matcher"] == "Task|Agent"
    handler = dispatch[0]["hooks"][0]
    assert handler["timeout"] <= 3
    assert "onFailure" not in handler
    # La garde, elle, ne change pas : même commande, pas de --decision.
    guards = [e for e in entries if e is not dispatch[0]]
    assert guards and all("--decision" not in e["hooks"][0]["command"] for e in guards)


def test_the_guard_stays_fail_closed_next_to_the_dispatch_hook(tmp_path: Path) -> None:
    """La garde (sans ``--decision``) garde ``onFailure: block`` après l'ajout du hook consultatif."""
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    emitter = emitter_for(HostId.CLAUDE_CODE_CLI)
    assert emitter is not None
    (merge,) = emitter.plan(build_surface(tmp_path), tmp_path).merges
    handlers = [h for e in merge.merge({})["hooks"]["PreToolUse"] for h in e["hooks"]]
    guards = [h for h in handlers if "--decision" not in h["command"]]
    assert guards
    assert all(h.get("onFailure") == "block" for h in guards)


def test_runtime_and_entry_point_read_the_same_decision_option() -> None:
    """Deux ``--decision`` : la première gagne des deux côtés."""
    args = ["--host", "claude", "--event", "PreToolUse", "--decision", DECISION, "--decision", "grimoire.tool-policy"]
    assert hook_entry._option(args, "--decision") == DECISION


def test_runtime_main_takes_the_first_decision_option(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[str | None] = []

    def spy(*_args: Any, decision_id: str | None = None, **_kwargs: Any) -> Any:
        seen.append(decision_id)
        raise RuntimeError("arrêt")

    monkeypatch.setattr("grimoire.hosts.runtime.run_hook", spy)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_agent_payload(project, {"prompt": "x"}))))
    args = ["--host", "claude", "--event", "PreToolUse", "--decision", DECISION, "--decision", "grimoire.tool-policy"]
    assert runtime_main(args) == 0
    assert seen == [DECISION]
    assert json.loads(capsys.readouterr().out) == {}


def test_other_hosts_do_not_get_the_dispatch_hook(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    surface = build_surface(tmp_path)
    assert all(hook.decision != DECISION for hook in surface.hooks)
