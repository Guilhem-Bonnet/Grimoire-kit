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
    OWNED_COMMAND_MARKERS,
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
from grimoire.missions.verifiability import Verifiability

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

#: Reasoning demand -> model tier, avant croisement avec ``cost`` dans
#: :func:`_model_for`. ``inherit`` reste le défaut honnête du milieu : sans
#: signal fort dans un sens ou dans l'autre, le modèle de la session est une
#: meilleure estimation que la nôtre.
_MODEL_BY_REASONING = {"high": "opus", "medium": "inherit", "low": "haiku"}

#: Budget de tours par défaut d'un sous-agent, faute d'un ``max_turns:``
#: déclaré dans sa fiche — assez pour une tranche de travail bornée (lire,
#: modifier, vérifier), pas assez pour une dérive silencieuse.
_DEFAULT_MAX_TURNS = 30

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
_MATCHERLESS = {
    HookEvent.SESSION_START,
    HookEvent.USER_PROMPT_SUBMIT,
    HookEvent.PRE_COMPACT,
    # A subagent starting is not a tool call — nothing to match on.
    HookEvent.SUBAGENT_START,
}

#: Alias local vers la liste partagée : la reconnaissance d'un hook écrit par
#: le kit est la même question ici et chez Copilot, elle ne doit pas diverger.
_OWNED_COMMAND_MARKERS = OWNED_COMMAND_MARKERS


def _model_for(agent: AgentSpec) -> str:
    """Croise ``reasoning`` et ``cost`` : le premier signal fort gagne.

    ``reasoning: high`` l'emporte toujours — un agent qui a besoin de
    raisonnement profond ne doit pas être rétrogradé parce qu'il est aussi
    déclaré peu coûteux (ex. le concierge : gros raisonnement de triage, mais
    invoqué à chaque tour, donc `cost: low`). En dessous de ce plancher,
    `cost: low` prime sur `reasoning` : un agent qu'on veut bon marché doit
    rester bon marché même à raisonnement moyen. Ce n'est qu'en l'absence de
    tout signal fort — ni gros raisonnement, ni petit coût, ni petit
    raisonnement — que le modèle de la session (`inherit`) reste le défaut
    honnête du milieu.
    """
    reasoning = agent.affinity.reasoning.lower()
    if reasoning == "high":
        return "opus"
    if agent.affinity.cost.lower() == "low":
        return "haiku"
    return _MODEL_BY_REASONING.get(reasoning, "inherit")


def _effort_for(agent: AgentSpec) -> str:
    """``reasoning: high`` -> ``effort: high`` ; tout le reste (medium, low) ->
    ``effort: low``. Binaire à dessein : Claude Code n'a pas de palier
    intermédiaire pour ce champ, et un agent qui n'a pas explicitement demandé
    un gros raisonnement n'a pas besoin du budget d'effort le plus large."""
    return "high" if agent.affinity.reasoning.lower() == "high" else "low"


def _max_turns_for(agent: AgentSpec) -> int:
    """La fiche d'agent l'emporte quand elle déclare ``max_turns:`` ; sinon le
    défaut borné (:data:`_DEFAULT_MAX_TURNS`)."""
    return agent.max_turns if agent.max_turns is not None else _DEFAULT_MAX_TURNS


def _dispatch_policy_section() -> str:
    """Section "Politique de dispatch" (issue #329) — quel modèle pour quelle tâche.

    Only the entry persona dispatches other personas as sub-agents (every
    other role "ne clos pas la tâche globale" — see the ``role`` branch
    below), so only its file needs the rule. The three labels are quoted
    from :class:`~grimoire.missions.verifiability.Verifiability`, not
    paraphrased: the tier a sub-agent gets must never drift from what
    ``grimoire task dispatch`` already computes for the same task from the
    same source of truth.
    """
    return f"""## Politique de dispatch

Avant de dispatcher un sous-agent, choisis son modèle selon la classe de
vérifiabilité de la tâche (celle que `grimoire task dispatch` calcule) :

- **V0** — {Verifiability.V0.explanation} → `haiku`.
- **V1** — {Verifiability.V1.explanation} → `sonnet`.
- **V2** — {Verifiability.V2.explanation} → le modèle de la session (le tien).

Exige de chaque sous-agent, en fin de réponse, un bloc ```grimoire-uncertainties```
portant une liste JSON d'objets `{{"where": ..., "what": ..., "why": ...}}` —
un par point qu'il n'a pas pu vérifier. Un sous-agent qui clôt sans ce bloc
n'a pas rendu un résultat vérifiable, il a rendu une opinion.
"""


def _agent_file(agent: AgentSpec, surface: ProjectSurface) -> EmittedFile:
    tools = map_verbs(agent.tools, _TOOL_TABLE)
    fields: dict[str, Any] = {
        "name": agent.name,
        "description": agent.description,
        "tools": ", ".join(tools),
        "model": _model_for(agent),
        "effort": _effort_for(agent),
        "maxTurns": _max_turns_for(agent),
    }
    if not agent.entry_point:
        # Seul le point d'entrée tourne en avant-plan, attendu par l'humain ;
        # tout autre sous-agent est dispatché en invisible et peut tourner en
        # tâche de fond sans qu'on l'attende.
        fields["background"] = True
    header = Emitter.frontmatter(fields)
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
    if agent.entry_point:
        body = f"{body}\n{_dispatch_policy_section()}"
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
    HookEvent.POST_TOOL_USE_FAILURE: "PostToolUseFailure",
    HookEvent.SUBAGENT_START: "SubagentStart",
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
