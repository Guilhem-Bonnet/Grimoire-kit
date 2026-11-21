"""Émetteur Cursor — hooks, sous-agents, compétences, règles ; plus seulement de la prose.

Cursor expose davantage qu'un fichier de règles (documentation lue le 2026-11-21) :

- ``.cursor/hooks.json`` (https://cursor.com/docs/hooks) : ``{"version": 1,
  "hooks": {"<événement>": [{"command", "timeout", "matcher", "failClosed",
  "loop_limit"}]}}``, lancé depuis la racine du projet. ``preToolUse`` et
  ``beforeShellExecution`` peuvent refuser (``permission: "deny"``, ou code 2) ;
  ``stop`` ne refuse pas mais relance par ``followup_message``, dans la limite
  ``loop_limit`` ;
- ``.cursor/agents/*.md`` (https://cursor.com/docs/context/subagents) : frontmatter
  ``name``, ``description``, ``model`` (``inherit`` ou un identifiant Cursor),
  ``readonly``, ``is_background`` ;
- ``.cursor/skills/<slug>/SKILL.md`` (https://cursor.com/docs/context/skills) :
  ``name`` (minuscules, chiffres, tirets, égal au dossier) et ``description``
  obligatoires ;
- ``.cursor/rules/*.mdc`` : la règle d'entrée et les couches de règles, inchangées.

Cursor n'est pas installé sur le poste de développement et aucune capture réelle
n'existe : tout ce qui suit repose sur la documentation. La garde est donc écrite
mais déclarée ``advisory`` (refus documenté, non vérifié), jamais ``enforced``.

Ce qui reste dégradé est déclaré dans le plan (``Degradation``), jamais tu :
``beforeSubmitPrompt`` n'injecte aucun contexte, ``ask`` n'est pas appliqué sur
``preToolUse``, ``model`` n'est écrit que pour un palier que le projet nomme sous
``dispatch.cursor_models``, aucune commande utilisateur n'est émise, et la
configuration MCP n'est pas traduite.

``--event`` porte le nom neutre (``PreToolUse``), comme pour Gemini : la matrice
règle × hôte reconnaît un hook à cet argument, et le runtime relit le nom Cursor
de l'appel dans la charge (``hook_event_name``), voir :mod:`grimoire.hosts.cursor_wire`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from grimoire.bridges.schemas import HostId
from grimoire.core.grounding import GROUNDING_RULE, UNCERTAINTIES_BLOCK_RULE
from grimoire.hosts.cursor_wire import LOOP_LIMIT
from grimoire.hosts.decisions import DEFAULT_DECISION_BY_EVENT
from grimoire.hosts.decisions.dispatch_model import cursor_model_for
from grimoire.hosts.emitters.base import (
    OWNED_COMMAND_MARKERS,
    Degradation,
    EmitPlan,
    EmittedFile,
    Emitter,
    JsonMerge,
    context_load_instruction,
    managed_header,
    persona_section,
)
from grimoire.hosts.emitters.claude_code import FAIL_CLOSED_TIMEOUT, _model_for
from grimoire.hosts.emitters.copilot import _dispatch_policy_section
from grimoire.hosts.emitters.rules import with_rule_layers
from grimoire.hosts.hook_launcher import BARE, HookLauncher, resolve_hook_launcher
from grimoire.hosts.surface import (
    AgentSpec,
    Enforcement,
    HookEvent,
    HookSpec,
    ProjectSurface,
    SkillSpec,
    ToolVerb,
)

HOST_ALIAS = "cursor"
CURSOR_DIR = Path(".cursor")
HOOKS_FILE = CURSOR_DIR / "hooks.json"
AGENTS_DIR = CURSOR_DIR / "agents"
SKILLS_DIR = CURSOR_DIR / "skills"
ENTRYPOINT = CURSOR_DIR / "rules" / "grimoire.mdc"
#: Repli d'une compétence dont le slug n'est pas un ``name`` Cursor valide.
FALLBACK_SKILLS_DIR = Path("_grimoire/hosts/skills")

#: Version du schéma de ``hooks.json`` (doc : « Config schema version. Use 1 »).
HOOKS_VERSION = 1

#: ``name`` d'une compétence : « Lowercase letters, numbers, and hyphens only ».
_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

#: Outils Cursor par famille neutre, pour les matchers ``preToolUse``/``postToolUse``
#: (valeurs documentées : ``Shell``, ``Read``, ``Write``, ``Grep``, ``Delete``,
#: ``Task``, ``MCP:<outil>``). ``network`` n'a pas d'outil Cursor documenté.
_MATCHER_TABLE: dict[str, tuple[str, ...]] = {
    "execute": ("Shell",),
    "write": ("Write", "Delete"),
    "secret": ("Read", "Grep"),
    "delegation": ("Task",),
}

#: Événement neutre -> événement(s) Cursor. La garde avant outil se répartit sur deux
#: événements qui savent refuser : ``beforeShellExecution`` pour la famille
#: ``execute`` (il applique ``ask``), ``preToolUse`` pour le reste (``ask`` n'y est
#: pas appliqué, le runtime le rend en refus).
_CURSOR_EVENTS: dict[HookEvent, str] = {
    HookEvent.SESSION_START: "sessionStart",
    HookEvent.PRE_TOOL_USE: "preToolUse",
    HookEvent.POST_TOOL_USE: "postToolUse",
    HookEvent.POST_TOOL_USE_FAILURE: "postToolUseFailure",
    HookEvent.PRE_COMPACT: "preCompact",
    HookEvent.SUBAGENT_STOP: "subagentStop",
    HookEvent.STOP: "stop",
}
SHELL_EVENT = "beforeShellExecution"

#: Orthographe neutre écrite dans ``--event`` (voir le docstring du module).
_WIRE_NAMES: dict[HookEvent, str] = {
    HookEvent.SESSION_START: "SessionStart",
    HookEvent.PRE_TOOL_USE: "PreToolUse",
    HookEvent.POST_TOOL_USE: "PostToolUse",
    HookEvent.POST_TOOL_USE_FAILURE: "PostToolUseFailure",
    HookEvent.PRE_COMPACT: "PreCompact",
    HookEvent.SUBAGENT_STOP: "SubagentStop",
    HookEvent.STOP: "Stop",
}

#: Événements dont l'entrée prend un matcher d'outil.
_TOOL_EVENTS = frozenset({HookEvent.PRE_TOOL_USE, HookEvent.POST_TOOL_USE, HookEvent.POST_TOOL_USE_FAILURE})

#: Événements de fin dont la relance (``followup_message``) est bornée par ``loop_limit``.
_LOOPING_EVENTS = frozenset({HookEvent.STOP, HookEvent.SUBAGENT_STOP})


def _is_guard(hook: HookSpec) -> bool:
    return hook.event is HookEvent.PRE_TOOL_USE and hook.enforcement is Enforcement.BLOCKING


def _program(launcher: HookLauncher) -> str:
    """Le programme du hook. Un hook de projet Cursor tourne depuis la racine du projet
    (doc : « Project hooks: project root ») : un venv du projet s'écrit relatif à elle."""
    if launcher.project_relative:
        return f"./{launcher.posix}"
    return Emitter.quoted_program(launcher.posix)


def _matcher(hook: HookSpec, families: tuple[str, ...]) -> str:
    tools: list[str] = []
    for family in families:
        for tool in _MATCHER_TABLE.get(family, ()):
            if tool not in tools:
                tools.append(tool)
    return "|".join(tools)


def _entry(hook: HookSpec, launcher: HookLauncher, *, matcher: str = "") -> dict[str, Any]:
    wire = _WIRE_NAMES[hook.event]
    program = _program(launcher)
    command = (
        Emitter.hook_command(HOST_ALIAS, wire, program)
        if hook.decision == DEFAULT_DECISION_BY_EVENT.get(hook.event)
        else Emitter.hook_command_for_decision(HOST_ALIAS, wire, program, hook.decision)
    )
    entry: dict[str, Any] = {"command": command, "timeout": hook.timeout}
    if matcher:
        entry["matcher"] = matcher
    if _is_guard(hook) and launcher.found:
        # « failClosed: true » : plantage, délai dépassé, code autre que 2 ou sortie vide
        # bloquent l'action. Un dépassement vaut alors refus : même plancher que Claude Code.
        entry["failClosed"] = True
        entry["timeout"] = max(hook.timeout, FAIL_CLOSED_TIMEOUT)
    if hook.event in _LOOPING_EVENTS:
        entry["loop_limit"] = LOOP_LIMIT
    return entry


def hook_entries(surface: ProjectSurface, launcher: HookLauncher = BARE) -> dict[str, list[dict[str, Any]]]:
    """Les entrées de ``.cursor/hooks.json`` du kit, par événement Cursor."""
    by_event: dict[str, list[dict[str, Any]]] = {}
    for hook in surface.hooks:
        name = _CURSOR_EVENTS.get(hook.event)
        if name is None:
            continue
        if hook.event is HookEvent.PRE_TOOL_USE and hook.matcher:
            # La famille `execute` passe par `beforeShellExecution` (qui applique `ask`) ;
            # le reste par `preToolUse`.
            if "execute" in hook.matcher:
                by_event.setdefault(SHELL_EVENT, []).append(_entry(hook, launcher))
            rest = tuple(f for f in hook.matcher if f != "execute")
            matcher = _matcher(hook, rest)
            if matcher:
                by_event.setdefault(name, []).append(_entry(hook, launcher, matcher=matcher))
            continue
        matcher = _matcher(hook, hook.matcher) if hook.event in _TOOL_EVENTS else ""
        by_event.setdefault(name, []).append(_entry(hook, launcher, matcher=matcher))
    return by_event


def _owned_entry(entry: Any) -> bool:
    """Une entrée que le kit a écrite, reconnue à ce qu'elle lance (un JSON n'a pas de marqueur)."""
    if not isinstance(entry, dict):
        return False
    command = entry.get("command")
    return isinstance(command, str) and any(marker in command for marker in OWNED_COMMAND_MARKERS)


def _hooks_merge(surface: ProjectSurface, launcher: HookLauncher) -> JsonMerge:
    """Fusion chirurgicale de ``.cursor/hooks.json`` : un hook que le kit n'a pas écrit est gardé tel quel."""
    ours = hook_entries(surface, launcher)

    def merge(data: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(data.get("version"), int) or isinstance(data.get("version"), bool):
            data["version"] = HOOKS_VERSION
        hooks = data.get("hooks")
        hooks = hooks if isinstance(hooks, dict) else {}
        for event, entries in ours.items():
            existing = hooks.get(event)
            kept = [entry for entry in (existing if isinstance(existing, list) else []) if not _owned_entry(entry)]
            hooks[event] = kept + entries
        # Un événement que le kit ne câble plus perd son entrée périmée, rien d'autre.
        for event, existing in list(hooks.items()):
            if event in ours or not isinstance(existing, list):
                continue
            kept = [entry for entry in existing if not _owned_entry(entry)]
            if kept:
                hooks[event] = kept
            else:
                hooks.pop(event)
        data["hooks"] = hooks
        return data

    return JsonMerge(relpath=HOOKS_FILE, merge=merge, label=HOOKS_FILE.as_posix())


def _readonly(agent: AgentSpec) -> bool:
    """``readonly: true`` pour une persona qui n'écrit ni n'exécute (champ documenté)."""
    return ToolVerb.EDIT not in agent.tools and ToolVerb.EXECUTE not in agent.tools


def _agent_model(agent: AgentSpec, cursor_models: dict[str, str]) -> str:
    """Le ``model`` Cursor de *agent*, ou ``""`` : seulement un nom que le projet déclare.

    Même affinité que Claude Code (palier ``opus``/``sonnet``/``haiku``), traduite par
    ``dispatch.cursor_models.<palier>`` ; aucun nom de modèle Cursor n'est deviné.
    Sans correspondance, rien n'est écrit et Cursor applique son défaut documenté
    (``inherit`` : le modèle de l'agent parent).
    """
    tier = _model_for(agent)
    return cursor_models.get(tier, "") if tier != "inherit" else ""


def _model_tiers(project_root: Path) -> dict[str, str]:
    models = {tier: cursor_model_for(project_root, tier) for tier in ("opus", "sonnet", "haiku")}
    return {tier: name for tier, name in models.items() if name}


def _attached_skill_section(skill: SkillSpec) -> str:
    """Le corps d'une compétence possédée par un agent, replié dans le fichier de cet agent.

    Même raisonnement que Claude Code et Copilot : le fichier d'agent accepte un corps
    Markdown libre, lu seulement quand l'agent tourne.
    """
    return f"""
## Compétence attachée : {skill.name}

> {skill.description}

{skill.body}"""


def _agent_file(
    agent: AgentSpec,
    surface: ProjectSurface,
    owned_skills: tuple[SkillSpec, ...],
    cursor_models: dict[str, str],
) -> EmittedFile:
    fields: dict[str, Any] = {
        "name": agent.name,
        "description": agent.description,
        "model": _agent_model(agent, cursor_models),
        "readonly": True if _readonly(agent) else None,
    }
    header = Emitter.frontmatter(fields)
    role = (
        "Point d'entrée : sur une demande directe et bornée, tu fais le travail toi-même ; "
        "sinon tu délègues à la persona adaptée (sous-agent `.cursor/agents/`) et tu vérifies son résultat."
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
    for skill in owned_skills:
        body = f"{body}\n{_attached_skill_section(skill)}"
    return EmittedFile(relpath=AGENTS_DIR / f"{agent.name}.md", content=body)


def _skill_file(skill: SkillSpec) -> EmittedFile:
    header = Emitter.frontmatter(
        {
            "name": skill.slug,
            "description": skill.description,
            # Champ documenté, écrit seulement quand la source le déclare.
            "disable-model-invocation": skill.disable_model_invocation,
        }
    )
    content = f"{header}\n{managed_header('.md')}\n\n{skill.body}"
    return EmittedFile(relpath=SKILLS_DIR / skill.slug / "SKILL.md", content=content)


def _fallback_skill_file(skill: SkillSpec) -> EmittedFile:
    content = f"{managed_header('.md')}\n\n# {skill.slug}\n\n> {skill.description}\n\n{skill.body}"
    return EmittedFile(relpath=FALLBACK_SKILLS_DIR / f"{skill.slug}.md", content=content)


def _entrypoint(
    surface: ProjectSurface, native_skills: list[SkillSpec], fallback_skills: list[SkillSpec]
) -> EmittedFile:
    """La règle d'entrée (``alwaysApply``) : ce qui existe, où, et ce qui n'est pas opposable."""
    lines = [
        "---",
        "description: Grimoire — agents, compétences et gouvernance",
        "alwaysApply: true",
        "---",
        "",
        managed_header(".md"),
        "",
        f"# {surface.project_name} — Cursor",
        "",
        "Projet **Grimoire Kit**. Instructions canoniques : "
        "[`.github/copilot-instructions.md`](.github/copilot-instructions.md).",
        "",
        "## Surfaces de l'hôte",
        "",
        f"- **Sous-agents** : {len(surface.agents)} — `.cursor/agents/`, chacun dans sa fenêtre de contexte.",
        f"- **Compétences** : {len(native_skills)} — `.cursor/skills/`, chargées à la demande.",
        "- **Hooks** : `.cursor/hooks.json` (garde avant outil, trace, relance de fin de tour).",
        "",
    ]
    if surface.agents:
        lines += ["## Personas", "", "| Persona | Rôle | Sous-agent |", "|---|---|---|"]
        for agent in surface.agents:
            marker = " (entrée)" if agent.entry_point else ""
            lines.append(f"| `{agent.name}`{marker} | {agent.description} | `.cursor/agents/{agent.name}.md` |")
        lines.append("")
    lines += [
        "## Règle de source",
        "",
        GROUNDING_RULE,
        "",
        UNCERTAINTIES_BLOCK_RULE.replace("Termine ta réponse", "Toute réponse d'analyse ou de revue se termine"),
        "",
    ]
    if fallback_skills:
        lines += ["## Compétences hors chargement", "", "| Compétence | Quand l'utiliser | Contenu |", "|---|---|---|"]
        for skill in fallback_skills:
            lines.append(
                f"| `{skill.slug}` | {skill.description} | `{FALLBACK_SKILLS_DIR.as_posix()}/{skill.slug}.md` |"
            )
        lines += [
            "",
            "Leur slug n'est pas un nom de compétence Cursor : lire le fichier quand la situation se présente.",
            "",
        ]
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
        for hook in surface.hooks:
            lines.append(f"- **{hook.event.value}** — {hook.rationale}")
        lines += [
            "",
            "La garde avant outil tourne dans `preToolUse` et `beforeShellExecution`, qui",
            "peuvent refuser un appel ; la fin de tour (`stop`) ne refuse pas, elle relance",
            f"au plus {LOOP_LIMIT} fois avec le motif. **Non vérifié** : aucun refus n'a été",
            "observé sur un Cursor réel ; la CI reste l'autorité.",
            "",
        ]
        if surface.governed and any(h.enforcement is Enforcement.BLOCKING for h in surface.hooks):
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
    return EmittedFile(relpath=ENTRYPOINT, content="\n".join(lines))


def _degradations(
    surface: ProjectSurface,
    launcher: HookLauncher,
    unmapped: list[str],
    fallback_skills: list[SkillSpec],
) -> list[Degradation]:
    gaps: list[Degradation] = [*Emitter.launcher_degradations(launcher)]
    guard = any(_is_guard(h) for h in surface.hooks)
    if guard and not launcher.found:
        gaps.append(
            Degradation(
                surface="hook failClosed",
                reason=(
                    "grimoire-hook est introuvable : la garde est émise sans `failClosed`, "
                    "sinon chaque appel d'outil gardé serait refusé."
                ),
                fallback="installer le kit (`uv tool install grimoire-kit` ou pipx) puis relancer `grimoire up`.",
            )
        )
    if launcher.project_relative and launcher.windows:
        gaps.append(
            Degradation(
                surface="hook command windows",
                reason=(
                    "Une entrée de `.cursor/hooks.json` n'a qu'un champ `command`, sans variante par "
                    "système documentée : le chemin du venv du projet est écrit sous sa forme POSIX."
                ),
                fallback="sous Windows, installer le kit dans le PATH (`uv tool install grimoire-kit`) puis `grimoire host sync`.",
            )
        )
    gaps += [
        Degradation(
            surface="hook verification",
            reason=(
                "Le refus de `preToolUse`/`beforeShellExecution` et la relance de `stop` sont écrits "
                "d'après https://cursor.com/docs/hooks ; Cursor n'est pas installé sur le poste de "
                "développement et aucun refus réel n'a été capturé."
            ),
            fallback="garde déclarée `advisory` (non vérifiée), jamais `enforced` ; les gates restent opposables en CI.",
        ),
        Degradation(
            surface="ask",
            reason="`ask` est « accepté par le schéma mais non appliqué » sur `preToolUse` (doc Cursor).",
            fallback=(
                "une demande de confirmation sur `preToolUse` est rendue en refus motivé ; "
                "`beforeShellExecution` garde `ask` (appliqué)."
            ),
        ),
        Degradation(
            surface="permissions",
            reason="Cursor n'expose pas de table de permissions que le kit sache écrire.",
            fallback="règles appliquées par la garde des hooks (mêmes refus, même formulation).",
        ),
        Degradation(
            surface="hook matchers",
            reason=(
                "La garde `preToolUse` nomme `Write`, `Delete`, `Read` et `Grep` ; les commandes passent "
                "par `beforeShellExecution`. `Task`, les outils `MCP:<outil>` et tout outil que la doc ne "
                "nomme pas ne passent pas par la garde."
            ),
            fallback="une écriture ou une commande passe par `Write`/`Delete`/`Shell`, jugés ; la CI reste l'autorité.",
        ),
        Degradation(
            surface="commands",
            reason="Le kit n'émet pas de commande utilisateur pour Cursor.",
            fallback="chaque commande est exposée comme invocation `grimoire host run <slug>` dans la règle d'entrée.",
        ),
        Degradation(
            surface="mcp config",
            reason="La configuration MCP de `.mcp.json` n'est pas traduite pour Cursor.",
            fallback="serveurs nommés dans la règle d'entrée ; le branchement reste côté client.",
        ),
        Degradation(
            surface="dispatch model",
            reason="Aucune entrée de hook ne réécrit le modèle d'un appel `Task` sous Cursor.",
            fallback="le modèle d'un sous-agent vient de son fichier (`model`), sinon `inherit`.",
        ),
        Degradation(
            surface="third-party hooks",
            reason=(
                "Cursor charge aussi les hooks de `.claude/settings.json` (import tiers, actif par défaut) : "
                "un projet qui émet aussi pour Claude Code voit la garde tourner deux fois sous Cursor."
            ),
            fallback=(
                "les deux gardes rendent la même décision (le refus l'emporte) ; couper l'import tiers "
                "dans les réglages de Cursor pour n'en garder qu'une."
            ),
        ),
        Degradation(
            surface="detached session",
            reason="Le kit ne lance aucun worker par `agent -p` (mode headless de Cursor).",
            fallback="les sessions de fond restent des sessions `claude --bg`.",
        ),
    ]
    events = {h.event for h in surface.hooks}
    if HookEvent.STOP in events:
        gaps.append(
            Degradation(
                surface="hook stop",
                reason=(
                    "`stop` ne peut pas refuser la fin d'un tour : un `followup_message` relance l'agent "
                    f"avec le motif, au plus {LOOP_LIMIT} fois (`loop_limit`), et seulement sur un tour terminé."
                ),
                fallback="au-delà de la limite, la clôture passe ; les gates restent rouges et opposables en CI.",
            )
        )
    if HookEvent.SUBAGENT_STOP in events:
        gaps.append(
            Degradation(
                surface="hook subagent_stop",
                reason="`subagentStop` n'a pas de canal de contexte : un `followup_message` relancerait le sous-agent.",
                fallback="l'état des gates est tracé au journal, sans message ni relance.",
            )
        )
    for hook in surface.hooks:
        if hook.event in _CURSOR_EVENTS:
            continue
        reason = (
            "`beforeSubmitPrompt` ne rend que `continue`/`user_message` : aucun contexte ne peut être injecté."
            if hook.event is HookEvent.USER_PROMPT_SUBMIT
            else f"Cursor ne documente pas d'événement pour `{hook.event.value}` que le kit sache câbler."
        )
        gaps.append(
            Degradation(
                surface=f"hook {hook.event.value}",
                reason=reason,
                fallback="règle énoncée dans la règle d'entrée `.cursor/rules/grimoire.mdc`, non opposable ici.",
            )
        )
    if unmapped:
        gaps.append(
            Degradation(
                surface="model affinity",
                reason=(
                    "`model` d'un sous-agent Cursor prend `inherit` ou un identifiant de modèle Cursor, "
                    "sans correspondance documentée avec les paliers du kit. Personas dont le palier n'a "
                    f"pas de nom Cursor déclaré : {', '.join(unmapped)}."
                ),
                fallback=(
                    "`model` n'est écrit que pour un palier déclaré sous `dispatch.cursor_models.<palier>` "
                    "de la politique d'orchestration ; sinon Cursor applique `inherit`."
                ),
            )
        )
    if fallback_skills:
        gaps.append(
            Degradation(
                surface="skills",
                reason=(
                    "Un `name` de compétence Cursor n'admet que minuscules, chiffres et tirets : "
                    f"{', '.join(s.slug for s in fallback_skills)}."
                ),
                fallback=f"copiées sous `{FALLBACK_SKILLS_DIR.as_posix()}/` et listées dans la règle d'entrée.",
            )
        )
    return gaps


class CursorEmitter(Emitter):
    host_id = HostId.CURSOR

    def plan(self, surface: ProjectSurface, project_root: Path) -> EmitPlan:
        launcher = resolve_hook_launcher(project_root)
        cursor_models = _model_tiers(project_root)
        by_slug = {skill.slug: skill for skill in surface.skills}
        attached = {slug for agent in surface.agents for slug in agent.skills}
        files: list[EmittedFile] = [
            _agent_file(
                agent,
                surface,
                tuple(by_slug[slug] for slug in agent.skills if slug in by_slug),
                cursor_models,
            )
            for agent in surface.agents
        ]
        transversal = [skill for skill in surface.skills if skill.slug not in attached]
        native = [skill for skill in transversal if _SKILL_NAME.match(skill.slug)]
        fallback = [skill for skill in transversal if not _SKILL_NAME.match(skill.slug)]
        files.extend(_skill_file(skill) for skill in native)
        files.extend(_fallback_skill_file(skill) for skill in fallback)
        files.append(_entrypoint(surface, native, fallback))
        unmapped = sorted(
            agent.name
            for agent in surface.agents
            if (tier := _model_for(agent)) != "inherit" and tier not in cursor_models
        )
        plan = EmitPlan(
            host_id=self.host_id,
            files=tuple(files),
            merges=(_hooks_merge(surface, launcher),),
            degradations=tuple(_degradations(surface, launcher, unmapped, fallback)),
            owned_globs=(
                ".cursor/agents/*.md",
                ".cursor/skills/*/SKILL.md",
            ),
        )
        return with_rule_layers(plan, project_root)
