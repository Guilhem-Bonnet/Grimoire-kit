"""GitHub Copilot emitter — same governance, VS Code's file layout.

Copilot executes as much as Claude Code does except one thing: it has no
declarative permission table. That single gap is handled explicitly rather than
dropped — the permission rules become the ``pre_tool_use`` hook's business, and
the difference is reported as a degradation instead of being papered over.

Layout follows the VS Code customization contract: agents in ``.github/agents``,
prompts (slash commands) in ``.github/prompts``, skills in ``.github/skills``,
hooks as JSON in ``.github/hooks``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from grimoire.bridges.schemas import HostId
from grimoire.hosts.emitters.base import (
    OWNED_COMMAND_MARKERS,
    Degradation,
    EmitPlan,
    EmittedFile,
    Emitter,
    context_load_instruction,
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

HOST_ALIAS = "copilot"
GH_DIR = Path(".github")

_TOOL_TABLE: dict[ToolVerb, tuple[str, ...]] = {
    ToolVerb.READ: ("read",),
    ToolVerb.SEARCH: ("search",),
    ToolVerb.EDIT: ("edit",),
    ToolVerb.EXECUTE: ("execute",),
    ToolVerb.WEB: ("fetch",),
}

_WIRE_NAMES: dict[HookEvent, str] = {
    HookEvent.SESSION_START: "SessionStart",
    HookEvent.USER_PROMPT_SUBMIT: "UserPromptSubmit",
    HookEvent.PRE_TOOL_USE: "PreToolUse",
    HookEvent.POST_TOOL_USE: "PostToolUse",
    # Same spelling as Claude Code (see `runtime._WIRE_EVENT_NAMES`); the
    # capability manifest, not this table, is what says whether Copilot wires
    # a hook that targets one of them.
    HookEvent.POST_TOOL_USE_FAILURE: "PostToolUseFailure",
    HookEvent.SUBAGENT_START: "SubagentStart",
    HookEvent.SUBAGENT_STOP: "SubagentStop",
    HookEvent.PRE_COMPACT: "PreCompact",
    HookEvent.STOP: "Stop",
}


def _attached_skill_section(skill: SkillSpec) -> str:
    """Fold *skill*'s body into the file of the agent that owns it.

    Same reasoning as the Claude Code emitter (see there): the custom-agent
    contract (``.github/agents/*.agent.md``) accepts arbitrary Markdown body
    and is read only when that agent is activated, so folding the skill body
    in achieves the same lazy attachment — the format permits the
    equivalent, no :class:`Degradation` needed for this part.
    """
    header = Emitter.frontmatter({"allowed-tools": list(map_verbs(skill.tools, _TOOL_TABLE))})
    return f"""
## Compétence attachée : {skill.name}

{header}
{skill.body}"""


def _agent_file(agent: AgentSpec, surface: ProjectSurface, owned_skills: tuple[SkillSpec, ...] = ()) -> EmittedFile:
    # Pas de `model` ici : le contrat documenté pour .github/agents/*.agent.md
    # (https://code.visualstudio.com/docs/copilot/customization/custom-agents)
    # accepte un nom de modèle explicite ou une liste de repli, mais ne
    # documente aucune valeur de sélection automatique équivalente à
    # `inherit` côté Claude Code. Inventer un nom de modèle serait mentir sur
    # ce que l'hôte sait faire ; la lacune est déclarée en toutes lettres dans
    # la dégradation « model affinity » du plan plutôt que d'être tue.
    header = Emitter.frontmatter(
        {
            "description": agent.description,
            "tools": list(map_verbs(agent.tools, _TOOL_TABLE)),
            "user-invocable": agent.entry_point,
        }
    )
    role = (
        "Point d'entrée : quand la demande ne désigne pas clairement un rôle, tranche toi-même."
        if agent.entry_point
        else "Agent routé en interne : tu traites une tranche de travail, tu ne clos pas la tâche globale."
    )
    content = f"""{header}
{managed_header(".md")}

Tu actives la persona Grimoire **{agent.name}** du projet {surface.project_name}.

1. Lis `{agent.definition_ref}` en entier — persona, règles, protocole d'activation.
2. {context_load_instruction(agent)}
3. {role}
4. Frontière d'outils : {", ".join(v.value for v in agent.tools)}. N'en sors pas.
5. Rends un résultat vérifiable ; signale comme non vérifié ce que tu n'as pas vérifié.
"""
    for skill in owned_skills:
        content = f"{content}\n{_attached_skill_section(skill)}"
    return EmittedFile(relpath=GH_DIR / "agents" / f"{agent.name}.agent.md", content=content)


def _skill_file(skill: SkillSpec) -> EmittedFile:
    header = Emitter.frontmatter({"name": skill.slug, "description": skill.description})
    content = f"{header}\n{managed_header('.md')}\n\n{skill.body}"
    return EmittedFile(relpath=GH_DIR / "skills" / skill.slug / "SKILL.md", content=content)


def _prompt_file(command: CommandSpec) -> EmittedFile:
    header = Emitter.frontmatter(
        {
            "description": command.description,
            "agent": "agent",
            "tools": list(map_verbs(command.tools, _TOOL_TABLE)),
        }
    )
    body = command.body
    if command.argument_hint and "${input" not in body:
        body = body.rstrip() + f"\n\nArgument fourni : ${{input:argument:{command.argument_hint}}}\n"
    content = f"{header}\n{managed_header('.md')}\n\n{body}"
    return EmittedFile(relpath=GH_DIR / "prompts" / f"{command.slug}.prompt.md", content=content)


def _hook_file(hook: HookSpec) -> EmittedFile:
    event = _WIRE_NAMES[hook.event]
    payload: dict[str, Any] = {
        "hooks": {
            event: [
                {
                    "type": "command",
                    "command": Emitter.hook_command(HOST_ALIAS, event),
                    "timeout": hook.timeout,
                }
            ]
        }
    }
    slug = hook.event.value.replace("_", "-")
    return EmittedFile(
        relpath=GH_DIR / "hooks" / f"grimoire-{slug}.json",
        content=json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        managed=False,
        owned_if_contains=OWNED_COMMAND_MARKERS,
    )


def _readme(surface: ProjectSurface, blocking: list[HookSpec], gaps: list[Degradation]) -> EmittedFile:
    attached = sum(len(agent.skills) for agent in surface.agents)
    transversal = len(surface.skills) - len({slug for agent in surface.agents for slug in agent.skills})
    lines = [
        managed_header(".md"),
        "",
        f"# Surface Copilot — {surface.project_name}",
        "",
        "Généré par `grimoire host sync --host copilot`. Source de vérité des",
        "instructions : `.github/copilot-instructions.md`.",
        "",
        "| Surface | Contenu |",
        "|---|---|",
        f"| Agents | {len(surface.agents)} — `.github/agents/` |",
        f"| Skills transversales | {transversal} — `.github/skills/` |",
        f"| Skills attachées | {attached} — repliées dans le fichier de leur agent |",
        f"| Prompts | {len([c for c in surface.commands if c.source != 'workflow'])} — `.github/prompts/` |",
        f"| Hooks | {len(surface.hooks)} — `.github/hooks/` |",
        "",
        "Les fichiers de hook ne portent pas de marqueur de gestion : ce sont des",
        "JSON purs. C'est la commande invoquée qui dit à qui le fichier",
        "appartient — `grimoire host sync` réécrit ceux qui appellent",
        "`grimoire-hook`, et préserve les autres en les signalant `[!]`.",
        "",
        "## Politique de dispatch",
        "",
        "Quand l'agent d'entrée route une tranche de travail vers une autre",
        "persona, la classe de vérifiabilité de la tâche (celle que",
        "`grimoire task dispatch` calcule) dit le niveau de confiance à lui",
        "accorder avant de croire son résultat sans relecture :",
        "",
        f"- **V0** — {Verifiability.V0.explanation}",
        f"- **V1** — {Verifiability.V1.explanation}",
        f"- **V2** — {Verifiability.V2.explanation}",
        "",
        "Cet hôte ne documente aucun nom de modèle par palier : voir la",
        "dégradation « model affinity » ci-dessous, le contrat",
        "`.github/agents/*.agent.md` n'offre rien d'équivalent à `inherit`.",
        "Ce qui reste vrai partout : toute persona routée doit clore sa",
        "réponse par un bloc ```grimoire-uncertainties``` — une liste JSON",
        'd\'objets `{"where": ..., "what": ..., "why": ...}` pour ce qu\'elle',
        "n'a pas pu vérifier. Sans ce bloc, la réponse n'est pas un résultat",
        "vérifiable, c'est une opinion.",
        "",
    ]
    if blocking:
        lines += ["## Hooks bloquants", ""]
        lines += [f"- `{_WIRE_NAMES[h.event]}` — {h.rationale}" for h in blocking]
        lines.append("")
    if gaps:
        lines += ["## Dégradations sur cet hôte", ""]
        lines += [f"- **{g.surface}** — {g.reason} Repli : {g.fallback}" for g in gaps]
        lines.append("")
    return EmittedFile(relpath=GH_DIR / "hooks" / "README.md", content="\n".join(lines))


class CopilotEmitter(Emitter):
    host_id = HostId.GITHUB_COPILOT

    def plan(self, surface: ProjectSurface, project_root: Path) -> EmitPlan:
        del project_root
        files: list[EmittedFile] = []
        by_slug = {skill.slug: skill for skill in surface.skills}
        attached_slugs = {slug for agent in surface.agents for slug in agent.skills}
        files.extend(
            _agent_file(
                agent,
                surface,
                owned_skills=tuple(by_slug[slug] for slug in agent.skills if slug in by_slug),
            )
            for agent in surface.agents
        )
        # A skill an agent owns is folded into that agent's own file above;
        # only skills nobody declared stay transversal, at project scope.
        files.extend(_skill_file(skill) for skill in surface.skills if skill.slug not in attached_slugs)
        # The kit's workflow prompts are already placed verbatim under
        # `.github/prompts/` by the scaffolder. Re-rendering them here would
        # make two writers for one path, and every sync would report a
        # conflict on a file nobody actually edited.
        files.extend(_prompt_file(c) for c in surface.commands if c.source != "workflow")
        files.extend(_hook_file(hook) for hook in surface.hooks)

        degradations: list[Degradation] = []
        if not surface.permissions.is_empty():
            degradations.append(
                Degradation(
                    surface="permissions",
                    reason="Copilot n'expose pas de table de permissions déclarative.",
                    fallback="règles appliquées par le hook PreToolUse (mêmes refus, même formulation).",
                )
            )
        degradations.append(
            Degradation(
                surface="hook matchers",
                reason="Les hooks VS Code ne filtrent pas par outil dans leur configuration.",
                fallback="le filtrage se fait dans la décision : un appel en lecture seule sort en `allow` sans effet.",
            )
        )
        degradations.append(
            Degradation(
                surface="model affinity",
                reason=(
                    "L'agent personnalisé VS Code (.github/agents/*.agent.md) accepte un nom de "
                    "modèle explicite ou une liste de repli via `model`, mais aucune valeur de "
                    "sélection automatique équivalente à `inherit` n'y est documentée (source : "
                    "https://code.visualstudio.com/docs/copilot/customization/custom-agents)."
                ),
                fallback="`model` n'est pas émis ; le modèle actuel du sélecteur VS Code s'applique à chaque agent.",
            )
        )
        blocking = [h for h in surface.hooks if h.enforcement is Enforcement.BLOCKING]
        files.append(_readme(surface, blocking, degradations))
        return EmitPlan(host_id=self.host_id, files=tuple(files), merges=(), degradations=tuple(degradations))
