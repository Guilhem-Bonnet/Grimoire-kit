"""Emitter for hosts whose only executable surface is an instructions file.

Codex, Cursor and Gemini CLI read a prose entrypoint and speak MCP; they do not
load sub-agent files, skill folders, slash commands, or lifecycle hooks. The
honest move is not to write files they will ignore, but to write **one** file
that says what exists and how to reach it — every persona by path, every skill
by trigger, every command as the CLI invocation it wraps — and to state plainly
that governance here is a rule, not a constraint.

That last sentence is the point of the degradation list: a project that cannot
enforce its gates in the host should know that CI is the only place left where
they hold.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.bridges.schemas import HostId
from grimoire.hosts.capabilities import HostProfile, profile_for
from grimoire.hosts.emitters.base import Degradation, EmitPlan, EmittedFile, Emitter, managed_header
from grimoire.hosts.surface import Enforcement, ProjectSurface

#: Where each prose-only host looks first.
_ENTRYPOINTS: dict[HostId, Path] = {
    HostId.CODEX: Path("AGENTS.md"),
    HostId.GEMINI_CLI: Path("GEMINI.md"),
    HostId.CURSOR: Path(".cursor/rules/grimoire.mdc"),
}

CANONICAL_INSTRUCTIONS = ".github/copilot-instructions.md"


def _catalog(surface: ProjectSurface, profile: HostProfile) -> str:
    lines: list[str] = []
    if profile.host_id is HostId.CURSOR:
        # Cursor rule files need a header to apply unconditionally.
        lines += ["---", "description: Grimoire — agents, compétences et gouvernance", "alwaysApply: true", "---", ""]
    lines += [
        managed_header(".md"),
        "",
        f"# {surface.project_name} — {profile.display_name}",
        "",
        f"Projet **Grimoire Kit**. Instructions canoniques : [`{CANONICAL_INSTRUCTIONS}`]({CANONICAL_INSTRUCTIONS}).",
        "",
        "Cet hôte lit des instructions et parle MCP ; il n'exécute ni sous-agents, ni",
        "compétences chargées à la demande, ni hooks de cycle de vie. Ce fichier tient",
        "donc lieu de catalogue : tout ce qui suit s'active en lisant un fichier ou en",
        "lançant une commande.",
        "",
    ]

    if surface.agents:
        lines += ["## Personas", "", "| Persona | Rôle | Définition | Outils |", "|---|---|---|---|"]
        for agent in surface.agents:
            marker = " (entrée)" if agent.entry_point else ""
            tools = ", ".join(v.value for v in agent.tools)
            lines.append(f"| `{agent.name}`{marker} | {agent.description} | `{agent.definition_ref}` | {tools} |")
        lines += [
            "",
            "Activer une persona = lire sa définition en entier et l'appliquer, sans la",
            "résumer. Aucun contexte n'est isolé sur cet hôte : la persona s'ajoute à la",
            "conversation courante au lieu de s'exécuter à part.",
            "",
        ]

    if surface.skills:
        lines += ["## Compétences", "", "| Compétence | Quand l'utiliser | Contenu |", "|---|---|---|"]
        for skill in surface.skills:
            lines.append(f"| `{skill.slug}` | {skill.description} | `_grimoire/hosts/skills/{skill.slug}.md` |")
        lines += ["", "Aucun chargement automatique ici : lire le fichier quand la situation décrite se présente.", ""]

    if surface.commands:
        lines += ["## Commandes", "", "| Commande | Effet |", "|---|---|"]
        for command in surface.commands:
            hint = f" {command.argument_hint}" if command.argument_hint else ""
            lines.append(f"| `grimoire host run {command.slug}{hint}` | {command.description} |")
        lines.append("")

    if surface.mcp_servers:
        names = ", ".join(f"`{s.name}`" for s in surface.mcp_servers)
        lines += ["## MCP", "", f"Serveurs déclarés dans `.mcp.json` : {names}.", ""]

    if surface.hooks:
        lines += ["## Gouvernance", ""]
        blocking = [h for h in surface.hooks if h.enforcement is Enforcement.BLOCKING]
        for hook in surface.hooks:
            lines.append(f"- **{hook.event.value}** — {hook.rationale}")
        lines += [
            "",
            "Sur cet hôte, ces règles ne sont pas opposables : rien n'intercepte un appel",
            "d'outil ni une fin de tour. Elles tiennent par discipline, et par la CI.",
            "",
        ]
        if blocking and surface.governed:
            lines += [
                "Avant toute conclusion de tâche :",
                "",
                "```bash",
                "grimoire standard gate check --strict",
                "grimoire standard verify .",
                "```",
                "",
                "Une clôture sans gates verts est une tâche non terminée.",
                "",
            ]

    return "\n".join(lines)


def _skill_copies(surface: ProjectSurface) -> list[EmittedFile]:
    """Skill bodies, readable at a stable path since no host loads them here."""
    files: list[EmittedFile] = []
    for skill in surface.skills:
        content = f"{managed_header('.md')}\n\n# {skill.slug}\n\n> {skill.description}\n\n{skill.body}"
        files.append(EmittedFile(relpath=Path("_grimoire/hosts/skills") / f"{skill.slug}.md", content=content))
    return files


class GenericEmitter(Emitter):
    """One class, three hosts: the entrypoint path is the only difference."""

    def __init__(self, host_id: HostId) -> None:
        self.host_id = host_id

    def plan(self, surface: ProjectSurface, project_root: Path) -> EmitPlan:
        del project_root
        profile = profile_for(self.host_id)
        entrypoint = _ENTRYPOINTS.get(self.host_id, Path("AGENTS.md"))
        files = [EmittedFile(relpath=entrypoint, content=_catalog(surface, profile))]
        files.extend(_skill_copies(surface))
        degradations = [
            Degradation(
                surface="subagents",
                reason=f"{profile.display_name} ne charge pas de définition d'agent isolée.",
                fallback="personas listées dans l'entrypoint, à lire et appliquer manuellement.",
            ),
            Degradation(
                surface="skills",
                reason="Pas de chargement à la demande.",
                fallback="compétences copiées sous `_grimoire/hosts/skills/`, à lire quand la situation l'exige.",
            ),
            Degradation(
                surface="commands",
                reason="Pas de commandes utilisateur natives.",
                fallback="chaque commande est exposée comme invocation `grimoire host run <slug>`.",
            ),
            Degradation(
                surface="hooks",
                reason="Aucun événement de cycle de vie exposé par cet hôte.",
                fallback="gouvernance énoncée comme règle dans l'entrypoint, opposable en CI uniquement.",
            ),
        ]
        return EmitPlan(host_id=self.host_id, files=tuple(files), merges=(), degradations=tuple(degradations))
