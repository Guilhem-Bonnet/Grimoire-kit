"""``SubagentStart`` sous Copilot : classe et palier recommandés rendus en ``additionalContext`` (#1280, P3).

Forme : https://code.visualstudio.com/docs/agents/reference/hooks-reference
(``hookSpecificOutput.additionalContext``). La charge rejoue la fixture documentée
``tests/fixtures/hosts/copilot/documented/subagent-start.json`` (pas une capture).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts.collect import build_surface
from grimoire.hosts.emitters import emitter_for
from grimoire.hosts.events import HookEvent
from grimoire.hosts.runtime import run_hook
from grimoire.missions.dispatch_advice import dispatch_advice
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService

pytestmark = pytest.mark.covers("host:github-copilot", level="integration")

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/hosts/copilot/documented/subagent-start.json"
DECISION = "grimoire.subagent-start"
SESSION = "aaaaaaaa-0000-4000-8000-0000000000c3"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    home = str(tmp_path_factory.mktemp("home"))
    monkeypatch.setenv("HOME", home)
    monkeypatch.setenv("USERPROFILE", home)
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PROJECT_DIR"):
        monkeypatch.delenv(var, raising=False)


def _payload() -> dict[str, Any]:
    return dict(json.loads(FIXTURE.read_text(encoding="utf-8"))["payloads"][0]["input"], sessionId=SESSION)


def _claimed(root: Path) -> str:
    setup_standard_profile(root, profile_id="governed", task_id="bootstrap")
    registry = root / "_grimoire/standard/llm-provider-registry.yaml"
    registry.write_text(registry.read_text(encoding="utf-8").replace("enabled: false", "enabled: true", 1), encoding="utf-8")
    service = TaskService(root)
    tid = service.add("Travail", ("la suite de tests passe",), owner="guilhem", actor="copilot", ready=True).task.id
    service.context(tid)
    service.claim(tid, "copilot", host="github-copilot")
    service.transition(tid, TaskState.RUNNING, "copilot")
    service.attach_session(tid, SESSION, session_host="github-copilot", actor="copilot")
    return tid


def _run(root: Path, host: HostId = HostId.GITHUB_COPILOT) -> tuple[dict[str, Any], Any]:
    rendered, decision, _ = run_hook(
        _payload(), host_id=host, event=HookEvent.SUBAGENT_START, project_root=root, decision_id=DECISION
    )
    return rendered, decision


def test_copilot_subagent_start_renders_class_and_tier(tmp_path: Path) -> None:
    tid = _claimed(tmp_path)
    advice = dispatch_advice(TaskService(tmp_path).require(tid))
    rendered, decision = _run(tmp_path)
    specific = rendered["hookSpecificOutput"]
    assert specific["hookEventName"] == "SubagentStart"
    assert advice["verifiability"]["class"] in specific["additionalContext"]
    assert tid in specific["additionalContext"]
    assert decision.detail["tier"] == (advice["model_tier"] or "session")


@pytest.mark.parametrize("host", [HostId.CLAUDE_CODE_CLI, HostId.CODEX])
def test_other_hosts_keep_their_output(tmp_path: Path, host: HostId) -> None:
    _claimed(tmp_path)
    rendered, decision = _run(tmp_path, host)
    assert rendered == {}
    assert decision.abstain


def test_no_claimed_task_abstains(tmp_path: Path) -> None:
    (tmp_path / "_grimoire").mkdir()
    rendered, _ = _run(tmp_path)
    assert rendered == {}


def test_copilot_emitter_wires_subagent_start_only_there(tmp_path: Path) -> None:
    surface = build_surface(tmp_path)
    paths = {str(f.relpath): f for f in emitter_for(HostId.GITHUB_COPILOT).plan(surface, tmp_path).files}
    hook_file = next(f for p, f in paths.items() if p.endswith("grimoire-subagent-start.json"))
    entry = json.loads(hook_file.content)["hooks"]["SubagentStart"][0]
    assert f"--decision {DECISION}" in entry["bash"]
    claude = emitter_for(HostId.CLAUDE_CODE_CLI).plan(surface, tmp_path)
    assert all(DECISION not in f.content for f in claude.files if isinstance(f.content, str))
