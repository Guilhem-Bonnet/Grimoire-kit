"""Émetteur Codex : schéma des fichiers écrits, fusion, capacités déclarées (#1281, lot L2).

Formats tirés de la documentation Codex lue le 2026-11-21 :
https://learn.chatgpt.com/docs/hooks (``.codex/hooks.json`` : événement → groupes
``matcher``/``hooks`` → gestionnaires ``type``/``command``/``timeout`` en secondes),
https://learn.chatgpt.com/docs/build-skills (``.agents/skills/<slug>/SKILL.md``,
``name`` et ``description``), https://learn.chatgpt.com/docs/agent-configuration/subagents
(``.codex/agents/*.toml`` : ``name``, ``description``, ``developer_instructions``),
https://learn.chatgpt.com/docs/extend/mcp (``[mcp_servers.<nom>]`` dans
``.codex/config.toml``). La CLI n'est pas installée : rien n'est capturé.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.bridges.schemas import CODEX_MANIFEST, HostId
from grimoire.cli.doctor_codex import check_codex_hooks
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts import matrix as matrix_mod
from grimoire.hosts.capabilities import (
    GovernanceFunction,
    ParityLevel,
    contract_for,
    function_states,
    gaps_for,
    profile_for,
)
from grimoire.hosts.collect import build_surface
from grimoire.hosts.emitters import CodexEmitter, EmitResult, GenericEmitter, apply_plan, emitter_for
from grimoire.hosts.emitters import codex as codex_mod
from grimoire.hosts.hook_launcher import HookLauncher
from grimoire.hosts.matrix import ADVISORY, ENFORCED, build_matrix
from grimoire.hosts.surface import HookEvent

pytestmark = pytest.mark.covers("host:codex", level="integration")

AGENT_DIR = Path("_grimoire/_config/custom/agents")
#: Événements que la page Hooks de Codex énumère et que le kit câble.
DOCUMENTED_EVENTS = {
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "SubagentStart",
    "SubagentStop",
    "PreCompact",
    "Stop",
}


@pytest.fixture
def governed(tmp_path: Path) -> Path:
    (tmp_path / AGENT_DIR).mkdir(parents=True)
    (tmp_path / AGENT_DIR / "concierge.md").write_text(
        '---\nname: "concierge"\ndescription: "Tri et routage"\n---\nTu tries.\n', encoding="utf-8"
    )
    skills = tmp_path / "_grimoire/kit/skills"
    skills.mkdir(parents=True)
    (skills / "grimoire-memory.md").write_text(
        "---\nname: grimoire-memory\ndescription: Mémoire projet\n---\nCorps.\n", encoding="utf-8"
    )
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"grimoire": {"command": "grimoire-mcp", "args": ["serve"]}}}), encoding="utf-8"
    )
    return tmp_path


def _sync(root: Path, *, force: bool = False) -> EmitResult:
    emitter = emitter_for(HostId.CODEX)
    assert isinstance(emitter, CodexEmitter)
    return apply_plan(emitter.plan(build_surface(root), root), root, force=force)


def _hooks(root: Path) -> dict:
    return json.loads((root / ".codex/hooks.json").read_text(encoding="utf-8"))["hooks"]


# ── schéma des fichiers émis ─────────────────────────────────────────────────


def test_codex_has_a_rich_emitter_not_the_prose_one() -> None:
    emitter = emitter_for(HostId.CODEX)
    assert isinstance(emitter, CodexEmitter) and not isinstance(emitter, GenericEmitter)


def test_hooks_json_has_the_documented_shape(governed: Path) -> None:
    _sync(governed)
    hooks = _hooks(governed)
    assert set(hooks) <= DOCUMENTED_EVENTS
    assert {"PreToolUse", "Stop", "SessionStart"} <= set(hooks)
    for event, groups in hooks.items():
        assert isinstance(groups, list) and groups, event
        for group in groups:
            assert set(group) <= {"matcher", "hooks"}, event
            for handler in group["hooks"]:
                assert handler["type"] == "command"
                assert f"--host codex --event {event}" in handler["command"]
                assert isinstance(handler["timeout"], int) and 0 < handler["timeout"] <= 600
    guard = hooks["PreToolUse"][0]
    assert guard["matcher"] == "Bash|apply_patch"
    assert guard["hooks"][0]["timeout"] >= codex_mod.GUARD_TIMEOUT
    assert "matcher" not in hooks["Stop"][0]  # matcher ignoré sur Stop (page Hooks)
    assert hooks["Stop"][0]["hooks"][0]["timeout"] == 60


def test_skills_follow_the_codex_layout(governed: Path) -> None:
    _sync(governed)
    text = (governed / ".agents/skills/grimoire-memory/SKILL.md").read_text(encoding="utf-8")
    front = text.split("---")[1]
    keys = {line.split(":", 1)[0].strip() for line in front.strip().splitlines()}
    assert keys == {"name", "description"}
    assert "name: 'grimoire-memory'" in front


def test_agents_are_toml_with_the_three_required_fields(governed: Path) -> None:
    _sync(governed)
    data = tomllib.loads((governed / ".codex/agents/concierge.toml").read_text(encoding="utf-8"))
    assert {"name", "description", "developer_instructions"} <= set(data)
    assert data["name"] == "concierge"
    assert "model" not in data  # dégradation déclarée « model affinity »
    assert "concierge" in data["developer_instructions"]


def test_mcp_servers_go_to_config_toml(governed: Path) -> None:
    _sync(governed)
    data = tomllib.loads((governed / ".codex/config.toml").read_text(encoding="utf-8"))
    assert data["mcp_servers"]["grimoire"] == {"command": "grimoire-mcp", "args": ["serve"]}


def test_agents_md_keeps_the_user_content_and_says_approval_is_required(governed: Path) -> None:
    (governed / "AGENTS.md").write_text("# À moi\n\nNe pas déployer le vendredi.\n", encoding="utf-8")
    _sync(governed, force=True)
    text = (governed / "AGENTS.md").read_text(encoding="utf-8")
    assert text.startswith("# À moi\n\nNe pas déployer le vendredi.\n")
    assert "approbation requise via /hooks" in text
    assert text.count("<!-- grimoire:begin") == 1


def test_a_project_relative_launcher_is_written_from_the_git_root(
    governed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = HookLauncher(
        posix=".venv/bin/grimoire-hook", windows=".venv\\Scripts\\grimoire-hook.exe", project_relative=True
    )
    monkeypatch.setattr(codex_mod, "resolve_hook_launcher", lambda _root: launcher)
    _sync(governed)
    command = _hooks(governed)["PreToolUse"][0]["hooks"][0]["command"]
    assert command.startswith('"$(git rev-parse --show-toplevel)"/.venv/bin/grimoire-hook --host codex')


# ── fusion : ce qui n'est pas au kit reste intact ─────────────────────────────


def test_sync_is_idempotent(governed: Path) -> None:
    _sync(governed)
    again = _sync(governed)
    assert not again.written and not again.skipped


def test_foreign_hooks_survive_and_stale_kit_entries_go(governed: Path) -> None:
    foreign = {"type": "command", "command": "python3 .codex/hooks/check.py", "timeout": 5}
    (governed / ".codex").mkdir()
    (governed / ".codex/hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [{"matcher": "Bash", "hooks": [foreign]}],
                    "PostCompact": [{"hooks": [foreign]}],
                    "Interrupt": [{"hooks": [{"type": "command", "command": "grimoire-hook --host codex --event X"}]}],
                },
                "autre": True,
            }
        ),
        encoding="utf-8",
    )
    _sync(governed)
    data = json.loads((governed / ".codex/hooks.json").read_text(encoding="utf-8"))
    assert data["autre"] is True
    assert data["hooks"]["PreToolUse"][0] == {"matcher": "Bash", "hooks": [foreign]}
    assert len(data["hooks"]["PreToolUse"]) == 2  # le groupe du projet, puis celui du kit
    assert data["hooks"]["PostCompact"] == [{"hooks": [foreign]}]
    assert "Interrupt" not in data["hooks"]  # entrée du kit pour un événement qu'il ne câble plus


def test_config_toml_is_appended_never_rewritten(governed: Path) -> None:
    original = '# réglages perso\nmodel = "gpt-5.5"\n\n[mcp_servers.context7]\ncommand = "npx"\nargs = ["-y", "@upstash/context7-mcp"]\n'
    (governed / ".codex").mkdir()
    (governed / ".codex/config.toml").write_text(original, encoding="utf-8")
    _sync(governed)
    text = (governed / ".codex/config.toml").read_text(encoding="utf-8")
    assert text.startswith(original)
    data = tomllib.loads(text)
    assert set(data["mcp_servers"]) == {"context7", "grimoire"}
    assert not _sync(governed).written


def test_a_server_the_project_declared_is_left_alone(governed: Path) -> None:
    mine = '[mcp_servers.grimoire]\ncommand = "/opt/mon-grimoire"\n'
    (governed / ".codex").mkdir()
    (governed / ".codex/config.toml").write_text(mine, encoding="utf-8")
    result = _sync(governed)
    assert ".codex/config.toml" in result.unchanged
    assert (governed / ".codex/config.toml").read_text(encoding="utf-8") == mine


def test_an_invalid_config_toml_is_preserved_and_reported(governed: Path) -> None:
    (governed / ".codex").mkdir()
    (governed / ".codex/config.toml").write_text("[mcp_servers\n", encoding="utf-8")
    result = _sync(governed)
    assert any(label.startswith(".codex/config.toml") for label in result.skipped)
    assert (governed / ".codex/config.toml").read_text(encoding="utf-8") == "[mcp_servers\n"


# ── capacités : documenté, jamais prouvé ─────────────────────────────────────


def test_the_guard_is_advisory_never_enforced(governed: Path) -> None:
    matrix = build_matrix(governed, (HostId.CODEX,))
    assert matrix.ok, [c.to_dict() for c in matrix.failing]
    hook_cells = [c for c in matrix.cells if c.kind == "hook"]
    assert hook_cells and all(c.status != ENFORCED for c in hook_cells)
    pre = next(c for c in hook_cells if c.subject == "hook:pre_tool_use")
    assert pre.status == ADVISORY and "/hooks" in pre.reason
    assert matrix_mod.FAILING.isdisjoint({c.status for c in hook_cells})


def test_profile_manifest_and_contract_agree() -> None:
    profile = profile_for(HostId.CODEX)
    assert profile.subagents_native and profile.skills_native and not profile.commands_native
    assert profile.blocking_events == frozenset()
    assert not any(profile.blocks(event) for event in HookEvent)
    assert profile.hook_approval.startswith("approbation requise via /hooks")
    # Le manifeste ne déclare plus seulement subagent_start/stop : il suit ce que l'émetteur câble.
    for event in codex_mod._CODEX_EVENTS:
        assert profile.supports_event(event), event
    assert CODEX_MANIFEST.hooks.post_tool_use_failure is False
    assert not contract_for(HostId.CODEX, "0.99.0").fenced
    assert "hook_approval" in {g.surface for g in gaps_for(profile)}


def test_function_states_never_claim_a_rich_guard() -> None:
    states = function_states(HostId.CODEX)
    assert states[GovernanceFunction.TOOL_GUARD].level is ParityLevel.DEGRADED
    assert "/hooks" in states[GovernanceFunction.TOOL_GUARD].reason
    assert states[GovernanceFunction.WRITE_TRACE].level is ParityLevel.DEGRADED
    assert states[GovernanceFunction.PERSONAS].level is ParityLevel.RICH


def test_degradations_are_declared(governed: Path) -> None:
    emitter = emitter_for(HostId.CODEX)
    assert emitter is not None
    surfaces = {d.surface for d in emitter.plan(build_surface(governed), governed).degradations}
    assert {
        "hook approval",
        "hook enforcement",
        "hook failure",
        "ask",
        "web search",
        "commands",
        "model affinity",
        "mcp trust",
    } <= surfaces


# ── host status et doctor disent l'approbation ───────────────────────────────


def test_host_status_says_approval_is_required(governed: Path) -> None:
    from grimoire.cli.app import app

    _sync(governed)
    result = CliRunner().invoke(
        app, ["-o", "json", "host", "status", "--host", "codex", "--project-root", str(governed)]
    )
    payload = json.loads(result.output)
    assert payload[0]["hook_approval"].startswith("approbation requise via /hooks")


def test_doctor_warns_that_codex_hooks_need_approval(governed: Path) -> None:
    assert check_codex_hooks(governed) == []
    _sync(governed)
    (check,) = check_codex_hooks(governed)
    assert check.level == "warn" and "approbation requise via /hooks" in check.detail
    (governed / ".codex/hooks.json").write_text("{", encoding="utf-8")
    (broken,) = check_codex_hooks(governed)
    assert "not valid JSON" in broken.detail
