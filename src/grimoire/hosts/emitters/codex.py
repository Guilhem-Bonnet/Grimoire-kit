"""OpenAI Codex CLI emitter — hooks, skills, sub-agents and MCP, not just prose.

Codex exposes more than ``AGENTS.md``. What this emitter writes, and the page
of the Codex documentation that says the host reads it (all read on 2026-11-21):

- ``.codex/hooks.json`` — lifecycle **hooks** of a trusted project, event →
  matcher group → ``command`` handlers with a ``timeout`` in seconds
  (https://learn.chatgpt.com/docs/hooks). Merged: an entry the kit did not
  write is kept as is;
- ``.agents/skills/<slug>/SKILL.md`` — **skills**, ``name`` and
  ``description`` required (https://learn.chatgpt.com/docs/build-skills);
- ``.codex/agents/<name>.toml`` — custom **sub-agents**, one TOML file each
  with ``name``, ``description`` and ``developer_instructions``
  (https://learn.chatgpt.com/docs/agent-configuration/subagents);
- ``.codex/config.toml`` — ``[mcp_servers.<name>]`` tables, read for a trusted
  project only (https://learn.chatgpt.com/docs/extend/mcp). Only the servers
  the file does not declare yet are appended; the rest of the file, comments
  included, is never rewritten;
- ``AGENTS.md`` — the kit owns one delimited block, as before.

The host binary is not installed on the development machine and no hook run
was captured: every claim rests on the documentation. Two facts keep the guard
**advisory** (never ``enforced``) in the capability tables:

- a project hook runs only once the user has trusted it through ``/hooks``
  (trust is tied to the hook's hash, so a regenerated hook needs a new review)
  — the kit cannot read that approval;
- no refusal has been observed on a real Codex (contract not verified).

What stays degraded, and why: no project slash commands are documented (custom
prompts exist, but the page gives no project location), ``WebSearch`` fires no
hook, ``PermissionRequest`` is not a kit event, the sub-agent ``model`` field
takes model names the kit has no mapping for, and no fail-closed switch exists
for a hook that crashes or times out.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

from grimoire.bridges.schemas import HostId
from grimoire.core.grounding import GROUNDING_RULE, UNCERTAINTIES_BLOCK_RULE
from grimoire.hosts.decisions import DEFAULT_DECISION_BY_EVENT
from grimoire.hosts.emitters.agent_skills import SHARED_SKILLS_DIR, SHARED_SKILLS_GLOB, shared_skill_file
from grimoire.hosts.emitters.base import (
    Degradation,
    EmitPlan,
    EmittedFile,
    Emitter,
    JsonMerge,
    MergeRefusedError,
    TextMerge,
    context_load_instruction,
    managed_header,
    persona_section,
)
from grimoire.hosts.emitters.copilot import _dispatch_policy_section
from grimoire.hosts.emitters.rules import with_rule_layers
from grimoire.hosts.hook_launcher import BARE, HookLauncher, expand_project_dir, resolve_hook_launcher
from grimoire.hosts.surface import (
    AgentSpec,
    Enforcement,
    HookEvent,
    HookSpec,
    McpServerSpec,
    ProjectSurface,
)

HOST_ALIAS = "codex"
CODEX_DIR = Path(".codex")
HOOKS_FILE = CODEX_DIR / "hooks.json"
CONFIG_FILE = CODEX_DIR / "config.toml"
AGENTS_DIR = CODEX_DIR / "agents"
SKILLS_DIR = SHARED_SKILLS_DIR

#: Ce que l'utilisateur doit faire pour que les hooks du projet s'exécutent.
APPROVAL_HINT = "approbation requise via /hooks"

#: Neutral tool family -> the Codex tool names a matcher can name. The page
#: names ``Bash`` and ``apply_patch`` (``apply_patch`` also matches ``Edit`` and
#: ``Write``). Codex has no read tool of its own (reads go through the shell),
#: ``WebSearch`` fires no hook, and no delegation tool name is documented: those
#: families contribute nothing, and an empty matcher widens the hook to every
#: tool, which is the safe direction.
_MATCHER_TABLE: dict[str, tuple[str, ...]] = {
    "write": ("apply_patch",),
    "execute": ("Bash",),
    "secret": ("Bash",),
}

#: Neutral event -> the key Codex reads in ``hooks`` (page Hooks, « Events »).
_CODEX_EVENTS: dict[HookEvent, str] = {
    HookEvent.SESSION_START: "SessionStart",
    HookEvent.USER_PROMPT_SUBMIT: "UserPromptSubmit",
    HookEvent.PRE_TOOL_USE: "PreToolUse",
    HookEvent.POST_TOOL_USE: "PostToolUse",
    HookEvent.SUBAGENT_START: "SubagentStart",
    HookEvent.SUBAGENT_STOP: "SubagentStop",
    HookEvent.PRE_COMPACT: "PreCompact",
    HookEvent.STOP: "Stop",
}

#: Events whose matcher is ignored or filters on something else than a tool
#: (session source, compaction trigger, agent type): no matcher is written, so
#: every occurrence fires.
_MATCHERLESS = frozenset(
    {
        HookEvent.SESSION_START,
        HookEvent.USER_PROMPT_SUBMIT,
        HookEvent.SUBAGENT_START,
        HookEvent.SUBAGENT_STOP,
        HookEvent.PRE_COMPACT,
        HookEvent.STOP,
    }
)

#: Délai minimal (secondes) de la garde. Codex ne documente pas l'issue d'un
#: hook qui dépasse son délai : un délai court ouvrirait plus souvent la porte.
#: Même plancher que la garde Claude Code (voir ``claude_code.FAIL_CLOSED_TIMEOUT``).
GUARD_TIMEOUT = 30

#: Fichiers que ``AGENTS.md`` cite quand ils existent (aucun import n'est documenté).
_MEMORY_FILES: tuple[str, ...] = (
    ".github/copilot-instructions.md",
    "_grimoire/_memory/shared-context.md",
    "_grimoire/_memory/decisions-log.md",
    "_grimoire/_memory/failure-museum.md",
)

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _toml_string(text: str) -> str:
    """TOML basic string for *text*.

    JSON escaping is a subset of TOML's (see ``gemini._toml_string``), except that
    JSON leaves ``\\x7f`` (DEL) raw while TOML forbids it in a basic string: it is
    escaped as ``\\u007f``. JSON already escapes ``\\x00``-``\\x1f``.
    """
    return json.dumps(text, ensure_ascii=False).replace("\x7f", "\\u007f")


def _toml_key(name: str) -> str:
    return name if _BARE_KEY.fullmatch(name) else _toml_string(name)


def _matcher(hook: HookSpec) -> str:
    if hook.event in _MATCHERLESS:
        return ""
    tools: list[str] = []
    for family in hook.matcher:
        for tool in _MATCHER_TABLE.get(family, ()):
            if tool not in tools:
                tools.append(tool)
    return "|".join(tools)


def _hook_program(launcher: HookLauncher) -> str:
    """Le programme d'un hook Codex (issue #760).

    Codex lance la commande dans le ``cwd`` de la session, qui peut être un
    sous-dossier du projet (il cherche ``.codex`` de la racine jusqu'au ``cwd``).
    Un venv du projet s'écrit donc depuis la racine git, forme que la page des
    hooks donne elle-même en exemple (``$(git rev-parse --show-toplevel)``).
    """
    if launcher.project_relative:
        return f'"$(git rev-parse --show-toplevel)"/{launcher.posix}'
    return Emitter.quoted_program(launcher.posix)


def _is_guard(hook: HookSpec) -> bool:
    return hook.event is HookEvent.PRE_TOOL_USE and hook.enforcement is Enforcement.BLOCKING


def _hook_entry(hook: HookSpec, launcher: HookLauncher = BARE) -> dict[str, Any]:
    """One ``hooks.<Event>[]`` matcher group, in the shape the Codex page documents."""
    entry: dict[str, Any] = {}
    matcher = _matcher(hook)
    if matcher:
        entry["matcher"] = matcher
    wire = _CODEX_EVENTS[hook.event]
    program = _hook_program(launcher)
    command = (
        Emitter.hook_command(HOST_ALIAS, wire, program)
        if hook.decision == DEFAULT_DECISION_BY_EVENT.get(hook.event)
        else Emitter.hook_command_for_decision(HOST_ALIAS, wire, program, hook.decision)
    )
    timeout = max(hook.timeout, GUARD_TIMEOUT) if _is_guard(hook) else hook.timeout
    entry["hooks"] = [{"type": "command", "command": command, "timeout": timeout}]
    return entry


#: A handler command the kit wrote for Codex: the hook program (bare, absolute,
#: quoted, or from the git root, see :func:`_hook_program`), then exactly
#: ``--host codex --event <Event>`` and an optional ``--decision``. Matched on the
#: whole command, never as a substring: a project command that merely mentions
#: ``grimoire-hook`` stays the project's.
_OWNED_COMMAND = re.compile(
    r'^(?:"\$\(git rev-parse --show-toplevel\)"/(?:\S*/)?|"[^"]*[/\\]|\S*[/\\])?'
    r'(?:grimoire-hook(?:\.exe)?"?|grimoire host hook)'
    rf" --host {HOST_ALIAS} --event [A-Za-z]+(?: --decision [A-Za-z0-9_.-]+)?$"
)


def _owned_handler(handler: Any) -> bool:
    """A handler the kit wrote, recognised by its exact command (JSON has no marker)."""
    command = handler.get("command") if isinstance(handler, dict) else None
    return isinstance(command, str) and _OWNED_COMMAND.fullmatch(command.strip()) is not None


def _without_owned(entries: list[Any]) -> list[Any]:
    """*entries* minus the kit's handlers; a group left with none of its own handlers is dropped.

    Filtered handler by handler: a project handler sharing a matcher group with
    the kit's one is kept, in its group.
    """
    kept: list[Any] = []
    for entry in entries:
        handlers = entry.get("hooks") if isinstance(entry, dict) else None
        if not isinstance(handlers, list) or not any(_owned_handler(h) for h in handlers):
            kept.append(entry)
            continue
        remaining = [h for h in handlers if not _owned_handler(h)]
        if remaining:
            kept.append({**entry, "hooks": remaining})
    return kept


def codex_hooks(surface: ProjectSurface) -> tuple[HookSpec, ...]:
    """Les hooks de la surface qu'un événement Codex peut porter."""
    return tuple(hook for hook in surface.hooks if hook.event in _CODEX_EVENTS)


def _hooks_merge(surface: ProjectSurface, launcher: HookLauncher = BARE) -> JsonMerge:
    hooks_by_event: dict[str, list[dict[str, Any]]] = {}
    for hook in codex_hooks(surface):
        hooks_by_event.setdefault(_CODEX_EVENTS[hook.event], []).append(_hook_entry(hook, launcher))

    def merge(data: dict[str, Any]) -> dict[str, Any]:
        hooks = data.get("hooks", {})
        if not isinstance(hooks, dict):
            # Le remplacer effacerait la configuration du projet : fichier préservé, conflit signalé.
            msg = "`hooks` n'est pas un objet"
            raise MergeRefusedError(msg)
        for event, entries in hooks_by_event.items():
            existing = hooks.get(event)
            if existing is not None and not isinstance(existing, list):
                msg = f"`hooks.{event}` n'est pas une liste"
                raise MergeRefusedError(msg)
            hooks[event] = _without_owned(existing or []) + entries
        # Un événement que la surface ne câble plus perd l'entrée du kit, jamais celles du projet.
        for event, existing in list(hooks.items()):
            if event in hooks_by_event or not isinstance(existing, list):
                continue
            kept = _without_owned(existing)
            if kept:
                hooks[event] = kept
            else:
                hooks.pop(event)
        data["hooks"] = hooks
        return data

    return JsonMerge(relpath=HOOKS_FILE, merge=merge, label=HOOKS_FILE.as_posix())


def _mcp_table(server: McpServerSpec, project_root: Path) -> str:
    lines = [f"[mcp_servers.{_toml_key(server.name)}]"]
    if server.url and not server.command:
        lines.append(f"url = {_toml_string(server.url)}")
    else:
        # Aucune variable de racine n'est documentée pour `mcp_servers` : une commande
        # relative au projet est écrite absolue, comme chez Gemini CLI (#760).
        lines.append(f"command = {_toml_string(expand_project_dir(server.command, project_root))}")
        if server.args:
            lines.append(f"args = [{', '.join(_toml_string(arg) for arg in server.args)}]")
    return "\n".join(lines)


def _config_merge(servers: tuple[McpServerSpec, ...], project_root: Path) -> TextMerge:
    """Ajouter à ``.codex/config.toml`` les serveurs MCP qu'il ne déclare pas encore.

    Le fichier est lu (TOML invalide : ``ValueError``, fichier préservé), jamais
    réécrit : les tables manquantes sont ajoutées en fin de fichier, ce qui
    garde chaque octet existant, commentaires compris. Le résultat est relu ; s'il
    n'est pas du TOML valide (``mcp_servers`` en table en ligne), ``ValueError`` :
    rien n'est écrit. Un serveur déjà déclaré est celui du projet ; le kit n'y
    touche pas.
    """

    def merge(text: str | None) -> str:
        current = text or ""
        data = tomllib.loads(current)  # TOMLDecodeError est une ValueError
        declared = data.get("mcp_servers", {})
        if not isinstance(declared, dict):
            msg = "`mcp_servers` n'est pas une table"
            raise MergeRefusedError(msg)
        missing = [server for server in servers if server.name not in declared]
        if not missing:
            return current
        blocks = [
            # Pas de marqueur géré : une fois ajoutée, la table est au projet (le kit ne la relit jamais).
            "# Serveur MCP ajouté par `grimoire host sync` (Grimoire) ; il est désormais le vôtre.\n"
            + _mcp_table(server, project_root)
            for server in missing
        ]
        head = current.rstrip("\n")
        merged = (f"{head}\n\n" if head else "") + "\n\n".join(blocks) + "\n"
        # Relu avant écriture : un ajout qui rendrait le fichier invalide n'est jamais écrit.
        # Cas connu : `mcp_servers = {...}` en table en ligne, fermée à tout `[mcp_servers.<nom>]`
        # ajouté ensuite ; la réécrire toucherait à la configuration du projet.
        try:
            tomllib.loads(merged)
        except tomllib.TOMLDecodeError as exc:
            msg = (
                f"l'ajout de [mcp_servers.<nom>] rendrait le TOML invalide ({exc}) ; "
                "`mcp_servers` en table en ligne ? Déclarez les serveurs du kit à la main"
            )
            raise MergeRefusedError(msg) from exc
        return merged

    return TextMerge(relpath=CONFIG_FILE, merge=merge, label=CONFIG_FILE.as_posix())


def _agent_file(agent: AgentSpec, surface: ProjectSurface) -> EmittedFile:
    role = (
        "Point d'entrée : quand la demande ne désigne pas clairement un rôle, c'est toi qui tranches."
        if agent.entry_point
        else "Tu es dispatché sur une tranche de travail précise ; tu ne clos pas la tâche globale."
    )
    instructions = f"""Tu incarnes la persona Grimoire **{agent.name}** du projet {surface.project_name}.

{persona_section(agent)}

1. {context_load_instruction(agent)}
2. {role}
3. Ne sors pas de ta frontière d'outils : {", ".join(v.value for v in agent.tools)}.
4. {GROUNDING_RULE}
"""
    instructions = (
        f"{instructions}\n{_dispatch_policy_section()}"
        if agent.entry_point
        else f"{instructions}5. {UNCERTAINTIES_BLOCK_RULE}\n"
    )
    content = "\n".join(
        [
            managed_header(".toml"),
            f"name = {_toml_string(agent.name)}",
            f"description = {_toml_string(agent.description)}",
            f"developer_instructions = {_toml_string(instructions)}",
            "",
        ]
    )
    return EmittedFile(relpath=AGENTS_DIR / f"{agent.name}.toml", content=content)


def _entrypoint(surface: ProjectSurface, project_root: Path) -> EmittedFile:
    lines = [
        managed_header(".md"),
        "",
        f"# {surface.project_name} — Codex",
        "",
        "Surface générée par `grimoire host sync --host codex`. Éditer ce bloc est",
        "sans effet durable : la prochaine synchronisation le régénère.",
        "",
    ]
    present = [rel for rel in _MEMORY_FILES if (project_root / rel).is_file()]
    if present:
        lines += ["## Instructions et mémoire", "", *(f"- [`{rel}`]({rel})" for rel in present), ""]
    if surface.agents:
        lines += ["## Personas", ""]
        for agent in surface.agents:
            marker = " (entrée)" if agent.entry_point else ""
            lines.append(f"- `{agent.name}`{marker} — {agent.description}")
        lines.append("")
    lines += [
        "## Surfaces de l'hôte",
        "",
        f"- **Sous-agents** : {len(surface.agents)} — `.codex/agents/`, à désigner par leur nom.",
        f"- **Compétences** : {len(surface.skills)} — `.agents/skills/`, chargées à la demande (`$` ou `/skills`).",
        "- **MCP** : serveurs ajoutés à `.codex/config.toml` (lu seulement pour un projet approuvé).",
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
    if codex_hooks(surface):
        lines += [
            "## Gouvernance",
            "",
            "Les hooks du projet sont dans `.codex/hooks.json`. **Codex ne les exécute",
            f"qu'une fois approuvés** ({APPROVAL_HINT}, à refaire après chaque régénération).",
            "",
        ]
        lines += [f"- **{_CODEX_EVENTS[hook.event]}** — {hook.rationale}" for hook in codex_hooks(surface)]
        lines += [
            "",
            "Tant que l'approbation n'est pas faite, ou si un hook échoue, ces règles ne tiennent",
            "que par discipline et par la CI : rien ne refuse un appel d'outil ni une clôture.",
            "",
        ]
        if surface.governed and any(h.event is HookEvent.STOP for h in surface.hooks):
            lines += [
                "Avant toute conclusion de tâche :",
                "",
                "```bash",
                "grimoire standard gate check --strict",
                "grimoire standard verify .",
                "```",
                "",
            ]
    return EmittedFile(relpath=Path("AGENTS.md"), content="\n".join(lines), block=True)


def _degradations(surface: ProjectSurface) -> list[Degradation]:
    gaps: list[Degradation] = [
        Degradation(
            surface="hook approval",
            reason=(
                "Codex n'exécute un hook de projet qu'une fois approuvé (empreinte du hook) ; "
                "le kit ne peut pas lire cette approbation."
            ),
            fallback=f"{APPROVAL_HINT} après chaque `grimoire host sync` ; la garde reste consultative tant que non prouvée.",
        ),
        Degradation(
            surface="hook enforcement",
            reason=(
                "Refus PreToolUse et relance Stop documentés, jamais observés : Codex n'est pas installé "
                "sur le poste du kit, aucun contrat vérifié."
            ),
            fallback="garde et gate de clôture déclarés consultatifs ; les gates restent opposables en CI.",
        ),
        Degradation(
            surface="hook failure",
            reason=(
                "Codex ne documente pas d'échec fermé : un hook qui ne démarre pas, dépasse son délai "
                "ou sort hors de 0/2 laisse a priori passer l'outil."
            ),
            fallback=(
                f"délai de la garde porté à {GUARD_TIMEOUT} s ; une panne interne de `grimoire-hook` "
                "rend un refus explicite (code 2)."
            ),
        ),
        Degradation(
            surface="ask",
            reason="`ask` n'est pas pris en charge sur PreToolUse : il ferait échouer le hook et passer l'outil.",
            fallback="une décision `ask` est rendue comme refus motivé (échec fermé, voir `codex_wire`).",
        ),
        Degradation(
            surface="web search",
            reason="Les outils hébergés comme WebSearch ne déclenchent aucun hook (page Hooks de Codex).",
            fallback="aucune garde sur la recherche web ; la politique réseau ne s'y applique pas.",
        ),
        Degradation(
            surface="permissions",
            reason="Pas de table de permissions de projet émise ; `PermissionRequest` n'est pas un événement du kit.",
            fallback="règles appliquées par le hook PreToolUse (mêmes refus, même formulation).",
        ),
        Degradation(
            surface="commands",
            reason="Aucun emplacement de commande de projet documenté (les prompts personnalisés n'en ont pas).",
            fallback="chaque commande est exposée dans `AGENTS.md` comme invocation `grimoire host run <slug>`.",
        ),
        Degradation(
            surface="model affinity",
            reason=(
                "Le champ `model` d'un agent Codex prend un nom de modèle ; le kit n'a pas de correspondance "
                "documentée entre son affinité neutre et ces noms."
            ),
            fallback="`model` n'est pas émis ; le modèle de la session s'applique à chaque agent.",
        ),
        Degradation(
            surface="mcp trust",
            reason="`.codex/config.toml` de projet n'est lu que pour un projet approuvé (trusted).",
            fallback="approuver le projet dans Codex, ou déclarer le serveur dans `~/.codex/config.toml`.",
        ),
        Degradation(
            surface="mcp tools unguarded",
            reason=(
                "La garde PreToolUse ne correspond qu'à `Bash|apply_patch` : les appels d'outils MCP "
                "n'y passent pas (aucun nom d'outil MCP pour un matcher n'est documenté par la page Hooks)."
            ),
            fallback=(
                "outils MCP non gardés sur Codex ; n'exposer au projet que des serveurs dont les outils "
                "sont acceptables sans garde."
            ),
        ),
    ]
    for hook in surface.hooks:
        if hook.event in _CODEX_EVENTS:
            continue
        gaps.append(
            Degradation(
                surface=f"hook {hook.event.value}",
                reason=f"Codex ne documente pas d'événement pour `{hook.event.value}`.",
                fallback="règle énoncée dans `AGENTS.md`, non opposable sur cet hôte.",
            )
        )
    return gaps


class CodexEmitter(Emitter):
    host_id = HostId.CODEX

    def plan(self, surface: ProjectSurface, project_root: Path) -> EmitPlan:
        launcher = resolve_hook_launcher(project_root)
        files: list[EmittedFile] = [_agent_file(agent, surface) for agent in surface.agents]
        files.extend(shared_skill_file(skill) for skill in surface.skills)
        files.append(_entrypoint(surface, project_root))
        text_merges = (_config_merge(surface.mcp_servers, project_root),) if surface.mcp_servers else ()
        plan = EmitPlan(
            host_id=self.host_id,
            files=tuple(files),
            merges=(_hooks_merge(surface, launcher),),
            degradations=(*Emitter.launcher_degradations(launcher), *_degradations(surface)),
            owned_globs=(".codex/agents/*.toml", SHARED_SKILLS_GLOB),
            text_merges=text_merges,
        )
        return with_rule_layers(plan, project_root)
