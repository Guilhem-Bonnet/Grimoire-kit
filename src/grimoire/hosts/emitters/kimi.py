"""Kimi Code CLI (Moonshot) : agents, compétences, MCP et instructions de projet (#1281, L8).

Ce que cet émetteur écrit, et la page de la doc de Kimi Code qui dit que l'hôte le lit
(https://moonshotai.github.io/kimi-code/en/, lue le 2026-11-22, Kimi Code 2.7.2 annoncée ;
la CLI n'est pas installée sur le poste du kit) :

- ``.kimi-code/agents/<nom>.md`` — **agents** de projet, Markdown à frontmatter YAML
  (``name`` en kebab-case, ``description`` obligatoire), le corps est le prompt système ;
  lancés par ``kimi --agent <nom>`` (customization/agents) ;
- ``.agents/skills/<slug>/SKILL.md`` — **compétences** de projet, ``name`` et
  ``description`` (customization/skills) ; même écrivain que Codex et Gemini ;
- ``.kimi-code/mcp.json`` — ``{"mcpServers": {...}}``, une entrée ``command`` = serveur
  stdio (customization/mcp). Chargé seulement pour un espace de travail approuvé ;
- ``AGENTS.md`` — instructions de projet, générées par ``/init`` (reference/slash-commands).
  Le kit n'y possède qu'un bloc délimité, que Codex écrit aussi : Kimi ne l'écrit que déclaré
  dans ``hosts.enabled`` sans Codex, pour que deux émetteurs ne s'y écrivent jamais dessus.

Ce qui reste dégradé, et pourquoi :

- **hooks** : Kimi Code documente des hooks bloquants (``PreToolUse``, ``UserPromptSubmit``,
  ``Stop`` : code 2 ou ``permissionDecision: deny``), mais seulement dans
  ``~/.kimi-code/config.toml`` ou un plugin : « there is no project-level hook file »
  (customization/hooks). Le kit n'écrit pas la configuration de l'utilisateur : aucune garde ;
- **model** : un champ ``model`` d'agent est ignoré (customization/agents) : jamais émis ;
- **commandes** : aucun dossier de commandes de projet (seuls les plugins en déclarent) ;
- **outils** : les noms d'outils de Kimi ne sont pas listés par la doc relue ; ``tools`` n'est
  pas émis, l'agent garde tous les outils et sa frontière n'est qu'énoncée.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from grimoire.bridges.schemas import HostId
from grimoire.core.grounding import GROUNDING_RULE, UNCERTAINTIES_BLOCK_RULE
from grimoire.hosts.emitters.agent_skills import SHARED_SKILLS_GLOB, shared_skills
from grimoire.hosts.emitters.base import (
    Degradation,
    EmitPlan,
    EmittedFile,
    Emitter,
    JsonMerge,
    MergeRefusedError,
    context_load_instruction,
    managed_header,
    persona_section,
)
from grimoire.hosts.emitters.copilot import _dispatch_policy_section
from grimoire.hosts.emitters.copilot_mcp import KIT_SERVERS
from grimoire.hosts.emitters.rules import with_rule_layers
from grimoire.hosts.hook_launcher import expand_project_dir
from grimoire.hosts.surface import AgentSpec, McpServerSpec, ProjectSurface

HOST_ALIAS = "kimi"
KIMI_DIR = Path(".kimi-code")
AGENTS_DIR = KIMI_DIR / "agents"
MCP_FILE = KIMI_DIR / "mcp.json"
ENTRYPOINT = Path("AGENTS.md")

#: ``name`` d'un agent Kimi : kebab-case (customization/agents).
_KEBAB = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")

#: Fichiers que ``AGENTS.md`` cite quand ils existent (aucun import n'est documenté).
_MEMORY_FILES: tuple[str, ...] = (
    ".github/copilot-instructions.md",
    "_grimoire/_memory/shared-context.md",
    "_grimoire/_memory/decisions-log.md",
    "_grimoire/_memory/failure-museum.md",
)


def _agent_file(agent: AgentSpec, surface: ProjectSurface) -> EmittedFile:
    header = Emitter.frontmatter({"name": agent.name, "description": agent.description})
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


def _mcp_entry(server: McpServerSpec, project_root: Path) -> dict[str, Any]:
    if server.url and not server.command:
        return {"url": server.url}
    # Aucune variable de racine n'est documentée pour `mcpServers` : commande écrite absolue (#760).
    entry: dict[str, Any] = {"command": expand_project_dir(server.command, project_root)}
    if server.args:
        entry["args"] = list(server.args)
    return entry


def _mcp_merge(servers: tuple[McpServerSpec, ...], project_root: Path) -> JsonMerge | None:
    """Ajouter à ``.kimi-code/mcp.json`` les seuls serveurs du kit qu'il ne déclare pas encore."""
    entries = {s.name: _mcp_entry(s, project_root) for s in servers if s.name in KIT_SERVERS}
    if not entries:
        return None

    def merge(data: dict[str, Any]) -> dict[str, Any]:
        current = data.get("mcpServers", {})
        if not isinstance(current, dict):
            # Le remplacer effacerait les serveurs de l'utilisateur : fichier préservé, conflit signalé.
            msg = "`mcpServers` n'est pas un objet"
            raise MergeRefusedError(msg)
        for name, entry in entries.items():
            # Un serveur déjà déclaré est celui de l'utilisateur : on n'ajoute que ce qui manque.
            current.setdefault(name, entry)
        data["mcpServers"] = current
        return data

    return JsonMerge(relpath=MCP_FILE, merge=merge, label=MCP_FILE.as_posix())


def _entrypoint(surface: ProjectSurface, project_root: Path, agents: list[AgentSpec]) -> EmittedFile:
    lines = [
        managed_header(".md"),
        "",
        f"# {surface.project_name} — Kimi Code",
        "",
        "Surface générée par `grimoire host sync --host kimi`. Éditer ce bloc est",
        "sans effet durable : la prochaine synchronisation le régénère.",
        "",
    ]
    present = [rel for rel in _MEMORY_FILES if (project_root / rel).is_file()]
    if present:
        lines += ["## Instructions et mémoire", "", *(f"- [`{rel}`]({rel})" for rel in present), ""]
    if agents:
        lines += ["## Personas", ""]
        for agent in agents:
            marker = " (entrée)" if agent.entry_point else ""
            lines.append(f"- `{agent.name}`{marker} — {agent.description} (`kimi --agent {agent.name}`)")
        lines.append("")
    lines += [
        "## Surfaces de l'hôte",
        "",
        f"- **Agents** : {len(agents)} — `.kimi-code/agents/`, à lancer par `kimi --agent <nom>`.",
        f"- **Compétences** : {len(surface.skills)} — `.agents/skills/`, chargées à la demande (`/skill:<nom>`).",
        "- **MCP** : `.kimi-code/mcp.json`, chargé une fois l'espace de travail approuvé.",
        "",
        "## Règle de source",
        "",
        GROUNDING_RULE,
        "",
        UNCERTAINTIES_BLOCK_RULE.replace("Termine ta réponse", "Toute réponse d'analyse ou de revue se termine"),
        "",
    ]
    if surface.commands:
        lines += ["## Commandes", "", "| Commande | Effet |", "|---|---|"]
        for command in surface.commands:
            hint = f" {command.argument_hint}" if command.argument_hint else ""
            lines.append(f"| `grimoire host run {command.slug}{hint}` | {command.description} |")
        lines.append("")
    if surface.hooks:
        lines += ["## Gouvernance", ""]
        lines += [f"- **{hook.event.value}** — {hook.rationale}" for hook in surface.hooks]
        lines += [
            "",
            "Sur Kimi Code, ces règles ne sont pas opposables : le kit n'écrit aucun hook (Kimi",
            "Code n'en lit pas au niveau du projet). Elles tiennent par discipline, et par la CI.",
            "",
        ]
        if surface.governed:
            lines += [
                "Avant toute conclusion de tâche :",
                "",
                "```bash",
                "grimoire standard gate check --strict",
                "grimoire standard verify .",
                "```",
                "",
            ]
    return EmittedFile(relpath=ENTRYPOINT, content="\n".join(lines), block=True)


def _declared_hosts(project_root: Path) -> set[HostId] | None:
    """Hôtes de ``hosts.enabled`` ; ``None`` sans déclaration (ou config illisible)."""
    from grimoire.core.config import GrimoireConfig
    from grimoire.core.exceptions import GrimoireConfigError
    from grimoire.hosts.capabilities import resolve_host

    try:
        declared = GrimoireConfig.from_yaml(project_root / "project-context.yaml").hosts.enabled
    except GrimoireConfigError:
        return None
    if declared is None:
        return None
    return {host for alias in declared if (host := resolve_host(alias)) is not None}


def _entry_owner(project_root: Path) -> str:
    """Qui tient le bloc du kit dans ``AGENTS.md`` : ``""`` pour Kimi Code, sinon la raison de s'abstenir.

    Codex écrit le même bloc ; deux écrivains d'un même bloc s'écraseraient. Kimi ne l'écrit donc que
    déclaré dans ``hosts.enabled`` sans Codex. Une décision lue dans la seule déclaration, jamais dans la
    détection : celle-ci change dès que ``.kimi-code/`` existe, et le bloc irait et viendrait.
    """
    declared = _declared_hosts(project_root)
    if declared is None or HostId.KIMI_CODE not in declared:
        return "Kimi Code n'est pas déclaré dans `hosts.enabled` : le bloc du kit dans AGENTS.md n'est pas écrit."
    if HostId.CODEX in declared:
        return "Codex est activé et tient déjà le bloc du kit dans AGENTS.md, que Kimi Code lit aussi."
    return ""


def _degradations(surface: ProjectSurface, rejected_agents: list[str], entry_gap: str) -> list[Degradation]:
    gaps = [
        Degradation(
            surface="hooks",
            reason=(
                "Kimi Code documente des hooks bloquants, mais seulement dans `~/.kimi-code/config.toml` ou un "
                "plugin : aucun fichier de hooks de projet. Le kit n'écrit pas la configuration de l'utilisateur."
            ),
            fallback="aucune garde ni clôture sur cet hôte ; gouvernance énoncée dans AGENTS.md, opposable en CI.",
        ),
        Degradation(
            surface="commands",
            reason="Aucun emplacement de commande de projet documenté (seuls les plugins en déclarent).",
            fallback="chaque commande est exposée dans AGENTS.md comme invocation `grimoire host run <slug>`.",
        ),
        Degradation(
            surface="model affinity",
            reason="Kimi Code ignore le champ `model` d'un agent ; le modèle des sous-agents se règle pour tous.",
            fallback="`model` n'est pas émis ; `default_model` et `[secondary_model]` de la configuration s'appliquent.",
        ),
        Degradation(
            surface="tool boundary",
            reason="Les noms d'outils de Kimi Code ne sont pas listés par la doc relue : `tools` n'est pas émis.",
            fallback="l'agent garde tous les outils ; sa frontière est énoncée dans son prompt, non imposée.",
        ),
        Degradation(
            surface="subagents",
            reason="L'agent par défaut délègue à coder, explore et plan ; les agents du projet ne lui sont pas ajoutés.",
            fallback="lancer une persona par `kimi --agent <nom>`.",
        ),
        Degradation(
            surface="mcp trust",
            reason="`.kimi-code/mcp.json` n'est chargé que pour un espace de travail approuvé (`require_trust`).",
            fallback="approuver l'espace de travail dans Kimi Code, ou déclarer le serveur dans `~/.kimi-code/mcp.json`.",
        ),
    ]
    others = [s.name for s in surface.mcp_servers if s.name not in KIT_SERVERS]
    if others:
        gaps.append(
            Degradation(
                surface="mcp config",
                reason=f"Serveurs de `.mcp.json` hors kit non traduits (leur `env` n'est pas connu) : {', '.join(others)}.",
                fallback="les déclarer à la main dans `.kimi-code/mcp.json`.",
            )
        )
    if rejected_agents:
        gaps.append(
            Degradation(
                surface="agent names",
                reason=f"Nom d'agent hors kebab-case, refusé par Kimi Code : {', '.join(map(repr, rejected_agents))}.",
                fallback="agent non écrit sous `.kimi-code/agents/` ; le renommer dans sa source.",
            )
        )
    if entry_gap:
        gaps.append(
            Degradation(
                surface="instructions",
                reason=entry_gap,
                fallback="Kimi Code lit AGENTS.md tel qu'il est ; ses agents et compétences restent écrits à leur place.",
            )
        )
    return gaps


class KimiCodeEmitter(Emitter):
    host_id = HostId.KIMI_CODE

    def plan(self, surface: ProjectSurface, project_root: Path) -> EmitPlan:
        agents = [agent for agent in surface.agents if _KEBAB.fullmatch(agent.name)]
        rejected = [agent.name for agent in surface.agents if not _KEBAB.fullmatch(agent.name)]
        files: list[EmittedFile] = [_agent_file(agent, surface) for agent in agents]
        skill_files, skill_gaps = shared_skills(surface.skills)
        files.extend(skill_files)
        entry_gap = _entry_owner(project_root)
        if not entry_gap:
            files.append(_entrypoint(surface, project_root, agents))
        mcp = _mcp_merge(surface.mcp_servers, project_root)
        plan = EmitPlan(
            host_id=self.host_id,
            files=tuple(files),
            merges=(mcp,) if mcp is not None else (),
            degradations=(*skill_gaps, *_degradations(surface, rejected, entry_gap)),
            owned_globs=(".kimi-code/agents/*.md", SHARED_SKILLS_GLOB),
        )
        return with_rule_layers(plan, project_root)
