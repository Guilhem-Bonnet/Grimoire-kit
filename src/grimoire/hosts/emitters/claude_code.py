"""Claude Code emitter — every Grimoire surface has a native counterpart here.

Sub-agents get their own context window and tool boundary, skills load on
demand, commands are slash commands, hooks can refuse a tool call or a turn,
and permissions are declarative. Nothing degrades; what the project declares is
what the host executes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from grimoire.bridges.schemas import HostId
from grimoire.hosts.emitters.base import (
    EmitPlan,
    EmittedFile,
    Emitter,
    JsonMerge,
    managed_header,
    map_verbs,
)
from grimoire.hosts.surface import (
    AgentSpec,
    CommandSpec,
    Enforcement,
    HookEvent,
    HookSpec,
    ProjectSurface,
    SkillSpec,
    ToolVerb,
)

HOST_ALIAS = "claude"
CLAUDE_DIR = Path(".claude")

_TOOL_TABLE: dict[ToolVerb, tuple[str, ...]] = {
    ToolVerb.READ: ("Read", "Glob"),
    ToolVerb.SEARCH: ("Grep", "Glob"),
    ToolVerb.EDIT: ("Edit", "Write"),
    ToolVerb.EXECUTE: ("Bash",),
    ToolVerb.WEB: ("WebFetch", "WebSearch"),
}

#: Neutral tool family -> the tools this host would use for it. Drives hook
#: matchers, so a hook fires on the calls it is about and on no others.
_MATCHER_TABLE: dict[str, tuple[str, ...]] = {
    "write": ("Edit", "Write", "MultiEdit", "NotebookEdit"),
    "execute": ("Bash",),
    "secret": ("Read",),
    "network": ("WebFetch", "WebSearch"),
}

#: Reasoning demand -> model tier. ``inherit`` is the honest default for the
#: middle: the session's own model is a better guess than ours.
_MODEL_BY_REASONING = {"high": "opus", "medium": "inherit", "low": "haiku"}

#: Tool families the declarative permission table covers *in full*, so a host
#: that has one gains nothing from also spawning a hook process for them. Only
#: ``secret`` qualifies: every credential family is expressed as a deny glob
#: (see :mod:`grimoire.hosts.secrets`). ``execute`` is deliberately absent — its
#: ask-list is a shortlist of the most common destructive commands, while the
#: decision checks a broader set of shapes the table cannot express.
#:
#: This is not a cosmetic trim. ``Read`` in the matcher means a process per file
#: read: measured at ~307 ms, on every read, in every session.
_DECLARATIVELY_COVERED = frozenset({"secret"})

#: Events whose configuration entries take no matcher.
_MATCHERLESS = {HookEvent.SESSION_START, HookEvent.USER_PROMPT_SUBMIT, HookEvent.PRE_COMPACT}

#: Any hook command containing one of these belongs to the kit and is replaced
#: on sync. The activation command is legacy: the session-start decision now
#: covers it, and leaving both installed injects the directive twice.
_OWNED_COMMAND_MARKERS = (
    "grimoire-hook",
    # Superseded invocations, kept as markers so a project installed by an
    # earlier kit is migrated rather than accumulating a second entry beside
    # the new one. Forgetting one is not a cosmetic bug: the merge stops
    # recognising its own entry, keeps it as foreign, and appends another on
    # every sync.
    "grimoire host hook",
    "grimoire standard activation-context",
)


def _model_for(agent: AgentSpec) -> str:
    return _MODEL_BY_REASONING.get(agent.affinity.reasoning.lower(), "inherit")


def _agent_file(agent: AgentSpec, surface: ProjectSurface) -> EmittedFile:
    tools = map_verbs(agent.tools, _TOOL_TABLE)
    header = Emitter.frontmatter(
        {
            "name": agent.name,
            "description": agent.description,
            "tools": ", ".join(tools),
            "model": _model_for(agent),
        }
    )
    role = (
        "Tu es le point d'entrée : quand la demande ne désigne pas clairement un rôle, c'est toi qui tranches."
        if agent.entry_point
        else "Tu es dispatché sur une tranche de travail précise ; tu ne clos pas la tâche globale."
    )
    body = f"""{header}
{managed_header(".md")}

Tu incarnes la persona Grimoire **{agent.name}** du projet {surface.project_name}.

1. Lis `{agent.definition_ref}` en entier : ce fichier porte la persona, ses
   règles et son protocole d'activation. Applique-les sans les résumer.
2. Lis `_grimoire/_memory/shared-context.md` s'il existe, pour l'état courant du
   projet.
3. {role}
4. Ne sors pas de ta frontière d'outils : {", ".join(v.value for v in agent.tools)}.
5. Rends un résultat vérifiable — chemins exacts, commandes réellement
   exécutées. Ce que tu n'as pas vérifié, dis-le comme non vérifié.
"""
    return EmittedFile(relpath=CLAUDE_DIR / "agents" / f"{agent.name}.md", content=body)


def _skill_file(skill: SkillSpec) -> EmittedFile:
    header = Emitter.frontmatter(
        {
            "name": skill.slug,
            "description": skill.description,
            "allowed-tools": ", ".join(map_verbs(skill.tools, _TOOL_TABLE)),
        }
    )
    content = f"{header}\n{managed_header('.md')}\n\n{skill.body}"
    return EmittedFile(relpath=CLAUDE_DIR / "skills" / skill.slug / "SKILL.md", content=content)


def _command_file(command: CommandSpec) -> EmittedFile:
    header = Emitter.frontmatter(
        {
            "description": command.description,
            "argument-hint": command.argument_hint,
            "allowed-tools": ", ".join(map_verbs(command.tools, _TOOL_TABLE)),
        }
    )
    body = command.body
    if command.argument_hint and "$ARGUMENTS" not in body:
        body = body.rstrip() + "\n\nArgument fourni : $ARGUMENTS\n"
    content = f"{header}\n{managed_header('.md')}\n\n{body}"
    return EmittedFile(relpath=CLAUDE_DIR / "commands" / f"{command.slug}.md", content=content)


def _matcher(hook: HookSpec, *, covered: frozenset[str] = frozenset()) -> str:
    """Host tool pattern for *hook*, minus families already enforced declaratively."""
    tools: list[str] = []
    for family in hook.matcher:
        if family in covered:
            continue
        for tool in _MATCHER_TABLE.get(family, ()):
            if tool not in tools:
                tools.append(tool)
    return "|".join(tools)


def _hook_entry(hook: HookSpec) -> dict[str, Any]:
    entry: dict[str, Any] = {}
    if hook.event not in _MATCHERLESS:
        matcher = _matcher(hook, covered=_DECLARATIVELY_COVERED)
        if matcher:
            entry["matcher"] = matcher
    entry["hooks"] = [
        {
            "type": "command",
            "command": Emitter.hook_command(HOST_ALIAS, _WIRE_NAMES[hook.event]),
            "timeout": hook.timeout,
        }
    ]
    return entry


def _permission_rules(surface: ProjectSurface) -> dict[str, list[str]]:
    """Translate neutral ``verb:target`` rules into this host's syntax."""

    def render(rule: str) -> str | None:
        verb, _, target = rule.partition(":")
        if not target:
            return None
        if verb == "read":
            return f"Read(./{target.lstrip('./')})"
        if verb == "execute":
            if target.endswith("*"):
                return f"Bash({target.rstrip('*').rstrip()}:*)"
            return f"Bash({target})"
        return None

    out: dict[str, list[str]] = {}
    for bucket, rules in (
        ("deny", surface.permissions.deny),
        ("ask", surface.permissions.ask),
        ("allow", surface.permissions.allow),
    ):
        rendered = [r for r in (render(rule) for rule in rules) if r]
        if rendered:
            out[bucket] = rendered
    return out


def _settings_merge(surface: ProjectSurface) -> JsonMerge:
    hooks_by_event: dict[str, list[dict[str, Any]]] = {}
    for hook in surface.hooks:
        hooks_by_event.setdefault(_WIRE_NAMES[hook.event], []).append(_hook_entry(hook))
    permissions = _permission_rules(surface)

    def merge(data: dict[str, Any]) -> dict[str, Any]:
        hooks = data.get("hooks")
        if not isinstance(hooks, dict):
            hooks = {}
        for event, entries in hooks_by_event.items():
            existing = hooks.get(event)
            kept = [entry for entry in (existing if isinstance(existing, list) else []) if not _owned_entry(entry)]
            hooks[event] = kept + entries
        # An event we no longer install must lose its stale entry, or a project
        # keeps firing a rule its surface has dropped.
        for event, existing in list(hooks.items()):
            if event in hooks_by_event or not isinstance(existing, list):
                continue
            kept = [entry for entry in existing if not _owned_entry(entry)]
            if kept:
                hooks[event] = kept
            else:
                hooks.pop(event)
        data["hooks"] = hooks

        if permissions:
            current = data.get("permissions")
            if not isinstance(current, dict):
                current = {}
            for bucket, rules in permissions.items():
                existing_rules = current.get(bucket)
                merged = list(existing_rules) if isinstance(existing_rules, list) else []
                for rule in rules:
                    if rule not in merged:
                        merged.append(rule)
                current[bucket] = merged
            data["permissions"] = current
        return data

    return JsonMerge(relpath=CLAUDE_DIR / "settings.json", merge=merge, label=".claude/settings.json")


def _owned_entry(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    for hook in entry.get("hooks", []) if isinstance(entry.get("hooks"), list) else []:
        command = hook.get("command") if isinstance(hook, dict) else ""
        if isinstance(command, str) and any(marker in command for marker in _OWNED_COMMAND_MARKERS):
            return True
    command = entry.get("command")
    return isinstance(command, str) and any(marker in command for marker in _OWNED_COMMAND_MARKERS)


_WIRE_NAMES: dict[HookEvent, str] = {
    HookEvent.SESSION_START: "SessionStart",
    HookEvent.USER_PROMPT_SUBMIT: "UserPromptSubmit",
    HookEvent.PRE_TOOL_USE: "PreToolUse",
    HookEvent.POST_TOOL_USE: "PostToolUse",
    HookEvent.SUBAGENT_STOP: "SubagentStop",
    HookEvent.PRE_COMPACT: "PreCompact",
    HookEvent.STOP: "Stop",
}


class ClaudeCodeEmitter(Emitter):
    host_id = HostId.CLAUDE_CODE_CLI

    def plan(self, surface: ProjectSurface, project_root: Path) -> EmitPlan:
        del project_root  # every path is project-relative
        files: list[EmittedFile] = []
        files.extend(_agent_file(agent, surface) for agent in surface.agents)
        files.extend(_skill_file(skill) for skill in surface.skills)
        files.extend(_command_file(command) for command in surface.commands)
        blocking = [h for h in surface.hooks if h.enforcement is Enforcement.BLOCKING]
        files.append(_readme(surface, blocking))
        return EmitPlan(
            host_id=self.host_id,
            files=tuple(files),
            merges=(_settings_merge(surface),),
            degradations=(),
        )


def _readme(surface: ProjectSurface, blocking: list[HookSpec]) -> EmittedFile:
    lines = [
        managed_header(".md"),
        "",
        f"# Surface Claude Code — {surface.project_name}",
        "",
        "Fichiers générés par `grimoire host sync --host claude`. Les éditer ici est",
        "sans effet durable : la prochaine synchronisation les régénère. Pour",
        "personnaliser, modifiez la source (persona dans `_grimoire/`, skill ou",
        "commande dans le kit) puis resynchronisez.",
        "",
        "| Surface | Contenu |",
        "|---|---|",
        f"| Sous-agents | {len(surface.agents)} — `.claude/agents/` |",
        f"| Skills | {len(surface.skills)} — `.claude/skills/` |",
        f"| Commandes | {len(surface.commands)} — `.claude/commands/` |",
        f"| Hooks | {len(surface.hooks)} — `.claude/settings.json` |",
        "",
    ]
    if blocking:
        lines.append("## Hooks bloquants")
        lines.append("")
        for hook in blocking:
            lines.append(f"- `{_WIRE_NAMES[hook.event]}` — {hook.rationale}")
        lines.append("")
        lines.append(
            "Un hook bloquant refuse une action ou une clôture. Pour désactiver "
            "temporairement la gouvernance, retirez l'entrée de `.claude/settings.json` "
            "et n'exécutez pas `grimoire host sync` avant de l'avoir remise."
        )
        lines.append("")
    return EmittedFile(relpath=CLAUDE_DIR / "README.md", content="\n".join(lines))
