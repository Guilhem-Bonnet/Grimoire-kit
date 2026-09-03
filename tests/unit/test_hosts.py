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
from grimoire.core.claude_activation import activation_context_text
from grimoire.hosts.capabilities import gaps_for, profile_for, resolve_host
from grimoire.hosts.collect import build_surface, collect_agents, default_permissions, infer_tools, parse_frontmatter
from grimoire.hosts.decisions import (
    HookInput,
    Outcome,
    classify_tool,
    decide_activation,
    decide_evidence_gate,
    decide_tool_policy,
    entry_persona_context,
)
from grimoire.hosts.emitters import apply_plan, emitter_for, supported_hosts
from grimoire.hosts.emitters.claude_code import _matcher
from grimoire.hosts.runtime import normalize_input, parse_event, render, run_hook
from grimoire.hosts.secrets import SECRET_RULES, secret_read_globs
from grimoire.hosts.surface import Enforcement, HookEvent, HookSpec, ToolVerb

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
    assert commands.count("grimoire-hook --host claude --event SessionStart") == 1
    # The superseded invocation is migrated, not stacked beside the new one.
    assert not [c for c in commands if c.startswith("grimoire host hook")]
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


def test_a_host_with_a_permission_table_does_not_pay_a_process_per_read() -> None:
    """`Read` in the matcher means a hook process on every file read (~307 ms).

    Credential paths are already refused by the declarative deny rules on a host
    that has them, so intercepting reads there buys nothing and taxes every
    session. Shell access to the same files stays covered: `Bash` is in the
    matcher for the execute family.
    """
    hook = HookSpec(
        event=HookEvent.PRE_TOOL_USE,
        decision="grimoire.tool-policy",
        matcher=("execute", "write", "secret"),
    )
    trimmed = _matcher(hook, covered=frozenset({"secret"}))
    assert "Read" not in trimmed
    assert "Bash" in trimmed and "Edit" in trimmed


def test_a_host_without_a_permission_table_keeps_intercepting_reads() -> None:
    hook = HookSpec(
        event=HookEvent.PRE_TOOL_USE,
        decision="grimoire.tool-policy",
        matcher=("execute", "write", "secret"),
    )
    assert "Read" in _matcher(hook)


def test_every_credential_family_is_declared_in_both_forms() -> None:
    """The regex and the deny glob drifted apart once; this is what caught it.

    Three families (`.npmrc`, `credentials.*`, `service-account*.json`) had a
    detection pattern and no declarative counterpart, so they were unprotected
    on the side that costs nothing.
    """
    for rule in SECRET_RULES:
        assert rule.pattern, f"{rule.name} sans motif de détection"
        assert rule.globs, f"{rule.name} sans glob déclaratif"

    deny = default_permissions("governed").deny
    for glob in secret_read_globs():
        assert f"read:{glob}" in deny, f"{glob} absent des règles deny"


@pytest.mark.parametrize(
    "probe",
    [
        ".env",
        "app/.env.local",
        "app/.npmrc",
        "x/.pypirc",
        "~/.ssh/id_ecdsa",
        "secrets/token",
        "certs/a.p12",
        "k/credentials.json",
        "sa/service-account-prod.json",
    ],
)
def test_each_credential_family_is_actually_detected(probe: str) -> None:
    assert classify_tool("Read", {"file_path": probe}).secret_target, probe


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


def _copilot_hook_plan(project: Path) -> tuple[object, object]:
    """Le plan Copilot et son premier fichier de hook."""
    emitter = emitter_for(HostId.GITHUB_COPILOT)
    plan = emitter.plan(build_surface(project), project)
    hooks = [f for f in plan.files if f.relpath.as_posix().startswith(".github/hooks/grimoire-")]
    assert hooks, "le plan Copilot doit émettre au moins un fichier de hook"
    return plan, hooks[0]


_HAND_WRITTEN_HOOK = (
    '{"hooks": {"SessionStart": [{"type": "command", "command": "MAISON-NE-PAS-ECRASER.sh", "timeout": 10}]}}\n'
)


def test_copilot_sync_preserves_a_hand_written_hook(project: Path) -> None:
    """Un hook écrit à la main survit au sync, comme un agent écrit à la main.

    L'émetteur Copilot posait ``managed=False`` sur ses fichiers de hook, ce
    qui désactivait entièrement le contrôle de préservation : le drapeau
    confondait « ne peut pas porter de marqueur de gestion » — un JSON n'a pas
    de commentaires — avec « peut être écrasé sans prévenir ». Un projet ayant
    sa propre chaîne de gouvernance la perdait au premier sync, sans message
    et sans sauvegarde.
    """
    plan, hook = _copilot_hook_plan(project)
    target = project / hook.relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_HAND_WRITTEN_HOOK, encoding="utf-8")

    result = apply_plan(plan, project)

    assert target.read_text(encoding="utf-8") == _HAND_WRITTEN_HOOK
    assert hook.relpath.as_posix() in result.skipped


def test_copilot_sync_still_updates_its_own_hook(project: Path) -> None:
    """Garde-fou : le kit doit continuer à mettre à jour ce qu'il a écrit."""
    plan, hook = _copilot_hook_plan(project)
    target = project / hook.relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    stale = (
        json.dumps(
            {"hooks": {"SessionStart": [{"type": "command", "command": "grimoire-hook --host copilot", "timeout": 5}]}},
            indent=2,
        )
        + "\n"
    )
    target.write_text(stale, encoding="utf-8")

    result = apply_plan(plan, project)

    assert target.read_text(encoding="utf-8") == hook.content
    assert hook.relpath.as_posix() in result.written


def test_copilot_sync_force_overwrites_a_hand_written_hook(project: Path) -> None:
    """``--force`` reste la porte de sortie, comme pour les agents."""
    plan, hook = _copilot_hook_plan(project)
    target = project / hook.relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_HAND_WRITTEN_HOOK, encoding="utf-8")

    apply_plan(plan, project, force=True)

    assert target.read_text(encoding="utf-8") == hook.content


# ── Persona d'entrée ─────────────────────────────────────────────────────────


def _session_start(root: Path) -> str:
    return decide_activation(HookInput(event=HookEvent.SESSION_START, project_root=root)).context


def test_the_entry_persona_reaches_the_session_start_context(project: Path) -> None:
    """Le contrat entier : la désignation `entry_point` sort du hook."""
    context = _session_start(project)
    assert "concierge" in context, "la persona d'entrée n'atteint pas la session"
    assert "_grimoire/_config/custom/agents/concierge.md" in context, "sans chemin, rien à lire"
    assert "scribe" not in context, "seule la persona d'entrée est injectée"


def test_the_validated_directive_survives_the_persona(project: Path) -> None:
    """La persona s'ajoute au standard, elle ne le remplace pas.

    Le mécanisme d'activation a été mesuré 40/40 contre 0/40. L'écraser pour
    faire de la place à une persona échangerait un effet prouvé contre un
    effet supposé.
    """
    context = _session_start(project)
    assert "[Grimoire Standard — activation]" in context
    assert context.index("[Grimoire — persona d'entrée]") < context.index("[Grimoire Standard — activation]")


def test_a_project_without_an_entry_persona_keeps_the_bare_directive(tmp_path: Path) -> None:
    _write_agent(tmp_path, "scribe", "Tu rédiges la documentation.")
    assert entry_persona_context(tmp_path) == ("", "")
    assert _session_start(tmp_path) == activation_context_text(tmp_path, task_id="bootstrap")


def test_the_hook_names_the_persona_it_injected(project: Path) -> None:
    """Sans trace dans `detail`, une injection muette est indistinguable d'une absence."""
    rendered, decision, _ = run_hook(
        {"hook_event_name": "SessionStart", "cwd": str(project)}, host_id=HostId.CLAUDE_CODE_CLI
    )
    assert decision.detail["entry_agent"] == "concierge"
    assert "concierge" in rendered["hookSpecificOutput"]["additionalContext"]


def _configure_entry(root: Path, entry: str | None) -> None:
    """Écrit un project-context.yaml minimal ; ``None`` omet la clé."""
    lines = ['project:', '  name: "hosts-test"', '  type: "library"', 'agents:', '  archetype: "minimal"']
    if entry is not None:
        lines.append(f'  entry: "{entry}"')
    (root / "project-context.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_the_entry_persona_defaults_to_concierge_without_a_config(project: Path) -> None:
    surface = build_surface(project)
    assert surface.entry_agent() is not None and surface.entry_agent().name == "concierge"


def test_the_project_designates_its_entry_persona(project: Path) -> None:
    """Un projet qui a son propre point d'entrée ne doit pas en recevoir un second."""
    _configure_entry(project, "scribe")
    names = {a.name: a.entry_point for a in collect_agents(project)}
    assert names == {"concierge": False, "scribe": True}
    context = _session_start(project)
    assert "**scribe**" in context and "**concierge**" not in context


def test_an_empty_entry_means_no_entry_persona(project: Path) -> None:
    """Vide n'est pas absent : c'est la déclaration « je porte déjà mon point d'entrée »."""
    _configure_entry(project, "")
    assert build_surface(project).entry_agent() is None
    assert entry_persona_context(project) == ("", "")
    assert _session_start(project) == activation_context_text(project, task_id="bootstrap")


def test_an_entry_that_names_no_agent_is_reported_not_invented(project: Path) -> None:
    _configure_entry(project, "fantome")
    assert build_surface(project).entry_agent() is None
    assert entry_persona_context(project) == ("", "")


def test_no_host_can_open_a_session_inside_an_agent(project: Path) -> None:
    """Le manque est déclaré, pas commenté — et chaque hôte nomme son substitut."""
    del project
    for host_id in supported_hosts():
        profile = profile_for(host_id)
        assert not profile.agent_autostart, f"{host_id.value} prétend démarrer dans un agent"
        gap = next((g for g in gaps_for(profile) if g.surface == "agent_autostart"), None)
        assert gap is not None, f"{host_id.value} tait le manque au lieu de le dégrader"
        if profile.supports_event(HookEvent.SESSION_START):
            assert "session_start" in gap.fallback
        else:
            assert profile.instructions_entrypoint in gap.fallback
