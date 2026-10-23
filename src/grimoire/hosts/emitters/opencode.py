"""opencode emitter — agents, skills, commands, MCP and a refusing plugin.

Every surface below was loaded by opencode 1.17.11 in a throwaway project
(``opencode debug config|agent|skill`` and a real run against a local fake
provider; see ``tests/fixtures/hosts/opencode/`` and ``docs/hosts.md``):

- ``.opencode/agent/*.md`` (sub-agents), ``.opencode/skill/<slug>/SKILL.md``,
  ``.opencode/command/*.md`` (slash commands);
- ``opencode.json``: ``instructions`` (a file listed there reaches the system
  prompt) and ``mcp`` (``type: local``, ``command`` as an array);
- ``.opencode/plugin/*.js``: a plugin whose ``tool.execute.before`` hook throws
  to refuse a tool call. The refused tool did not run and the agent got the
  message as a failed tool state — this is what makes the host *fenced* for
  ``pretooluse_deny`` and what the guard plugin below relies on.

Not emitted because not proven: a ``permission`` table (a ``read`` deny glob
written the way the docs suggest did not stop a read of a dotenv file), any
session/stop/post-tool hook, and a per-agent model.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from grimoire.bridges.schemas import HostId
from grimoire.core.grounding import GROUNDING_RULE, UNCERTAINTIES_BLOCK_RULE
from grimoire.core.standard_generation import PROFILE_LADDER
from grimoire.hosts.emitters.base import (
    MANAGED_MARKER,
    Degradation,
    EmitPlan,
    EmittedFile,
    Emitter,
    JsonMerge,
    context_load_instruction,
    managed_header,
)
from grimoire.hosts.emitters.copilot import _dispatch_policy_section
from grimoire.hosts.emitters.rules import with_rule_layers
from grimoire.hosts.hook_launcher import BARE, HookLauncher, expand_project_dir, resolve_hook_launcher
from grimoire.hosts.secrets import secret_patterns
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

HOST_ALIAS = "opencode"
OC_DIR = Path(".opencode")
INSTRUCTIONS_REF = ".opencode/grimoire.md"
PLUGIN_RELPATH = OC_DIR / "plugin" / "grimoire-guard.js"

_TOOL_TABLE: dict[ToolVerb, tuple[str, ...]] = {
    ToolVerb.READ: ("read", "glob"),
    ToolVerb.SEARCH: ("grep", "glob"),
    ToolVerb.EDIT: ("edit", "write"),
    ToolVerb.EXECUTE: ("bash",),
    ToolVerb.WEB: ("webfetch",),
}

#: Tools the ``tools`` map of an agent file toggles, in a fixed order.
_ALL_TOOLS: tuple[str, ...] = ("bash", "edit", "glob", "grep", "read", "webfetch", "write")

#: Neutral hook family -> opencode tool names whose calls go through the decision.
#: ``secret`` is the read tool, filtered in the plugin by the shared secret regexes
#: so that an ordinary read does not pay a process.
_GUARDED: dict[str, tuple[str, ...]] = {
    "execute": ("bash",),
    "write": ("edit", "write", "patch", "multiedit"),
    "secret": ("read",),
}


def _tools_map(agent: AgentSpec) -> dict[str, bool]:
    allowed: list[str] = []
    for verb in agent.tools:
        allowed.extend(_TOOL_TABLE.get(verb, ()))
    return {name: name in allowed for name in _ALL_TOOLS}


def _frontmatter(fields: dict[str, Any], tools: dict[str, bool] | None = None) -> str:
    lines = [Emitter.frontmatter(fields).rsplit("---", 1)[0].rstrip("\n")]
    if tools:
        lines.append("tools:")
        lines.extend(f"  {name}: {'true' if on else 'false'}" for name, on in tools.items())
    lines.append("---")
    return "\n".join(lines)


def _skill_section(skill: SkillSpec) -> str:
    return f"\n## Compétence attachée : {skill.name}\n\n> {skill.description}\n\n{skill.body}"


def _agent_file(agent: AgentSpec, surface: ProjectSurface, owned_skills: tuple[SkillSpec, ...]) -> EmittedFile:
    header = _frontmatter(
        {"description": agent.description, "mode": "primary" if agent.entry_point else "subagent"},
        _tools_map(agent),
    )
    role = (
        "Point d'entrée : sur une demande directe et bornée tu fais le travail toi-même ; sinon tu délègues "
        "à la persona adaptée avec l'outil `task` (brief : contexte, objectif, contraintes) et tu rends son "
        "résultat ; la vérification reste la tienne."
        if agent.entry_point
        else "Agent routé en interne : tu traites une tranche de travail, tu ne clos pas la tâche globale."
    )
    body = f"""{header}
{managed_header(".md")}

Tu incarnes la persona Grimoire **{agent.name}** du projet {surface.project_name}.

1. Lis `{agent.definition_ref}` en entier — persona, règles, protocole d'activation.
2. {context_load_instruction(agent)}
3. {role}
4. Frontière d'outils : {", ".join(v.value for v in agent.tools)}. N'en sors pas.
5. {GROUNDING_RULE}
"""
    body = f"{body}\n{_dispatch_policy_section()}" if agent.entry_point else f"{body}6. {UNCERTAINTIES_BLOCK_RULE}\n"
    for skill in owned_skills:
        body = f"{body}{_skill_section(skill)}"
    return EmittedFile(relpath=OC_DIR / "agent" / f"{agent.name}.md", content=body)


def _skill_file(skill: SkillSpec) -> EmittedFile:
    header = Emitter.frontmatter({"name": skill.slug, "description": skill.description})
    return EmittedFile(
        relpath=OC_DIR / "skill" / skill.slug / "SKILL.md",
        content=f"{header}\n{managed_header('.md')}\n\n{skill.body}",
    )


def _command_file(command: CommandSpec) -> EmittedFile:
    header = Emitter.frontmatter({"description": command.description})
    body = command.body
    if command.argument_hint and "$ARGUMENTS" not in body:
        body = body.rstrip() + "\n\nArgument fourni : $ARGUMENTS\n"
    return EmittedFile(
        relpath=OC_DIR / "command" / f"{command.slug}.md",
        content=f"{header}\n{managed_header('.md')}\n\n{body}",
    )


def _guard_hook(surface: ProjectSurface) -> HookSpec | None:
    for hook in surface.hooks:
        if hook.event is HookEvent.PRE_TOOL_USE and hook.enforcement is Enforcement.BLOCKING:
            return hook
    return None


_PLUGIN_TEMPLATE = """// __MARKER__ — régénéré par `grimoire host sync --host opencode`; éditez la source, pas ce fichier.
// Garde PreToolUse : chaque appel d'outil concerné passe par `grimoire-hook`, et un refus
// devient une exception dans `tool.execute.before` — opencode n'exécute alors pas l'outil.
import { spawnSync } from "node:child_process"
import { readFileSync } from "node:fs"
import { join } from "node:path"

const GUARDED = __GUARDED__
const SECRET_PATTERNS = __SECRETS__.map((source) => new RegExp(source, "i"))
const TIMEOUT_MS = __TIMEOUT__
const BLOCKING_PROFILES = ["governed", "production"]
const KNOWN_PROFILES = __PROFILES__
// Programme de garde résolu à l'émission (issue #760) : nom nu s'il est dans le PATH de l'hôte,
// sinon un chemin vers le venv — relatif à la racine du projet quand `windows` est renseigné.
const HOOK = __HOOK__
let warned = false

function hookProgram(directory) {
  if (!HOOK.windows) return HOOK.posix
  return join(directory, process.platform === "win32" ? HOOK.windows : HOOK.posix)
}

// Fail closed in a blocking profile (read from the standard profile manifest, like the Python gates),
// warn once otherwise. An unreadable manifest is not a blocking profile.
function unavailable(directory, why) {
  let profile = ""
  try {
    const manifest = readFileSync(join(directory, "_grimoire", "standard", "standard-profile.yaml"), "utf8")
    profile = (manifest.match(/^profile:\\s*["']?([A-Za-z_-]+)/m) ?? [])[1] ?? ""
  } catch {}
  // Plancher committé (W2-07) : il s'applique même sous un profil déclaré plus léger. Un fichier présent
  // mais illisible, ou à valeur inconnue, vaut « governed » (fail-closed, comme profile_floor_id).
  let floor = ""
  try {
    const raw = readFileSync(join(directory, "_grimoire", "standard", "profile-floor.yaml"), "utf8")
    floor = (raw.match(/^floor:\\s*["']?([A-Za-z_-]+)/m) ?? [])[1] ?? ""
    if (!KNOWN_PROFILES.includes(floor)) floor = "governed"
  } catch (error) {
    if (error?.code !== "ENOENT") floor = "governed"
  }
  if (BLOCKING_PROFILES.includes(profile) || BLOCKING_PROFILES.includes(floor)) {
    throw new Error(`grimoire-hook introuvable, garde indisponible ; installe le kit ou désactive le profil gouverné (${why})`)
  }
  if (!warned) {
    warned = true
    console.error(`[grimoire] garde PreToolUse inactive : ${why}`)
  }
}

function touchesSecret(args) {
  const text = JSON.stringify(args ?? {})
  return SECRET_PATTERNS.some((pattern) => pattern.test(text))
}

export const GrimoireGuard = async ({ directory }) => ({
  "tool.execute.before": async (input, output) => {
    const tool = String(input.tool ?? "").toLowerCase()
    if (!GUARDED.includes(tool)) return
    if (tool === "read" && !touchesSecret(output.args)) return
    const payload = JSON.stringify({
      hook_event_name: "PreToolUse",
      tool_name: tool,
      tool_input: output.args ?? {},
      cwd: directory,
      session_id: String(input.sessionID ?? ""),
      tool_use_id: String(input.callID ?? ""),
    })
    const run = spawnSync(hookProgram(directory), ["--host", "opencode", "--event", "PreToolUse"], {
      input: payload,
      encoding: "utf8",
      timeout: TIMEOUT_MS,
    })
    if (run.error || run.status !== 0) {
      unavailable(directory, run.error ? run.error.message : `exit ${run.status}`)
      return
    }
    let verdict = {}
    try {
      verdict = JSON.parse(run.stdout || "{}")
    } catch {
      unavailable(directory, "verdict illisible")
      return
    }
    const specific = verdict.hookSpecificOutput ?? {}
    const decision = specific.permissionDecision
    if (decision === "deny" || decision === "ask") {
      // opencode has no interactive "ask" from a plugin: an ask is refused, with the reason.
      throw new Error(specific.permissionDecisionReason || "refusé par la politique Grimoire")
    }
  },
})
"""


def _guard_plugin(hook: HookSpec, launcher: HookLauncher = BARE) -> EmittedFile:
    tools: list[str] = []
    for family in hook.matcher or tuple(_GUARDED):
        for name in _GUARDED.get(family, ()):
            if name not in tools:
                tools.append(name)
    content = (
        _PLUGIN_TEMPLATE.replace("__MARKER__", MANAGED_MARKER)
        .replace("__GUARDED__", json.dumps(tools))
        .replace("__SECRETS__", json.dumps(list(secret_patterns()), ensure_ascii=False))
        .replace("__TIMEOUT__", str(hook.timeout * 1000))
        .replace("__PROFILES__", json.dumps(list(PROFILE_LADDER)))
        .replace("__HOOK__", json.dumps({"posix": launcher.posix, "windows": launcher.windows}))
    )
    return EmittedFile(relpath=PLUGIN_RELPATH, content=content)


def _instructions_file(surface: ProjectSurface, guarded: HookSpec | None, gaps: list[Degradation]) -> EmittedFile:
    lines = [
        managed_header(".md"),
        "",
        f"# {surface.project_name} — opencode",
        "",
        "Projet **Grimoire Kit**. Instructions canoniques : `.github/copilot-instructions.md`.",
        "",
        f"- Personas : {len(surface.agents)} dans `.opencode/agent/` (entrée en `mode: primary`, les autres en sous-agents).",
        f"- Skills : {len(surface.skills)} dans `.opencode/skill/`.",
        f"- Commandes : {len([c for c in surface.commands if c.source != 'workflow'])} dans `.opencode/command/`.",
        "",
        "## Règle de source",
        "",
        GROUNDING_RULE,
        "",
    ]
    if guarded is not None:
        lines += [
            "## Gouvernance",
            "",
            f"- **PreToolUse** — {guarded.rationale}",
            "",
            "Le plugin `.opencode/plugin/grimoire-guard.js` refuse l'appel d'outil avant son exécution.",
            "",
        ]
    if gaps:
        lines += ["## Dégradations sur cet hôte", ""]
        lines += [f"- **{g.surface}** — {g.reason} Repli : {g.fallback}" for g in gaps]
        lines.append("")
    return EmittedFile(relpath=Path(INSTRUCTIONS_REF), content="\n".join(lines))


def _config_merge(surface: ProjectSurface, project_root: Path) -> JsonMerge:
    servers = surface.mcp_servers

    def merge(data: dict[str, Any]) -> dict[str, Any]:
        data.setdefault("$schema", "https://opencode.ai/config.json")
        instructions = data.get("instructions")
        current = list(instructions) if isinstance(instructions, list) else []
        if INSTRUCTIONS_REF not in current:
            current.append(INSTRUCTIONS_REF)
        data["instructions"] = current
        if servers:
            mcp = data.get("mcp")
            mcp = mcp if isinstance(mcp, dict) else {}
            for server in servers:
                # A server the project already configured is its own; only add what is missing.
                # opencode ne documente ni ``${CLAUDE_PROJECT_DIR:-.}`` ni la résolution
                # d'un chemin relatif : la racine du projet est écrite en absolu (#760).
                entry: dict[str, Any] = (
                    {"type": "remote", "url": server.url, "enabled": True}
                    if server.url and not server.command
                    else {
                        "type": "local",
                        "command": [expand_project_dir(server.command, project_root), *server.args],
                        "enabled": True,
                    }
                )
                mcp.setdefault(server.name, entry)
            data["mcp"] = mcp
        return data

    return JsonMerge(relpath=Path("opencode.json"), merge=merge, label="opencode.json")


class OpenCodeEmitter(Emitter):
    host_id = HostId.OPENCODE

    def plan(self, surface: ProjectSurface, project_root: Path) -> EmitPlan:
        by_slug = {skill.slug: skill for skill in surface.skills}
        attached = {slug for agent in surface.agents for slug in agent.skills}
        files: list[EmittedFile] = [
            _agent_file(agent, surface, tuple(by_slug[s] for s in agent.skills if s in by_slug))
            for agent in surface.agents
        ]
        files.extend(_skill_file(skill) for skill in surface.skills if skill.slug not in attached)
        files.extend(_command_file(c) for c in surface.commands if c.source != "workflow")
        guarded = _guard_hook(surface)
        launcher = resolve_hook_launcher(project_root) if guarded is not None else BARE
        if guarded is not None:
            files.append(_guard_plugin(guarded, launcher))

        degradations = [
            *Emitter.launcher_degradations(launcher),
            Degradation(
                surface="model affinity",
                reason="Aucune sélection de modèle par palier n'a été sondée sur cet hôte.",
                fallback="`model` n'est pas émis ; le modèle de la session s'applique à chaque agent.",
            ),
            Degradation(
                surface="permissions",
                reason="Une table `permission` n'a pas été prouvée (un refus de lecture déclaré en glob n'a pas arrêté la lecture).",
                fallback="les refus passent par le plugin de garde (exécution, écriture, lecture de secrets).",
            ),
        ]
        for hook in surface.hooks:
            if hook is guarded:
                continue
            degradations.append(
                Degradation(
                    surface=f"hook {hook.event.value}",
                    reason="Seul `tool.execute.before` a été sondé sur cet hôte.",
                    fallback="règle énoncée dans les instructions, non opposable.",
                )
            )
        if guarded is not None:
            degradations.append(
                Degradation(
                    surface="ask",
                    reason="Un plugin opencode ne peut pas poser de question à l'utilisateur.",
                    fallback="une décision `ask` est refusée avec son motif (échec fermé).",
                )
            )
        files.append(_instructions_file(surface, guarded, degradations))
        return with_rule_layers(
            EmitPlan(
                host_id=self.host_id,
                files=tuple(files),
                merges=(_config_merge(surface, project_root),),
                degradations=tuple(degradations),
            ),
            project_root,
        )
