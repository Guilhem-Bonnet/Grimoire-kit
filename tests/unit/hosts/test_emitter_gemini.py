"""L'émetteur Gemini CLI écrit les surfaces que l'hôte sait exécuter.

Gemini CLI n'est pas installé sur le poste de développement : tout ce qui suit
vérifie ce que le kit **écrit** (fichiers, formes documentées), pas ce que
l'hôte en fait. Les points que la documentation laisse ouverts — blocage de fin
de tour, format des fichiers de session — sont déclarés « non vérifié » dans la
doc de référence et dans les dégradations du plan, jamais promis.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import replace
from pathlib import Path

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts.capabilities import profile_for
from grimoire.hosts.collect import build_surface
from grimoire.hosts.emitters import apply_plan, emitter_for, owned_managed_paths
from grimoire.hosts.emitters.base import MANAGED_MARKER, EmitPlan, EmitResult
from grimoire.hosts.events import Enforcement, HookEvent
from grimoire.hosts.surface import HookSpec

AGENT_DIR = Path("_grimoire/_config/custom/agents")


def _write_agent(root: Path, name: str, body: str, *, reasoning: str = "medium") -> None:
    (root / AGENT_DIR).mkdir(parents=True, exist_ok=True)
    (root / AGENT_DIR / f"{name}.md").write_text(
        "\n".join(
            [
                "---",
                f'name: "{name}"',
                f'description: "{name} — rôle de test"',
                "tools: [read, edit]",
                "model_affinity:",
                f"  reasoning: {reasoning}",
                "  cost: medium",
                "---",
                "",
                body,
            ]
        )
        + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    _write_agent(tmp_path, "concierge", "Tu tries et tu routes.")
    _write_agent(tmp_path, "scribe", "Tu rédiges la documentation.", reasoning="low")
    return tmp_path


@pytest.fixture
def governed(project: Path) -> Path:
    setup_standard_profile(project, profile_id="governed", task_id="bootstrap")
    return project


def _sync(root: Path, plan: EmitPlan | None = None) -> EmitResult:
    emitter = emitter_for(HostId.GEMINI_CLI)
    assert emitter is not None
    chosen = plan if plan is not None else emitter.plan(build_surface(root), root)
    return apply_plan(chosen, root)


def _settings(root: Path) -> dict:
    return json.loads((root / ".gemini/settings.json").read_text(encoding="utf-8"))


def test_the_profile_claims_only_what_the_documentation_supports() -> None:
    profile = profile_for(HostId.GEMINI_CLI)
    assert profile.subagents_native and profile.commands_native
    assert not profile.skills_native and not profile.permissions_native
    assert profile.blocks(HookEvent.PRE_TOOL_USE)
    assert profile.blocks(HookEvent.USER_PROMPT_SUBMIT)
    # AfterAgent n'est documenté que comme hook : pas de blocage de fin de tour promis.
    assert not profile.blocks(HookEvent.STOP)
    assert not profile.blocks(HookEvent.SESSION_START)


def test_sync_emits_hooks_commands_agents_and_the_entrypoint(governed: Path) -> None:
    result = _sync(governed)
    assert result.ok, result.conflicts

    assert (governed / ".gemini/agents/concierge.md").is_file()
    assert (governed / ".gemini/agents/scribe.md").is_file()
    assert (governed / ".gemini/commands/grimoire/grimoire-gate.toml").is_file()
    assert (governed / "GEMINI.md").is_file()
    assert (governed / ".gemini/settings.json").is_file()
    # Aucun chargement de skills sur cet hôte : leurs corps restent lisibles.
    assert list((governed / "_grimoire/hosts/skills").glob("*.md"))


def test_every_documented_event_is_wired_with_a_hook_command(governed: Path) -> None:
    _sync(governed)
    hooks = _settings(governed)["hooks"]
    assert set(hooks) == {
        "BeforeTool",
        "AfterTool",
        "BeforeAgent",
        "SessionStart",
        "SessionEnd",
        "AfterAgent",
        "PreCompress",
    }
    for event, entries in hooks.items():
        assert len(entries) == 1, event
        assert entries[0]["hooks"][0]["type"] == "command"
        assert "grimoire-hook --host gemini --event " in entries[0]["hooks"][0]["command"]
    assert "--event PreToolUse" in hooks["BeforeTool"][0]["hooks"][0]["command"]
    assert "--event Stop" in hooks["AfterAgent"][0]["hooks"][0]["command"]
    assert "--event Stop" in hooks["SessionEnd"][0]["hooks"][0]["command"]


def test_hook_timeout_is_written_in_milliseconds(governed: Path) -> None:
    surface = build_surface(governed)
    pre = next(h for h in surface.hooks if h.event is HookEvent.PRE_TOOL_USE)
    _sync(governed)
    entry = _settings(governed)["hooks"]["BeforeTool"][0]
    assert entry["hooks"][0]["timeout"] == pre.timeout * 1000
    assert entry["hooks"][0]["timeout"] >= 1000  # des secondes écrites en ms seraient un refus instantané


def test_the_tool_matcher_names_the_host_tools(governed: Path) -> None:
    _sync(governed)
    matcher = _settings(governed)["hooks"]["BeforeTool"][0]["matcher"]
    tools = matcher.split("|")
    assert "write_file" in tools and "run_shell_command" in tools
    # Un événement qui n'est pas un appel d'outil ne porte pas de matcher.
    assert "matcher" not in _settings(governed)["hooks"]["SessionStart"][0]


def test_an_existing_settings_file_keeps_the_users_keys_and_hooks(project: Path) -> None:
    (project / ".gemini").mkdir(parents=True, exist_ok=True)
    original = {
        "theme": "dark",
        "hooks": {
            "Notification": [{"hooks": [{"type": "command", "command": "ma-notification.sh"}]}],
            # Entrée périmée écrite par une version précédente du kit : elle doit partir.
            "AfterModel": [
                {"hooks": [{"type": "command", "command": "grimoire-hook --host gemini --event AfterModel"}]}
            ],
        },
        "mcpServers": {"mine": {"command": "mon-serveur"}},
    }
    (project / ".gemini/settings.json").write_text(json.dumps(original), encoding="utf-8")

    _sync(project)
    settings = _settings(project)
    assert settings["theme"] == "dark"
    assert settings["hooks"]["Notification"][0]["hooks"][0]["command"] == "ma-notification.sh"
    assert "AfterModel" not in settings["hooks"]
    assert settings["mcpServers"]["mine"] == {"command": "mon-serveur"}


def test_the_kit_mcp_server_is_declared_with_an_expanded_command(governed: Path) -> None:
    _sync(governed)
    servers = _settings(governed)["mcpServers"]
    assert "grimoire" in servers
    command = servers["grimoire"]["command"]
    assert command.endswith("grimoire-mcp")
    # Gemini ne documente pas l'expansion de ${CLAUDE_PROJECT_DIR:-.} : le
    # chemin est écrit absolu plutôt que laissé à une variable que l'hôte ignore.
    assert "${" not in command


def test_commands_are_toml_with_a_prompt_and_gemini_placeholder(governed: Path) -> None:
    _sync(governed)
    parsed = tomllib.loads((governed / ".gemini/commands/grimoire/grimoire-gate.toml").read_text(encoding="utf-8"))
    assert parsed["description"]
    prompt = parsed["prompt"]
    assert "{{args}}" in prompt
    assert "$ARGUMENTS" not in prompt
    assert "grimoire" in prompt
    for path in (governed / ".gemini/commands/grimoire").glob("*.toml"):
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        assert data["prompt"].strip(), path  # `prompt` est requis par l'hôte


def test_agent_files_carry_the_host_frontmatter_and_no_invented_model(governed: Path) -> None:
    _sync(governed)
    text = (governed / ".gemini/agents/scribe.md").read_text(encoding="utf-8")
    assert MANAGED_MARKER in text
    _, _, body = text.partition("---\n")
    fields = dict(line.split(": ", 1) for line in body.split("---\n", 1)[0].splitlines() if ": " in line)
    assert fields["name"] == "'scribe'"
    assert "description" in fields
    assert "read_file" in fields["tools"]
    assert fields["max_turns"] == "30"
    # Aucune correspondance documentée affinité -> nom de modèle : pas de champ inventé.
    assert "model" not in fields
    assert "grimoire-uncertainties" in text


def test_the_entrypoint_is_short_and_imports_the_memory_files(governed: Path) -> None:
    memory = governed / "_grimoire/_memory"
    memory.mkdir(parents=True, exist_ok=True)
    (memory / "shared-context.md").write_text("# Contexte\n", encoding="utf-8")

    _sync(governed)
    entrypoint = governed / "GEMINI.md"
    # Le fichier est partagé : le contenu de l'utilisateur hors bloc est
    # préservé à l'octet près, un second sync ne le retire pas.
    entrypoint.write_text(
        "# Mon fichier à moi\n\nGarde ceci.\n\n" + entrypoint.read_text(encoding="utf-8"), encoding="utf-8"
    )
    _sync(governed)

    text = entrypoint.read_text(encoding="utf-8")
    assert "Mon fichier à moi" in text and "Garde ceci." in text
    assert "@_grimoire/_memory/shared-context.md" in text
    assert "@_grimoire/_memory/absent.md" not in text
    # Court : les catalogues (personas, commandes) vivent dans leurs fichiers, pas ici.
    assert len(text.splitlines()) < 40


def test_a_second_sync_is_a_no_op(governed: Path) -> None:
    _sync(governed)
    emitter = emitter_for(HostId.GEMINI_CLI)
    assert emitter is not None
    result = apply_plan(emitter.plan(build_surface(governed), governed), governed)
    assert result.ok and not result.written and not result.removed


def test_an_edited_agent_file_is_preserved_as_a_conflict(governed: Path) -> None:
    _sync(governed)
    agent = governed / ".gemini/agents/concierge.md"
    agent.write_text(agent.read_text(encoding="utf-8") + "\nNote perso.\n", encoding="utf-8")
    emitter = emitter_for(HostId.GEMINI_CLI)
    assert emitter is not None
    result = apply_plan(emitter.plan(build_surface(governed), governed), governed)
    assert ".gemini/agents/concierge.md" in result.skipped
    assert result.conflicts[".gemini/agents/concierge.md"] == "edited"
    assert "Note perso." in agent.read_text(encoding="utf-8")


def test_doctor_recognises_the_emitted_paths_as_kits(governed: Path) -> None:
    """``grimoire doctor`` contrôle ces chemins par ``owned_managed_paths`` :
    un fichier écrit par le kit doit y figurer, un fichier de l'utilisateur non."""
    emitter = emitter_for(HostId.GEMINI_CLI)
    assert emitter is not None
    plan = emitter.plan(build_surface(governed), governed)
    (governed / "GEMINI.md").write_text("# à moi\n", encoding="utf-8")  # contenu utilisateur hors bloc
    apply_plan(plan, governed)
    owned = {path.relative_to(governed).as_posix() for path in owned_managed_paths(plan, governed)}
    assert ".gemini/agents/concierge.md" in owned
    assert ".gemini/commands/grimoire/grimoire-gate.toml" in owned
    assert "GEMINI.md" not in owned  # fichier partagé : le kit n'en possède qu'un bloc

    foreign = governed / ".gemini/agents/maison.md"
    foreign.write_text("# à moi\n", encoding="utf-8")
    assert "maison.md" not in {p.name for p in owned_managed_paths(plan, governed)}


def test_events_the_host_does_not_document_are_declared_not_wired(governed: Path) -> None:
    surface = build_surface(governed)
    extra = HookSpec(
        event=HookEvent.SUBAGENT_STOP,
        decision="grimoire.subagent-gate",
        enforcement=Enforcement.ADVISORY,
    )
    surface = replace(surface, hooks=(*surface.hooks, extra))
    emitter = emitter_for(HostId.GEMINI_CLI)
    assert emitter is not None
    plan = emitter.plan(surface, governed)
    assert any(d.surface == "hook subagent_stop" for d in plan.degradations)
    assert "SubagentStop" not in json.dumps(plan.merges[0].merge({}))


def test_the_plan_declares_what_stays_degraded(governed: Path) -> None:
    emitter = emitter_for(HostId.GEMINI_CLI)
    assert emitter is not None
    surfaces = {d.surface for d in emitter.plan(build_surface(governed), governed).degradations}
    assert {"skills", "permissions", "model affinity", "detached session"} <= surfaces
    assert "hook stop verification" in surfaces
