"""Zed : instructions de projet, compétences, serveurs MCP et tâche CLI gouvernée (#1281 L8, #725).

Ce que cet émetteur écrit, et la page de la doc de Zed qui dit que l'éditeur le lit
(https://zed.dev/docs, lue le 2026-11-22 ; Zed n'est pas installé sur le poste du kit) :

- ``.rules`` — **instructions de projet** de l'agent de Zed. Zed lit le *premier* fichier
  trouvé dans cet ordre : ``.rules``, ``.cursorrules``, ``.windsurfrules``, ``.clinerules``,
  ``.github/copilot-instructions.md``, ``AGENT.md``, ``AGENTS.md``, ``CLAUDE.md``,
  ``GEMINI.md`` (ai/instructions). ``.rules`` passe donc devant les autres : le kit n'y
  possède qu'un bloc délimité, et y cite les autres fichiers de la liste présents dans le
  projet, que Zed ne lirait plus de lui-même ;
- ``.agents/skills/<slug>/SKILL.md`` — **compétences** de l'agent de Zed (ai/skills), même
  écrivain que Codex, Gemini et Kimi Code. Zed n'admet que ``[a-z0-9-]``, 64 caractères au
  plus : un nom hors de cette forme est écrit pour les autres hôtes et déclaré ignoré ici ;
- ``.zed/settings.json`` — ``context_servers`` (``command``, ``args``, ``env``) d'un serveur
  local (reference/all-settings, ai/mcp). Fusion qui n'ajoute que le serveur du kit absent.
  Zed accepte des commentaires dans ses réglages (JSONC, languages/json) : un fichier qui
  n'est pas du JSON strict est préservé et signalé, jamais réécrit ;
- ``.zed/tasks.json`` — quand Claude Code est activé, la tâche « Claude Code (gouverné) »
  (``command: claude``, ``cwd: $ZED_WORKTREE_ROOT``, ``use_new_terminal``) lance la CLI dans
  le terminal de Zed, avec tous ses hooks (tasks ; proposition de #725). Les tâches de
  l'utilisateur sont gardées ; celle du kit est reconnue à son libellé et sa commande exacts.

Ce qui reste dégradé : aucun hook de cycle de vie n'est documenté pour l'agent de Zed (hôte
sans garde), ni fichier d'agent (l'agent délègue par ``spawn_agent`` sans définition), ni
commande de projet ; les profils (``agent.profiles``) et les permissions d'outils
(``agent.tool_permissions``) existent, mais leur lecture dans les réglages de projet n'est pas
documentée : rien n'y est écrit. Réglages, serveurs MCP et tâches du projet ne s'appliquent
qu'à un worktree approuvé (worktree-trust).
"""

from __future__ import annotations

import json
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
    TextMerge,
    escapes_root,
    inline_text,
    load_json_unique,
    managed_header,
)
from grimoire.hosts.emitters.copilot_mcp import KIT_SERVERS
from grimoire.hosts.emitters.rules import with_rule_layers
from grimoire.hosts.hook_launcher import expand_project_dir
from grimoire.hosts.surface import McpServerSpec, ProjectSurface

HOST_ALIAS = "zed"
ZED_DIR = Path(".zed")
SETTINGS_FILE = ZED_DIR / "settings.json"
TASKS_FILE = ZED_DIR / "tasks.json"
ENTRYPOINT = Path(".rules")

#: Fichiers d'instructions que Zed lit, dans son ordre ; seul le premier présent compte (ai/instructions).
ZED_INSTRUCTION_FILES: tuple[str, ...] = (
    ".rules",
    ".cursorrules",
    ".windsurfrules",
    ".clinerules",
    ".github/copilot-instructions.md",
    "AGENT.md",
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
)

#: Nom de compétence que Zed admet : minuscules, chiffres, tirets, 64 caractères au plus (ai/skills).
_ZED_SKILL = re.compile(r"[a-z0-9-]{1,64}")

#: La tâche du kit : la CLI Claude Code dans le terminal de Zed (#725).
GOVERNED_TASK_LABEL = "Claude Code (gouverné)"
GOVERNED_TASK: dict[str, Any] = {
    "label": GOVERNED_TASK_LABEL,
    "command": "claude",
    "cwd": "$ZED_WORKTREE_ROOT",
    "use_new_terminal": True,
}


def _entrypoint(surface: ProjectSurface, governed_task: bool) -> EmittedFile:
    lines = [
        managed_header(".md"),
        "",
        f"# {surface.project_name} — Zed",
        "",
        "Surface générée par `grimoire host sync --host zed`. Éditer ce bloc est",
        "sans effet durable : la prochaine synchronisation le régénère.",
        "",
    ]
    # Liste fixe, pas l'état du disque : un autre hôte synchronisé dans la même passe peut créer
    # `CLAUDE.md` ou `AGENTS.md` après ce plan, et le bloc changerait d'une passe à l'autre.
    lines += [
        "## Instructions du projet",
        "",
        "Zed ne lit que le premier fichier d'instructions de sa liste, celui-ci. Ceux du projet qui",
        "suivent valent aussi s'ils existent : les lire avant d'agir.",
        "",
        *(f"- `{rel}`" for rel in ZED_INSTRUCTION_FILES[1:]),
        "",
    ]
    if surface.agents:
        lines += ["## Personas", "", "| Persona | Rôle | Définition |", "|---|---|---|"]
        for agent in surface.agents:
            marker = " (entrée)" if agent.entry_point else ""
            lines.append(f"| `{agent.name}`{marker} | {inline_text(agent.description)} | `{agent.definition_ref}` |")
        lines += [
            "",
            "Activer une persona = lire sa définition en entier et l'appliquer, sans la résumer.",
            "",
        ]
    lines += [
        "## Surfaces de l'hôte",
        "",
        f"- **Compétences** : {len(surface.skills)} — `.agents/skills/`, chargées à la demande (`/` puis le nom).",
        "- **MCP** : `context_servers` de `.zed/settings.json`, lancés dans un worktree approuvé.",
    ]
    if governed_task:
        lines.append(f"- **Mode gouverné** : la tâche « {GOVERNED_TASK_LABEL} » lance Claude Code et ses hooks.")
    lines += [
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
            lines.append(f"| `grimoire host run {command.slug}{hint}` | {inline_text(command.description)} |")
        lines.append("")
    if surface.hooks:
        lines += ["## Gouvernance", ""]
        lines += [f"- **{hook.event.value}** — {hook.rationale}" for hook in surface.hooks]
        lines += [
            "",
            "Dans l'agent de Zed, ces règles ne sont pas opposables : rien n'intercepte un appel",
            "d'outil ni une fin de tour. Elles tiennent par discipline, et par la CI.",
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


def _server_entry(server: McpServerSpec, project_root: Path) -> dict[str, Any]:
    if server.url and not server.command:
        return {"url": server.url}
    # Aucune variable de racine n'est documentée pour `context_servers` : commande écrite absolue (#760).
    entry: dict[str, Any] = {"command": expand_project_dir(server.command, project_root)}
    entry["args"] = list(server.args)
    return entry


def _settings_merge(servers: tuple[McpServerSpec, ...], project_root: Path) -> JsonMerge | None:
    """Ajouter aux ``context_servers`` de ``.zed/settings.json`` les seuls serveurs du kit absents."""
    entries = {s.name: _server_entry(s, project_root) for s in servers if s.name in KIT_SERVERS}
    if not entries:
        return None

    def merge(data: dict[str, Any]) -> dict[str, Any]:
        current = data.get("context_servers", {})
        if not isinstance(current, dict):
            msg = "`context_servers` n'est pas un objet"
            raise MergeRefusedError(msg)
        for name, entry in entries.items():
            # Un serveur déjà déclaré est celui de l'utilisateur : on n'ajoute que ce qui manque.
            current.setdefault(name, entry)
        data["context_servers"] = current
        return data

    return JsonMerge(relpath=SETTINGS_FILE, merge=merge, label=SETTINGS_FILE.as_posix(), unique_keys=True)


def _is_kit_task(entry: Any) -> bool:
    """La tâche du kit : égale à :data:`GOVERNED_TASK`, champ pour champ. Rien d'autre n'est au kit."""
    return bool(entry == GOVERNED_TASK)


def _is_variant(entry: Any) -> bool:
    """Une tâche qui porte le libellé du kit sans être la sienne (arguments, env, autre commande…)."""
    return isinstance(entry, dict) and entry.get("label") == GOVERNED_TASK_LABEL and not _is_kit_task(entry)


def _load_tasks(text: str | None) -> list[Any]:
    current = load_json_unique(text) if text is not None and text.strip() else []
    if not isinstance(current, list):
        msg = "la racine de `.zed/tasks.json` n'est pas une liste"
        raise MergeRefusedError(msg)
    return current


def _render_tasks(text: str | None, current: list[Any], desired: list[Any]) -> str:
    if text is not None and current == desired:
        return text  # déjà en place : rien n'est réécrit, pas même la mise en forme
    return json.dumps(desired, indent=2, ensure_ascii=False) + "\n"


def _tasks_merge() -> TextMerge:
    """Poser la tâche gouvernée dans ``.zed/tasks.json`` (liste JSON), sans toucher aux autres.

    Le fichier se lit en JSON strict : commentaires (JSONC, que Zed accepte), clé répétée, racine
    qui n'est pas une liste, ou tâche de l'utilisateur au libellé du kit lèvent ``ValueError`` :
    le fichier est préservé tel quel et le conflit signalé, jamais une tâche écrasée.
    """

    def merge(text: str | None) -> str:
        current = _load_tasks(text)
        if any(_is_variant(entry) for entry in current):
            msg = f"une tâche « {GOVERNED_TASK_LABEL} » n'est pas celle du kit : renommez-la ou retirez-la"
            raise MergeRefusedError(msg)
        desired = [*(entry for entry in current if not _is_kit_task(entry)), GOVERNED_TASK]
        return _render_tasks(text, current, desired)

    return TextMerge(relpath=TASKS_FILE, merge=merge, label=TASKS_FILE.as_posix())


def _tasks_removal() -> TextMerge:
    """Retirer la tâche du kit, identique à :data:`GOVERNED_TASK`, quand Claude Code n'est plus activé."""

    def merge(text: str | None) -> str:
        current = _load_tasks(text)
        desired = [entry for entry in current if not _is_kit_task(entry)]
        return _render_tasks(text, current, desired)

    return TextMerge(relpath=TASKS_FILE, merge=merge, label=TASKS_FILE.as_posix())


def _claude_enabled(project_root: Path) -> bool:
    """Claude Code est-il déclaré dans ``hosts.enabled``, ou, sans déclaration, présent (``.claude/``) ?

    Pas le repli de la détection (« claude » quand aucun hôte n'est trouvé) : il change dès que
    ``.zed/`` existe, et la tâche apparaîtrait puis disparaîtrait d'une synchronisation à l'autre.
    """
    from grimoire.core.config import GrimoireConfig
    from grimoire.core.exceptions import GrimoireConfigError
    from grimoire.hosts.capabilities import resolve_host

    try:
        declared = GrimoireConfig.from_yaml(project_root / "project-context.yaml").hosts.enabled
    except GrimoireConfigError:
        declared = None
    if declared is not None:
        return any(resolve_host(alias) is HostId.CLAUDE_CODE_CLI for alias in declared)
    return (project_root / ".claude").is_dir()


def _degradations(surface: ProjectSurface, project_root: Path, governed_task: bool) -> list[Degradation]:
    hooks_fallback = (
        f"aucune garde dans l'agent de Zed ; la tâche « {GOVERNED_TASK_LABEL} » lance Claude Code et ses hooks "
        "dans le terminal de Zed"
        if governed_task
        else "aucune garde dans l'agent de Zed ; gouvernance énoncée dans `.rules`, opposable en CI"
    )
    gaps = [
        Degradation(
            surface="hooks",
            reason=(
                "Zed ne documente aucun hook de cycle de vie pour son agent ; le fil ACP « Claude Agent » "
                "n'exécuterait pas les hooks de Claude Code (#725, non vérifié)."
            ),
            fallback=hooks_fallback,
        ),
        Degradation(
            surface="subagents",
            reason="L'agent de Zed délègue par `spawn_agent` sans charger de fichier de définition d'agent.",
            fallback="personas listées dans `.rules`, à lire et appliquer.",
        ),
        Degradation(
            surface="commands",
            reason="Pas de commande de projet documentée.",
            fallback="chaque commande est exposée dans `.rules` comme invocation `grimoire host run <slug>`.",
        ),
        Degradation(
            surface="permissions",
            reason="`agent.tool_permissions` existe, mais sa lecture dans `.zed/settings.json` n'est pas documentée.",
            fallback="aucune règle de permission écrite ; la politique d'outils est énoncée dans `.rules`.",
        ),
        Degradation(
            surface="profiles",
            reason="`agent.profiles` existe, mais sa lecture dans les réglages de projet n'est pas documentée.",
            fallback="aucun profil écrit ; les profils Write, Ask et Minimal de Zed s'appliquent.",
        ),
        Degradation(
            surface="model affinity",
            reason="Aucun fichier d'agent, donc aucun `model` par persona ; `agent.subagent_model` vaut pour tous.",
            fallback="le modèle choisi dans Zed s'applique à chaque persona.",
        ),
        Degradation(
            surface="worktree trust",
            reason="Zed n'applique réglages, serveurs MCP et tâches du projet qu'à un worktree approuvé.",
            fallback="approuver le worktree dans Zed (Restricted Mode), sans quoi seul `.rules` est lu.",
        ),
    ]
    shadowed = [rel for rel in ZED_INSTRUCTION_FILES[1:] if (project_root / rel).is_file()]
    if shadowed:
        gaps.append(
            Degradation(
                surface="instructions",
                reason=f"Zed ne lit que le premier fichier de sa liste : `.rules` passe devant {', '.join(shadowed)}.",
                fallback="ces fichiers sont cités dans `.rules`, à lire par l'agent.",
            )
        )
    others = [s.name for s in surface.mcp_servers if s.name not in KIT_SERVERS]
    if others:
        gaps.append(
            Degradation(
                surface="mcp config",
                reason=f"Serveurs de `.mcp.json` hors kit non traduits (leur `env` n'est pas connu) : {', '.join(others)}.",
                fallback="les déclarer à la main sous `context_servers` dans `.zed/settings.json`.",
            )
        )
    odd_skills = [skill.slug for skill in surface.skills if not _ZED_SKILL.fullmatch(skill.slug)]
    if odd_skills:
        gaps.append(
            Degradation(
                surface="skill names",
                reason=f"Nom hors de `[a-z0-9-]{{1,64}}`, que Zed n'admet pas : {', '.join(map(repr, odd_skills))}.",
                fallback="compétence écrite pour les autres hôtes, ignorée par Zed ; la renommer dans sa source.",
            )
        )
    if not governed_task:
        gaps.append(
            Degradation(
                surface="governed task",
                reason="Claude Code n'est pas activé : la tâche CLI gouvernée de `.zed/tasks.json` n'est pas écrite.",
                fallback="activer `claude` dans `hosts.enabled` pour la recevoir.",
            )
        )
    return gaps


class ZedEmitter(Emitter):
    host_id = HostId.ZED

    def plan(self, surface: ProjectSurface, project_root: Path) -> EmitPlan:
        governed_task = _claude_enabled(project_root)
        files: list[EmittedFile] = []
        skill_files, skill_gaps = shared_skills(surface.skills)
        files.extend(skill_files)
        files.append(_entrypoint(surface, governed_task))
        settings = _settings_merge(surface.mcp_servers, project_root)
        merges = (settings,) if settings is not None else ()
        if governed_task:
            text_merges: tuple[TextMerge, ...] = (_tasks_merge(),)
        elif (project_root / TASKS_FILE).is_file():
            text_merges = (_tasks_removal(),)
        else:
            text_merges = ()
        degradations = [*skill_gaps, *_degradations(surface, project_root, governed_task)]
        if escapes_root(project_root, ZED_DIR):
            # `.zed/` est un lien qui sort du projet : rien n'y est écrit, ni réglages ni tâches.
            merges, text_merges = (), ()
            degradations.append(
                Degradation(
                    surface="symlink",
                    reason="`.zed/` mène hors du projet une fois les liens résolus : écriture refusée.",
                    fallback="remplacer le lien par un dossier du projet, puis relancer `grimoire host sync`.",
                )
            )
        plan = EmitPlan(
            host_id=self.host_id,
            files=tuple(files),
            merges=merges,
            degradations=tuple(degradations),
            owned_globs=(SHARED_SKILLS_GLOB,),
            text_merges=text_merges,
        )
        return with_rule_layers(plan, project_root)
