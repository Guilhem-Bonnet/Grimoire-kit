"""Issue #716 : l'identité d'agent que l'hôte fournit (`agent_type`, `agent_id`) est lue.

Deux payloads : le `SubagentStop` réellement capturé sur Claude Code 2.1.301
(`tests/fixtures/hosts/claude-code/05-subagent-stop.json`, porte `agent_id` et
`agent_type`), et un `SessionStart` à la forme documentée pour une session
ouverte par `claude --agent <nom>` (`10-session-start-agent.json`, non capturé :
la doc des hooks dit « `agent_type` present when you start Claude Code with
`claude --agent <name>` »).
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts.decisions import decide_activation
from grimoire.hosts.decisions._shared import HookEvent, HookInput
from grimoire.hosts.runtime import normalize_input

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hosts" / "claude-code"


def _payload(name: str, event: str) -> dict:
    return dict(json.loads((FIXTURES / name).read_text(encoding="utf-8"))["payloads"][event])


def test_session_start_under_agent_carries_agent_type(tmp_path: Path) -> None:
    payload = _payload("10-session-start-agent.json", "SessionStart")
    hook = normalize_input(payload, project_root=tmp_path)
    assert hook.event is HookEvent.SESSION_START
    assert hook.agent_type == "task-worker"
    assert hook.agent_id == ""
    assert hook.agent_name == "task-worker", "la trace nomme l'agent en jeu"


def test_subagent_payload_exposes_agent_id_for_claims(tmp_path: Path) -> None:
    payload = _payload("05-subagent-stop.json", "SubagentStop")
    hook = normalize_input(payload, project_root=tmp_path)
    assert hook.agent_id == "a8952584bb862045a"
    assert hook.agent_type == "k0probe"
    assert hook.agent_name == "k0probe"


def test_explicit_agent_name_still_wins_over_agent_type(tmp_path: Path) -> None:
    hook = normalize_input({"hook_event_name": "SubagentStop", "agent_name": "explicit", "agent_type": "typed"}, project_root=tmp_path)
    assert hook.agent_name == "explicit"
    assert hook.agent_type == "typed"


def _governed(tmp_path: Path) -> Path:
    """Projet gouverné avec une persona d'entrée (`concierge`), comme la fixture `project` de test_hosts."""
    agents = tmp_path / "_grimoire" / "kit" / "agents"
    agents.mkdir(parents=True)
    (agents / "concierge.md").write_text(
        "---\nname: \"concierge\"\ndescription: \"concierge — rôle de test\"\n"
        "model_affinity:\n  reasoning: medium\n  context_window: medium\n  cost: medium\n  speed: medium\n---\n"
        "Tu tries et tu routes.\n",
        encoding="utf-8",
    )
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    return tmp_path


def test_session_start_without_agent_injects_the_entry_persona(tmp_path: Path) -> None:
    root = _governed(tmp_path)
    decision = decide_activation(HookInput(event=HookEvent.SESSION_START, project_root=root, host="claude"))
    assert decision.detail["entry_agent"] == "concierge", "sans --agent, la persona d'entrée est remise à la boucle principale"
    assert "concierge" in decision.context
    assert decision.detail["session_agent"] == ""


def test_session_start_under_agent_skips_the_entry_persona(tmp_path: Path) -> None:
    root = _governed(tmp_path)
    payload = _payload("10-session-start-agent.json", "SessionStart")
    hook = replace(normalize_input(payload, project_root=root), host="claude")
    decision = decide_activation(hook)
    assert decision.detail["session_agent"] == "task-worker"
    assert decision.detail["entry_agent"] == ""
    assert decision.detail["dispatch_context_injected"] is False
    assert "Politique de dispatch" not in decision.context
    assert "concierge" not in decision.context
    # Le reste du contexte (directive du standard) est inchangé.
    assert "_grimoire-output/evidence/" in decision.context
