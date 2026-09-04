"""Tests for the host surface layer: collection, emission, decisions, wire format.

The promise under test is narrow and checkable: one description of the project,
the same governance decision on every host, and a refusal that actually refuses
where the host can refuse.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.core import layout
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts.capabilities import gaps_for, profile_for, resolve_host
from grimoire.hosts.collect import build_surface, collect_agents, infer_tools, parse_frontmatter
from grimoire.hosts.decisions import (
    HookInput,
    Outcome,
    classify_tool,
    decide_evidence_gate,
    decide_tool_policy,
)
from grimoire.hosts.emitters import apply_plan, emitter_for, supported_hosts
from grimoire.hosts.runtime import normalize_input, parse_event, render, run_hook
from grimoire.hosts.surface import Enforcement, HookEvent, ToolVerb

AGENT_DIR = Path("_grimoire/_config/custom/agents")


def _write_agent(root: Path, name: str, body: str, *, tools: str = "", reasoning: str = "medium") -> None:
    (root / AGENT_DIR).mkdir(parents=True, exist_ok=True)
    header = [
        "---",
        f'name: "{name}"',
        f'description: "{name} — rôle de test"',
    ]
    if tools:
        header.append(f"tools: [{tools}]")
    header += ["model_affinity:", f"  reasoning: {reasoning}", "  context_window: medium", "---", ""]
    (root / AGENT_DIR / f"{name}.md").write_text("\n".join(header) + body + "\n", encoding="utf-8")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    _write_agent(tmp_path, "concierge", "Tu tries et tu routes. Tu exécutes des diagnostics.")
    _write_agent(tmp_path, "scribe", "Tu rédiges la documentation.", reasoning="low")
    return tmp_path


@pytest.fixture
def governed(project: Path) -> Path:
    setup_standard_profile(project, profile_id="governed", task_id="bootstrap")
    return project


# ── Collection ───────────────────────────────────────────────────────────────


def test_frontmatter_parsing_survives_a_leading_comment() -> None:
    text = "<!-- ARCHETYPE: meta -->\n---\nname: x\n---\ncorps"
    meta, body = parse_frontmatter(text)
    assert meta["name"] == "x"
    assert body.strip() == "corps"


def test_declared_tools_win_over_inference(project: Path) -> None:
    _write_agent(project, "auditor", "Tu rédiges et tu exécutes.", tools="'read'")
    by_name = {a.name: a for a in collect_agents(project)}
    assert by_name["auditor"].tools == (ToolVerb.READ,)
    assert by_name["auditor"].tools_origin == "declared"
    assert by_name["scribe"].tools_origin == "inferred"


def test_inference_grants_write_only_on_an_explicit_signal() -> None:
    assert ToolVerb.EDIT not in infer_tools("Tu observes et tu rapportes.", "")
    assert ToolVerb.EDIT in infer_tools("Tu rédiges la documentation.", "")
    assert ToolVerb.EXECUTE in infer_tools("Tu lances les tests.", "")


def test_an_unrendered_placeholder_name_falls_back_to_the_file_name(project: Path) -> None:
    """The blank agent template keeps `name: "{{agent_tag}}"` until it is filled in."""
    (project / AGENT_DIR / "custom-agent.md").write_text(
        '---\nname: "{{agent_tag}}"\ndescription: "{{agent_role}}"\n---\nCorps.\n',
        encoding="utf-8",
    )
    names = {a.name for a in collect_agents(project)}
    assert "custom-agent" in names
    assert not [n for n in names if "{{" in n]


def test_an_override_wins_over_the_kit_tier(tmp_path: Path) -> None:
    """La persona du projet doit gagner sur celle que le kit livre.

    Régression croisée entre la frontière kit/overrides et cette couche : tant
    que l'émetteur balayait les répertoires en direct, il projetait la
    définition du tier kit même quand le projet en avait posé une dans
    ``_grimoire/overrides/agents/``. Le wrapper généré pointait alors le
    fichier que la prochaine mise à jour réécrit, et la customisation
    disparaissait sans un mot.
    """
    for tier, body in ((layout.KIT_DIR, "Version livrée."), (layout.OVERRIDES_DIR, "Version du projet.")):
        directory = tmp_path / tier / layout.AGENTS_SUBDIR
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "concierge.md").write_text(
            '---\nname: "concierge"\ndescription: "tri"\n---\n' + body + "\n", encoding="utf-8"
        )

    (concierge,) = [a for a in collect_agents(tmp_path) if a.name == "concierge"]
    assert concierge.definition_ref == f"{layout.OVERRIDES_DIR}/{layout.AGENTS_SUBDIR}/concierge.md"

    emitter = emitter_for(HostId.GITHUB_COPILOT)
    apply_plan(emitter.plan(build_surface(tmp_path), tmp_path), tmp_path)
    wrapper = (tmp_path / ".github/agents/concierge.agent.md").read_text(encoding="utf-8")
    assert concierge.definition_ref in wrapper
    assert f"{layout.KIT_DIR}/{layout.AGENTS_SUBDIR}/concierge.md" not in wrapper


def test_evidence_skill_appears_only_once_enrolled(project: Path) -> None:
    assert "grimoire-evidence" not in {s.slug for s in build_surface(project).skills}
    setup_standard_profile(project, profile_id="governed", task_id="bootstrap")
    assert "grimoire-evidence" in {s.slug for s in build_surface(project).skills}


def test_blocking_gate_hook_requires_enrolment(project: Path) -> None:
    assert not [h for h in build_surface(project).hooks if h.event is HookEvent.STOP]
    setup_standard_profile(project, profile_id="governed", task_id="bootstrap")
    stop = [h for h in build_surface(project).hooks if h.event is HookEvent.STOP]
    assert stop and stop[0].enforcement is Enforcement.BLOCKING


# ── Emission ─────────────────────────────────────────────────────────────────


def test_claude_surface_is_native_everywhere(governed: Path) -> None:
    emitter = emitter_for(HostId.CLAUDE_CODE_CLI)
    assert emitter is not None
    apply_plan(emitter.plan(build_surface(governed), governed), governed)
    assert (governed / ".claude/agents/concierge.md").is_file()
    assert (governed / ".claude/skills/grimoire-evidence/SKILL.md").is_file()
    assert (governed / ".claude/commands/grimoire-gate.md").is_file()
    settings = json.loads((governed / ".claude/settings.json").read_text(encoding="utf-8"))
    assert "Stop" in settings["hooks"]
    assert settings["permissions"]["deny"]


def test_agent_tool_boundary_reaches_the_host_file(governed: Path) -> None:
    emitter = emitter_for(HostId.CLAUDE_CODE_CLI)
    assert emitter is not None
    apply_plan(emitter.plan(build_surface(governed), governed), governed)
    scribe = (governed / ".claude/agents/scribe.md").read_text(encoding="utf-8")
    assert "tools: 'Read, Glob, Grep, Edit, Write'" in scribe
    assert "model: 'haiku'" in scribe  # low reasoning demand


def test_copilot_surface_declares_its_permission_gap(governed: Path) -> None:
    emitter = emitter_for(HostId.GITHUB_COPILOT)
    assert emitter is not None
    plan = emitter.plan(build_surface(governed), governed)
    apply_plan(plan, governed)
    assert (governed / ".github/agents/concierge.agent.md").is_file()
    assert (governed / ".github/prompts/grimoire-gate.prompt.md").is_file()
    assert (governed / ".github/hooks/grimoire-stop.json").is_file()
    assert "permissions" in {d.surface for d in plan.degradations}


def test_copilot_agent_files_carry_the_wrapper_contract(governed: Path) -> None:
    """Contracts inherited from the scaffolder's wrappers, now owned here.

    `.github/agents/` had two writers: the scaffolder emitted a coarse wrapper
    and this emitter replaced it with one carrying the resolved tool boundary
    and the real definition path. The scaffolder no longer writes them, so the
    guarantees its tests pinned are pinned here instead.
    """
    emitter = emitter_for(HostId.GITHUB_COPILOT)
    assert emitter is not None
    apply_plan(emitter.plan(build_surface(governed), governed), governed)

    entry = (governed / ".github/agents/concierge.agent.md").read_text(encoding="utf-8")
    sub = (governed / ".github/agents/scribe.agent.md").read_text(encoding="utf-8")

    assert entry.startswith("---\n") and "description:" in entry
    # The entry point stays user-invocable; every other persona is routed.
    assert "user-invocable: false" not in entry
    assert "user-invocable: false" in sub
    # The wrapper must point at the file that actually holds the persona.
    assert "_grimoire/_config/custom/agents/concierge.md" in entry
    # ...and carry the boundary the surface resolved, not a fixed guess.
    assert "tools: ['read', 'search', 'edit']" in sub


def test_a_hand_written_copilot_wrapper_is_preserved(governed: Path) -> None:
    target = governed / ".github/agents/concierge.agent.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Mon agent à moi\n", encoding="utf-8")
    emitter = emitter_for(HostId.GITHUB_COPILOT)
    assert emitter is not None
    result = apply_plan(emitter.plan(build_surface(governed), governed), governed)
    assert ".github/agents/concierge.agent.md" in result.skipped
    assert target.read_text(encoding="utf-8") == "# Mon agent à moi\n"


def test_prose_only_host_states_that_governance_is_not_enforced(governed: Path) -> None:
    emitter = emitter_for(HostId.CODEX)
    assert emitter is not None
    plan = emitter.plan(build_surface(governed), governed)
    apply_plan(plan, governed, force=True)
    catalog = (governed / "AGENTS.md").read_text(encoding="utf-8")
    assert "concierge" in catalog
    assert "ne sont pas opposables" in catalog
    assert {"subagents", "skills", "commands", "hooks"} <= {d.surface for d in plan.degradations}


@pytest.mark.parametrize("host_id", list(supported_hosts()))
def test_emission_is_idempotent(governed: Path, host_id: HostId) -> None:
    emitter = emitter_for(host_id)
    assert emitter is not None
    surface = build_surface(governed)
    apply_plan(emitter.plan(surface, governed), governed, force=True)
    again = apply_plan(emitter.plan(build_surface(governed), governed), governed)
    assert not again.written, f"{host_id.value} réécrit : {again.written}"


def test_a_hand_written_file_is_never_silently_replaced(governed: Path) -> None:
    target = governed / ".claude/agents/concierge.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("écrit à la main\n", encoding="utf-8")
    emitter = emitter_for(HostId.CLAUDE_CODE_CLI)
    assert emitter is not None
    result = apply_plan(emitter.plan(build_surface(governed), governed), governed)
    assert ".claude/agents/concierge.md" in result.skipped
    assert target.read_text(encoding="utf-8") == "écrit à la main\n"
    forced = apply_plan(emitter.plan(build_surface(governed), governed), governed, force=True)
    assert ".claude/agents/concierge.md" in forced.written


def test_settings_merge_preserves_foreign_configuration(governed: Path) -> None:
    settings = governed / ".claude/settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(
        json.dumps(
            {
                "model": "opus",
                "hooks": {
                    "SessionStart": [
                        {"hooks": [{"type": "command", "command": "grimoire standard activation-context"}]},
                        {"hooks": [{"type": "command", "command": "echo maison"}]},
                    ]
                },
                "permissions": {"deny": ["Read(./privé)"]},
            }
        ),
        encoding="utf-8",
    )
    emitter = emitter_for(HostId.CLAUDE_CODE_CLI)
    assert emitter is not None
    apply_plan(emitter.plan(build_surface(governed), governed), governed)
    data = json.loads(settings.read_text(encoding="utf-8"))

    assert data["model"] == "opus"
    commands = [h["command"] for entry in data["hooks"]["SessionStart"] for h in entry["hooks"]]
    assert "echo maison" in commands
    # The legacy activation hook is superseded, not stacked on top of.
    assert "grimoire standard activation-context" not in commands
    assert commands.count("grimoire host hook --host claude --event SessionStart") == 1
    assert "Read(./privé)" in data["permissions"]["deny"]


def test_repeated_sync_never_stacks_hook_entries(governed: Path) -> None:
    emitter = emitter_for(HostId.CLAUDE_CODE_CLI)
    assert emitter is not None
    for _ in range(3):
        apply_plan(emitter.plan(build_surface(governed), governed), governed)
    data = json.loads((governed / ".claude/settings.json").read_text(encoding="utf-8"))
    assert len(data["hooks"]["Stop"]) == 1


# ── Decisions ────────────────────────────────────────────────────────────────


def test_tool_classification_reads_both_host_vocabularies() -> None:
    assert classify_tool("Bash", {"command": "rm -rf build"}).destructive_reason
    assert classify_tool("run_in_terminal", {"command": "rm -rf build"}).destructive_reason
    assert classify_tool("replace_string_in_file", {"filePath": "a.py"}).family == "write"
    assert classify_tool("Read", {"file_path": "app/.env"}).secret_target


def test_secret_detection_survives_a_windows_separator() -> None:
    """A Windows host hands over `app\\.env`; a `/`-only anchor lets it through."""
    assert classify_tool("Read", {"file_path": r"app\.env"}).secret_target
    assert classify_tool("Read", {"file_path": r"C:\proj\secrets\token.txt"}).secret_target
    assert not classify_tool("Read", {"file_path": r"docs\environment.md"}).secret_target


def test_destructive_and_secret_actions_are_refused(governed: Path) -> None:
    deny = decide_tool_policy(
        HookInput(
            event=HookEvent.PRE_TOOL_USE, project_root=governed, tool_name="Bash", tool_input={"command": "rm -rf src"}
        )
    )
    assert deny.outcome is Outcome.DENY
    secret = decide_tool_policy(
        HookInput(
            event=HookEvent.PRE_TOOL_USE, project_root=governed, tool_name="Read", tool_input={"file_path": ".env"}
        )
    )
    assert secret.outcome is Outcome.DENY


def test_read_only_calls_are_not_slowed_down(governed: Path) -> None:
    decision = decide_tool_policy(
        HookInput(
            event=HookEvent.PRE_TOOL_USE, project_root=governed, tool_name="Read", tool_input={"file_path": "README.md"}
        )
    )
    assert decision.outcome is Outcome.ALLOW
    assert decision.detail == {}


def _set_task_in_progress(root: Path) -> None:
    board = root / "_grimoire/standard/task-board.yaml"
    board.write_text(
        board.read_text(encoding="utf-8").replace('status: "proposed"', 'status: "in_progress"'), encoding="utf-8"
    )


def test_red_gates_block_a_governed_closure(governed: Path) -> None:
    _set_task_in_progress(governed)
    (governed / "_grimoire-output/context/bootstrap/context-bundle.yaml").unlink(missing_ok=True)
    decision = decide_evidence_gate(HookInput(event=HookEvent.STOP, project_root=governed))
    assert decision.outcome is Outcome.BLOCK
    assert "non terminée" in decision.reason


def test_a_block_never_repeats_itself(governed: Path) -> None:
    _set_task_in_progress(governed)
    (governed / "_grimoire-output/context/bootstrap/context-bundle.yaml").unlink(missing_ok=True)
    decision = decide_evidence_gate(HookInput(event=HookEvent.STOP, project_root=governed, stop_active=True))
    assert decision.outcome is Outcome.ALLOW


def test_an_unenrolled_project_is_never_blocked(project: Path) -> None:
    decision = decide_evidence_gate(HookInput(event=HookEvent.STOP, project_root=project))
    assert decision.outcome is Outcome.ALLOW
    assert decision.detail["skipped"] == "project_not_enrolled"


def test_a_green_gate_on_a_proposed_task_says_it_protects_nothing(governed: Path) -> None:
    decision = decide_evidence_gate(HookInput(event=HookEvent.STOP, project_root=governed))
    assert decision.outcome is Outcome.ALLOW
    assert "ne protège rien" in decision.context


def test_a_broken_project_does_not_brick_the_session(governed: Path) -> None:
    (governed / "_grimoire/standard/task-board.yaml").write_text("[oups", encoding="utf-8")
    decision = decide_evidence_gate(HookInput(event=HookEvent.STOP, project_root=governed))
    assert decision.outcome is Outcome.ALLOW


# ── Wire format ──────────────────────────────────────────────────────────────


def test_event_names_are_accepted_in_every_spelling() -> None:
    assert parse_event("PreToolUse") is HookEvent.PRE_TOOL_USE
    assert parse_event("pre_tool_use") is HookEvent.PRE_TOOL_USE
    assert parse_event("pre-tool-use") is HookEvent.PRE_TOOL_USE
    assert parse_event("inconnu") is None


def test_payload_keys_are_read_in_both_casings(tmp_path: Path) -> None:
    snake = normalize_input({"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(tmp_path)})
    camel = normalize_input({"hookEventName": "PreToolUse", "toolName": "Bash", "cwd": str(tmp_path)})
    assert snake.tool_name == camel.tool_name == "Bash"
    assert snake.event is camel.event is HookEvent.PRE_TOOL_USE


def test_the_same_refusal_reaches_both_blocking_hosts(governed: Path) -> None:
    _set_task_in_progress(governed)
    (governed / "_grimoire-output/context/bootstrap/context-bundle.yaml").unlink(missing_ok=True)
    payload = {"hook_event_name": "Stop", "cwd": str(governed)}
    claude, claude_decision, _ = run_hook(payload, host_id=HostId.CLAUDE_CODE_CLI)
    copilot, copilot_decision, _ = run_hook(payload, host_id=HostId.GITHUB_COPILOT)
    assert claude["decision"] == copilot["decision"] == "block"
    assert claude["reason"] == copilot["reason"]
    assert claude_decision.reason == copilot_decision.reason


def test_a_host_without_blocking_hooks_gets_context_not_a_refusal(governed: Path) -> None:
    _set_task_in_progress(governed)
    (governed / "_grimoire-output/context/bootstrap/context-bundle.yaml").unlink(missing_ok=True)
    rendered, decision, hook = run_hook({"hook_event_name": "Stop", "cwd": str(governed)}, host_id=HostId.CODEX)
    assert "decision" not in rendered
    assert decision.outcome is Outcome.BLOCK  # the rule is the same...
    assert render(decision, hook, HostId.CODEX) == rendered  # ...only the wiring differs


def test_pre_tool_use_renders_a_permission_decision(governed: Path) -> None:
    rendered, _, _ = run_hook(
        {
            "hook_event_name": "PreToolUse",
            "cwd": str(governed),
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf /"},
        },
        host_id=HostId.CLAUDE_CODE_CLI,
    )
    assert rendered["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_the_compaction_capsule_outlives_the_context(governed: Path) -> None:
    rendered, _, _ = run_hook({"hook_event_name": "PreCompact", "cwd": str(governed)}, host_id=HostId.CLAUDE_CODE_CLI)
    capsule = governed / "_grimoire-output/context/bootstrap/compaction-capsule.md"
    assert capsule.is_file()
    assert "bootstrap" in capsule.read_text(encoding="utf-8")
    assert "systemMessage" in rendered


# ── Capabilities ─────────────────────────────────────────────────────────────


def test_host_aliases_resolve() -> None:
    assert resolve_host("claude") is HostId.CLAUDE_CODE_CLI
    assert resolve_host("host-github-copilot") is HostId.GITHUB_COPILOT
    assert resolve_host("nawak") is None


def test_every_gap_names_its_fallback() -> None:
    for host_id in supported_hosts():
        for gap in gaps_for(profile_for(host_id)):
            assert gap.fallback, f"{host_id.value}/{gap.surface} dégrade sans repli déclaré"
