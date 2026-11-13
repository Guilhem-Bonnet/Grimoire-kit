"""Gemini CLI emitter — hooks, commands, sub-agents and MCP, not just prose.

Gemini CLI exposes more than an instructions file (geminicli.com/docs, read on
2026-11-12, last announced version v0.64.0):

- ``.gemini/settings.json`` carries the project's lifecycle **hooks** and its
  **MCP** servers, next to the user's own keys, which a merge must preserve;
- ``.gemini/commands/<dir>/*.toml`` are user-invocable commands, one TOML file
  each with a required ``prompt``;
- ``.gemini/agents/*.md`` are sub-agents with their own context window;
- ``GEMINI.md`` takes ``@path`` imports, so the entrypoint can stay short.

The host binary is not installed on the development machine: every claim below
rests on the documentation, not on a capture. What the documentation leaves
open is declared, never invented — see :mod:`grimoire.hosts.gemini_wire` and
``docs/reference/hotes.md``.

What stays degraded, and why:

- **skills** have no on-demand loader: their bodies are copied under
  ``_grimoire/hosts/skills/`` and listed in ``GEMINI.md``, to be read when the
  situation calls for it;
- **blocking** is documented only for ``BeforeTool`` and ``BeforeAgent``. The
  end-of-turn events (``AfterAgent``, ``SessionEnd``) are wired and receive
  their decision, but no verdict is claimed for them (non vérifié), and
  ``SessionStart`` is advisory (``additionalContext``);
- **permissions** have no declarative table here: the rules go through the
  ``BeforeTool`` hook, same decision and same wording as elsewhere;
- **model affinity** is not emitted: the agent ``model`` field takes model
  names, and the kit has no documented neutral-affinity mapping for them;
- **no detached agent session** is documented for this host.

The ``--event`` flag written in ``settings.json`` uses the kit's neutral wire
spelling (``PreToolUse``), not Gemini's (``BeforeTool``):
:func:`grimoire.hosts.runtime.parse_event` accepts both, and the rule × host
matrix recognises a hook by that argument — two spellings for one rule would
make the matrix report an undeclared hook on a hook that is wired.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from grimoire.bridges.schemas import HostId
from grimoire.core.grounding import GROUNDING_RULE, UNCERTAINTIES_BLOCK_RULE
from grimoire.hosts.emitters.base import (
    OWNED_COMMAND_MARKERS,
    Degradation,
    EmitPlan,
    EmittedFile,
    Emitter,
    JsonMerge,
    context_load_instruction,
    managed_header,
    map_verbs,
    persona_section,
)
from grimoire.hosts.emitters.copilot import _dispatch_policy_section
from grimoire.hosts.emitters.rules import with_rule_layers
from grimoire.hosts.hook_launcher import BARE, HookLauncher, expand_project_dir, resolve_hook_launcher
from grimoire.hosts.surface import (
    AgentSpec,
    CommandSpec,
    HookEvent,
    HookSpec,
    ProjectSurface,
    ToolVerb,
)

HOST_ALIAS = "gemini"
GEMINI_DIR = Path(".gemini")
COMMANDS_DIR = GEMINI_DIR / "commands" / "grimoire"
AGENTS_DIR = GEMINI_DIR / "agents"
SKILLS_DIR = Path("_grimoire/hosts/skills")

#: Neutral tool family -> the Gemini tools that carry it. Drives both the
#: sub-agent ``tools`` field and the hook matchers, so a hook fires on the
#: calls it is about and on no others.
_TOOL_TABLE: dict[ToolVerb, tuple[str, ...]] = {
    ToolVerb.READ: ("read_file", "read_many_files", "list_directory"),
    ToolVerb.SEARCH: ("grep_search", "glob"),
    ToolVerb.EDIT: ("write_file", "replace"),
    ToolVerb.EXECUTE: ("run_shell_command",),
    ToolVerb.WEB: ("google_web_search", "web_fetch"),
}

#: Hook families the host exposes as tools the matcher can name. ``delegation``
#: has no documented Gemini tool name, so it contributes no matcher: omitting
#: the matcher widens the hook to every tool, which is the safe direction.
_MATCHER_TABLE: dict[str, tuple[str, ...]] = {
    "write": ("write_file", "replace", "write_todos", "save_memory"),
    "execute": ("run_shell_command",),
    "secret": ("read_file", "read_many_files"),
    "network": ("web_fetch", "google_web_search"),
}

#: Neutral event -> the key(s) Gemini reads in ``hooks``. ``STOP`` is two
#: documented events — ``AfterAgent`` (end of turn) and ``SessionEnd`` (end of
#: session) — so one neutral rule becomes two entries, both advisory until a
#: blocking end-of-turn event is observed.
_GEMINI_EVENTS: dict[HookEvent, tuple[str, ...]] = {
    HookEvent.SESSION_START: ("SessionStart",),
    HookEvent.USER_PROMPT_SUBMIT: ("BeforeAgent",),
    HookEvent.PRE_TOOL_USE: ("BeforeTool",),
    HookEvent.POST_TOOL_USE: ("AfterTool",),
    HookEvent.PRE_COMPACT: ("PreCompress",),
    HookEvent.STOP: ("AfterAgent", "SessionEnd"),
}

#: Events whose entries take no matcher: nothing is a tool call.
_MATCHERLESS = {
    HookEvent.SESSION_START,
    HookEvent.USER_PROMPT_SUBMIT,
    HookEvent.PRE_COMPACT,
    HookEvent.STOP,
}

#: Neutral wire spelling written in ``--event`` (see the module docstring).
_WIRE_NAMES: dict[HookEvent, str] = {
    HookEvent.SESSION_START: "SessionStart",
    HookEvent.USER_PROMPT_SUBMIT: "UserPromptSubmit",
    HookEvent.PRE_TOOL_USE: "PreToolUse",
    HookEvent.POST_TOOL_USE: "PostToolUse",
    HookEvent.PRE_COMPACT: "PreCompact",
    HookEvent.STOP: "Stop",
}

#: Budget de tours par défaut d'un sous-agent, comme chez Claude Code : assez
#: pour une tranche bornée (lire, modifier, vérifier), pas assez pour une
#: dérive silencieuse.
_DEFAULT_MAX_TURNS = 30

#: Fichiers de mémoire que ``GEMINI.md`` importe quand ils existent. Un import
#: ``@`` vers un fichier absent serait une consigne morte, donc l'émetteur ne
#: l'écrit qu'après l'avoir constaté sur le disque.
_MEMORY_IMPORTS: tuple[str, ...] = (
    ".github/copilot-instructions.md",
    "_grimoire/_memory/shared-context.md",
    "_grimoire/_memory/config.yaml",
    "_grimoire/_memory/decisions-log.md",
    "_grimoire/_memory/failure-museum.md",
)


def _matcher(hook: HookSpec) -> str:
    if hook.event in _MATCHERLESS:
        return ""
    tools: list[str] = []
    for family in hook.matcher:
        for tool in _MATCHER_TABLE.get(family, ()):
            if tool not in tools:
                tools.append(tool)
    return "|".join(tools)


def _hook_entry(event_name: str, hook: HookSpec, launcher: HookLauncher = BARE) -> dict[str, Any]:
    """One ``hooks.<event>[]`` entry, in the shape Gemini documents.

    ``timeout`` is milliseconds here, where the neutral :class:`HookSpec`
    carries seconds: writing 30 would refuse the call after 30 ms.
    """
    entry: dict[str, Any] = {}
    matcher = _matcher(hook)
    if matcher:
        entry["matcher"] = matcher
    entry["hooks"] = [
        {
            "type": "command",
            "name": f"grimoire-{event_name.lower()}",
            "command": Emitter.hook_command(
                HOST_ALIAS, _WIRE_NAMES[hook.event], Emitter.quoted_program(launcher.posix)
            ),
            "timeout": hook.timeout * 1000,
        }
    ]
    return entry


def _owned_entry(entry: Any) -> bool:
    """A hook entry the kit wrote, recognised by what it invokes (JSON has no marker)."""
    if not isinstance(entry, dict):
        return False
    entries = entry.get("hooks")
    for hook in entries if isinstance(entries, list) else []:
        command = hook.get("command") if isinstance(hook, dict) else ""
        if isinstance(command, str) and any(marker in command for marker in OWNED_COMMAND_MARKERS):
            return True
    command = entry.get("command")
    return isinstance(command, str) and any(marker in command for marker in OWNED_COMMAND_MARKERS)


def _mcp_entry(command: str, args: tuple[str, ...], url: str) -> dict[str, Any]:
    if url and not command:
        return {"url": url}
    entry: dict[str, Any] = {"command": Emitter.quoted_program(command)}
    if args:
        entry["args"] = list(args)
    return entry


def _settings_merge(surface: ProjectSurface, project_root: Path, launcher: HookLauncher = BARE) -> JsonMerge:
    hooks_by_event: dict[str, list[dict[str, Any]]] = {}
    for hook in surface.hooks:
        for event_name in _GEMINI_EVENTS.get(hook.event, ()):
            hooks_by_event.setdefault(event_name, []).append(_hook_entry(event_name, hook, launcher))
    # A project-relative venv command is written absolute here: Gemini CLI
    # documents no ``${CLAUDE_PROJECT_DIR:-.}`` expansion for ``mcpServers``,
    # so an unexpanded variable would be a command that never starts (#760).
    mcp_entries = {
        server.name: _mcp_entry(expand_project_dir(server.command, project_root), server.args, server.url)
        for server in surface.mcp_servers
    }

    def merge(data: dict[str, Any]) -> dict[str, Any]:
        hooks = data.get("hooks")
        hooks = hooks if isinstance(hooks, dict) else {}
        for event, entries in hooks_by_event.items():
            existing = hooks.get(event)
            kept = [entry for entry in (existing if isinstance(existing, list) else []) if not _owned_entry(entry)]
            hooks[event] = kept + entries
        # An event the kit no longer wires must lose its stale entry, or the
        # project keeps firing a rule its surface has dropped.
        for event, existing in list(hooks.items()):
            if event in hooks_by_event or not isinstance(existing, list):
                continue
            kept = [entry for entry in existing if not _owned_entry(entry)]
            if kept:
                hooks[event] = kept
            else:
                hooks.pop(event)
        data["hooks"] = hooks

        if mcp_entries:
            servers = data.get("mcpServers")
            servers = servers if isinstance(servers, dict) else {}
            for name, entry in mcp_entries.items():
                # A server the project already declared is its own; only add
                # what is missing.
                servers.setdefault(name, entry)
            data["mcpServers"] = servers
        return data

    return JsonMerge(relpath=GEMINI_DIR / "settings.json", merge=merge, label=".gemini/settings.json")


def _agent_file(agent: AgentSpec, surface: ProjectSurface) -> EmittedFile:
    fields: dict[str, Any] = {
        "name": agent.name,
        "description": agent.description,
        "tools": list(map_verbs(agent.tools, _TOOL_TABLE)),
        "max_turns": agent.max_turns if agent.max_turns is not None else _DEFAULT_MAX_TURNS,
    }
    header = Emitter.frontmatter(fields)
    role = (
        "Point d'entrée : quand la demande ne désigne pas clairement un rôle, c'est toi qui tranches."
        if agent.entry_point
        else "Tu es dispatché sur une tranche de travail précise ; tu ne clos pas la tâche globale."
    )
    body = f"""{header}
{managed_header(".md")}

Tu incarnes la persona Grimoire **{agent.name}** du projet {surface.project_name}.

{persona_section(agent)}

1. {context_load_instruction(agent)}
2. {role}
3. Ne sors pas de ta frontière d'outils : {", ".join(v.value for v in agent.tools)}.
4. {GROUNDING_RULE}
"""
    body = f"{body}\n{_dispatch_policy_section()}" if agent.entry_point else f"{body}5. {UNCERTAINTIES_BLOCK_RULE}\n"
    return EmittedFile(relpath=AGENTS_DIR / f"{agent.name}.md", content=body)


def _toml_string(text: str) -> str:
    """TOML basic string for *text*.

    JSON's string escaping is a subset of TOML's basic-string escaping (both
    cover ``\\"``, ``\\\\``, ``\\n``, ``\\t`` and ``\\uXXXX``), and
    :func:`json.dumps` never emits the one sequence TOML lacks (``\\/``).
    One writer, no second escaper to keep in sync.
    """
    return json.dumps(text, ensure_ascii=False)


def _command_file(command: CommandSpec) -> EmittedFile:
    body = command.body.replace("$ARGUMENTS", "{{args}}")
    if command.argument_hint and "{{args}}" not in body:
        body = body.rstrip() + "\n\nArgument fourni : {{args}}\n"
    content = "\n".join(
        [
            managed_header(".toml"),
            f"description = {_toml_string(command.description)}",
            f"prompt = {_toml_string(body)}",
            "",
        ]
    )
    return EmittedFile(relpath=COMMANDS_DIR / f"{command.slug}.toml", content=content)


def _skill_copies(surface: ProjectSurface) -> list[EmittedFile]:
    """Skill bodies at a stable path, since this host loads none on demand."""
    files: list[EmittedFile] = []
    for skill in surface.skills:
        content = f"{managed_header('.md')}\n\n# {skill.slug}\n\n> {skill.description}\n\n{skill.body}"
        files.append(EmittedFile(relpath=SKILLS_DIR / f"{skill.slug}.md", content=content))
    return files


def _entrypoint(surface: ProjectSurface, project_root: Path) -> EmittedFile:
    """Short entrypoint: imports for what Gemini reads, a catalog for the rest."""
    imports = [rel for rel in _MEMORY_IMPORTS if (project_root / rel).is_file()]
    lines = [
        managed_header(".md"),
        "",
        f"# {surface.project_name} — Gemini CLI",
        "",
        "Surface générée par `grimoire host sync --host gemini`. Éditer ici est",
        "sans effet durable : la prochaine synchronisation régénère ce bloc.",
        "",
    ]
    if imports:
        lines += ["## Instructions et mémoire", ""]
        lines += [f"@{rel}" for rel in imports]
        lines.append("")
    lines += [
        "## Surfaces de l'hôte",
        "",
        f"- **Sous-agents** : {len(surface.agents)} — `.gemini/agents/`, chacun dans sa fenêtre de contexte.",
        f"- **Commandes** : {len(surface.commands)} — `.gemini/commands/grimoire/`.",
        f"- **Compétences** : {len(surface.skills)} — copies sous `_grimoire/hosts/skills/`, "
        "aucun chargement à la demande : lire le fichier quand la situation se présente.",
        "",
        "## Gouvernance",
        "",
        "- `BeforeTool` et `BeforeAgent` peuvent refuser une action "
        '(`{"decision": "deny"}`) ; c\'est là que la politique du projet s\'applique.',
        "- `SessionStart`, `AfterTool` et `PreCompress` ne font qu'informer.",
        "- `AfterAgent` et `SessionEnd` reçoivent la décision de clôture, mais **non vérifié** : "
        "aucun événement de fin de tour n'est documenté comme bloquant, donc aucun blocage n'est promis.",
        "",
    ]
    return EmittedFile(relpath=Path("GEMINI.md"), content="\n".join(lines), block=True)


def _degradations(surface: ProjectSurface) -> list[Degradation]:
    gaps: list[Degradation] = [
        Degradation(
            surface="skills",
            reason="Pas de chargement à la demande des compétences sur cet hôte.",
            fallback="compétences copiées sous `_grimoire/hosts/skills/`, listées dans `GEMINI.md`.",
        ),
        Degradation(
            surface="permissions",
            reason="Gemini CLI n'expose pas de table de permissions déclarative.",
            fallback="règles appliquées par le hook `BeforeTool` (mêmes refus, même formulation).",
        ),
        Degradation(
            surface="ask",
            reason="Gemini CLI ne permet pas d'interroger l'humain depuis un hook.",
            fallback="une décision `ask` est refusée avec son motif (échec fermé, voir `gemini_wire`).",
        ),
        Degradation(
            surface="hook stop verification",
            reason=(
                "`AfterAgent` et `SessionEnd` sont documentés comme hooks, pas comme points de "
                "blocage ; la CLI n'étant pas installée, le blocage de fin de tour n'est pas vérifié."
            ),
            fallback="la décision de clôture est transmise, sans verdict bloquant ; les gates restent opposables en CI.",
        ),
        Degradation(
            surface="session start blocking",
            reason="`SessionStart` n'est que consultatif (`additionalContext`) : aucun blocage au début de session.",
            fallback="la directive de session est injectée en contexte ; elle n'est pas opposable.",
        ),
        Degradation(
            surface="model affinity",
            reason=(
                "Le champ `model` d'un sous-agent prend un nom de modèle ; le kit n'a pas de "
                "correspondance documentée entre son affinité neutre et ces noms."
            ),
            fallback="`model` n'est pas émis ; le modèle de la session s'applique à chaque agent.",
        ),
        Degradation(
            surface="detached session",
            reason="Aucune session d'agent détachée n'est documentée pour Gemini CLI.",
            fallback="un agent tourne dans la session qui l'a lancé ; la reprise passe par `--resume <uuid>`.",
        ),
    ]
    for hook in surface.hooks:
        if hook.event in _GEMINI_EVENTS:
            continue
        gaps.append(
            Degradation(
                surface=f"hook {hook.event.value}",
                reason=f"Gemini CLI ne documente pas d'événement pour `{hook.event.value}`.",
                fallback="règle énoncée dans `GEMINI.md`, non opposable sur cet hôte.",
            )
        )
    return gaps


class GeminiEmitter(Emitter):
    host_id = HostId.GEMINI_CLI

    def plan(self, surface: ProjectSurface, project_root: Path) -> EmitPlan:
        launcher = resolve_hook_launcher(project_root)
        files: list[EmittedFile] = [_agent_file(agent, surface) for agent in surface.agents]
        files.extend(_command_file(command) for command in surface.commands)
        files.extend(_skill_copies(surface))
        files.append(_entrypoint(surface, project_root))
        degradations = [*Emitter.launcher_degradations(launcher), *_degradations(surface)]
        plan = EmitPlan(
            host_id=self.host_id,
            files=tuple(files),
            merges=(_settings_merge(surface, project_root, launcher),),
            degradations=tuple(degradations),
            owned_globs=(
                ".gemini/agents/*.md",
                ".gemini/commands/grimoire/*.toml",
                "_grimoire/hosts/skills/*.md",
            ),
        )
        return with_rule_layers(plan, project_root)
