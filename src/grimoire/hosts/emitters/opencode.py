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

Read from the V1 documentation on 2026-11-22, not captured:

- the same plugin answers the ``session.idle`` event (end of a turn, listed under
  "Session events" of https://opencode.ai/docs/en/plugins/) with the kit's
  closure decision. No hook that refuses the end of a turn or forces another one
  is documented there, nor in the V2 server hooks
  (https://opencode.ai/docs/build/plugins/server), so a refusal is logged
  through ``client.app.log`` and never claimed blocking;
- an agent's ``model`` takes ``provider/model-id``, and a subagent without one
  inherits the model of the primary agent that invoked it
  (https://opencode.ai/docs/en/agents/). It is written only for a tier the
  project names under ``dispatch.models.opencode`` (:mod:`grimoire.hosts.model_table`).

Not emitted because not proven: a ``permission`` table (a ``read`` deny glob
written the way the docs suggest did not stop a read of a dotenv file), and any
session-start or post-tool hook.
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
    persona_section,
)
from grimoire.hosts.emitters.claude_code import tier_for
from grimoire.hosts.emitters.copilot import _dispatch_policy_section
from grimoire.hosts.emitters.rules import with_rule_layers
from grimoire.hosts.hook_launcher import BARE, HookLauncher, expand_project_dir, resolve_hook_launcher
from grimoire.hosts.model_table import TIERS, load_model_table, resolve_model
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


#: Délai d'un plugin sans hook porteur (jamais émis seul : un garde-fou de typage).
_DEFAULT_TIMEOUT = 30


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


def _model_tiers(project_root: Path) -> tuple[dict[str, str], list[str]]:
    """Palier -> ``provider/model-id`` déclaré pour opencode, et les noms écartés faute de fournisseur.

    opencode documente le format ``provider/model-id`` : un nom sans ``/`` serait un modèle
    que l'hôte ne sait pas résoudre, il n'est donc pas écrit (et la dégradation le nomme).
    """
    table = load_model_table(project_root)
    models: dict[str, str] = {}
    rejected: list[str] = []
    for choice in (resolve_model(tier, HOST_ALIAS, table=table) for tier in TIERS):
        if not choice.model:
            continue
        if "/" in choice.model.strip("/"):
            models[choice.tier] = choice.model
        else:
            rejected.append(f"{choice.tier}={choice.model}")
    return models, rejected


def _agent_model(agent: AgentSpec, models: dict[str, str]) -> str:
    tier = tier_for(agent)
    return models.get(tier, "") if tier != "inherit" else ""


def _agent_file(
    agent: AgentSpec,
    surface: ProjectSurface,
    owned_skills: tuple[SkillSpec, ...],
    models: dict[str, str] | None = None,
) -> EmittedFile:
    header = _frontmatter(
        {
            "description": agent.description,
            "mode": "primary" if agent.entry_point else "subagent",
            "model": _agent_model(agent, models or {}),
        },
        _tools_map(agent),
    )
    role = (
        "Point d'entrée : sur une demande directe et bornée tu fais le travail toi-même ; sinon tu délègues "
        "à la persona adaptée avec l'outil `task` (brief : contexte, objectif, contraintes) et tu rends son "
        "résultat ; la vérification reste la tienne."
        if agent.entry_point
        else "Agent routé en interne : tu traites une tranche de travail, tu ne clos pas la tâche globale."
    )
    persona = persona_section(agent)
    body = f"""{header}
{managed_header(".md")}

Tu incarnes la persona Grimoire **{agent.name}** du projet {surface.project_name}.

{persona}

1. {context_load_instruction(agent)}
2. {role}
3. Frontière d'outils : {", ".join(v.value for v in agent.tools)}. N'en sors pas.
4. {GROUNDING_RULE}
"""
    body = f"{body}\n{_dispatch_policy_section()}" if agent.entry_point else f"{body}5. {UNCERTAINTIES_BLOCK_RULE}\n"
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


def _stop_hook(surface: ProjectSurface) -> HookSpec | None:
    return next((hook for hook in surface.hooks if hook.event is HookEvent.STOP), None)


_PLUGIN_TEMPLATE = """// __MARKER__ — régénéré par `grimoire host sync --host opencode`; éditez la source, pas ce fichier.
// Garde PreToolUse : chaque appel d'outil concerné passe par `grimoire-hook`, et un refus
// devient une exception dans `tool.execute.before` — opencode n'exécute alors pas l'outil.
import { spawnSync } from "node:child_process"
import { readFileSync } from "node:fs"
import { join } from "node:path"

const GUARDED = __GUARDED__
const SECRET_PATTERNS = __SECRETS__.map((source) => new RegExp(source, "i"))
const TIMEOUT_MS = __TIMEOUT__
// Décision de clôture sur `session.idle` (fin de tour) : rendue et journalisée, jamais opposée —
// opencode ne documente aucun moyen de refuser la fin d'un tour depuis un plugin.
const STOP_ENABLED = __STOP__
const STOP_TIMEOUT_MS = __STOP_TIMEOUT__
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

async function closureVerdict(directory, client, event) {
  const payload = JSON.stringify({
    hook_event_name: "Stop",
    cwd: directory,
    session_id: String(event.properties?.sessionID ?? ""),
    stop_hook_active: false,
  })
  const run = spawnSync(hookProgram(directory), ["--host", "opencode", "--event", "Stop"], {
    input: payload,
    encoding: "utf8",
    timeout: STOP_TIMEOUT_MS,
  })
  if (run.error || run.status !== 0) return
  let verdict = {}
  try {
    verdict = JSON.parse(run.stdout || "{}")
  } catch {
    return
  }
  const message = verdict.systemMessage || verdict.reason
  if (!message) return
  try {
    await client.app.log({ body: { service: "grimoire", level: "warn", message: `[grimoire] clôture : ${message}` } })
  } catch {
    console.error(`[grimoire] clôture : ${message}`)
  }
}

export const GrimoireGuard = async ({ directory, client }) => ({
  event: async ({ event }) => {
    if (STOP_ENABLED && event?.type === "session.idle") await closureVerdict(directory, client, event)
  },
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


def _guard_plugin(hook: HookSpec | None, launcher: HookLauncher = BARE, stop: HookSpec | None = None) -> EmittedFile:
    """Le plugin : garde ``tool.execute.before`` si *hook*, décision de clôture sur ``session.idle`` si *stop*."""
    tools: list[str] = []
    if hook is not None:
        for family in hook.matcher or tuple(_GUARDED):
            for name in _GUARDED.get(family, ()):
                if name not in tools:
                    tools.append(name)
    carrier = hook if hook is not None else stop
    timeout = carrier.timeout if carrier is not None else _DEFAULT_TIMEOUT
    content = (
        _PLUGIN_TEMPLATE.replace("__MARKER__", MANAGED_MARKER)
        .replace("__GUARDED__", json.dumps(tools))
        .replace("__SECRETS__", json.dumps(list(secret_patterns()), ensure_ascii=False))
        .replace("__TIMEOUT__", str(timeout * 1000))
        .replace("__STOP_TIMEOUT__", str((stop.timeout if stop is not None else timeout) * 1000))
        .replace("__STOP__", "true" if stop is not None else "false")
        .replace("__PROFILES__", json.dumps(list(PROFILE_LADDER)))
        .replace("__HOOK__", json.dumps({"posix": launcher.posix, "windows": launcher.windows}))
    )
    return EmittedFile(relpath=PLUGIN_RELPATH, content=content)


def _instructions_file(
    surface: ProjectSurface, guarded: HookSpec | None, gaps: list[Degradation], stop: HookSpec | None = None
) -> EmittedFile:
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
    if guarded is not None or stop is not None:
        lines += ["## Gouvernance", ""]
        if guarded is not None:
            lines.append(f"- **PreToolUse** — {guarded.rationale}")
        if stop is not None:
            lines.append(f"- **Stop** (consultatif, `session.idle`) — {stop.rationale}")
        lines.append("")
        if guarded is not None:
            lines += ["Le plugin `.opencode/plugin/grimoire-guard.js` refuse l'appel d'outil avant son exécution.", ""]
        if stop is not None:
            lines += [
                "À chaque fin de tour, le même plugin rend la décision de clôture et la journalise ; "
                "opencode ne permet pas de refuser la fin d'un tour, les gates restent opposables en CI.",
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


def _model_degradations(unmapped: list[str], rejected: list[str]) -> list[Degradation]:
    if not unmapped and not rejected:
        return []
    detail = f" Personas sans nom déclaré pour leur palier : {', '.join(unmapped)}." if unmapped else ""
    if rejected:
        detail += f" Noms écartés, sans fournisseur (`provider/model-id` attendu) : {', '.join(rejected)}."
    return [
        Degradation(
            surface="model affinity",
            reason=f"Le champ `model` d'un agent opencode prend `provider/model-id`, sans correspondance documentée avec les paliers du kit.{detail}",
            fallback=(
                "`model` n'est écrit que pour un palier déclaré sous `dispatch.models.opencode.<palier>` ; "
                "sinon un sous-agent hérite du modèle de l'agent principal qui l'invoque."
            ),
        )
    ]


class OpenCodeEmitter(Emitter):
    host_id = HostId.OPENCODE

    def plan(self, surface: ProjectSurface, project_root: Path) -> EmitPlan:
        by_slug = {skill.slug: skill for skill in surface.skills}
        attached = {slug for agent in surface.agents for slug in agent.skills}
        models, rejected = _model_tiers(project_root)
        files: list[EmittedFile] = [
            _agent_file(agent, surface, tuple(by_slug[s] for s in agent.skills if s in by_slug), models)
            for agent in surface.agents
        ]
        files.extend(_skill_file(skill) for skill in surface.skills if skill.slug not in attached)
        files.extend(_command_file(c) for c in surface.commands if c.source != "workflow")
        guarded = _guard_hook(surface)
        stop = _stop_hook(surface)
        plugged = guarded is not None or stop is not None
        launcher = resolve_hook_launcher(project_root) if plugged else BARE
        if plugged:
            files.append(_guard_plugin(guarded, launcher, stop))

        unmapped = sorted(
            agent.name for agent in surface.agents if (tier := tier_for(agent)) != "inherit" and tier not in models
        )
        degradations = [
            *Emitter.launcher_degradations(launcher),
            *_model_degradations(unmapped, rejected),
            Degradation(
                surface="permissions",
                reason="Une table `permission` n'a pas été prouvée (un refus de lecture déclaré en glob n'a pas arrêté la lecture).",
                fallback="les refus passent par le plugin de garde (exécution, écriture, lecture de secrets).",
            ),
        ]
        for hook in surface.hooks:
            if hook is guarded or hook is stop:
                continue
            degradations.append(
                Degradation(
                    surface=f"hook {hook.event.value}",
                    reason="Le plugin opencode ne câble que `tool.execute.before` et `session.idle`.",
                    fallback="règle énoncée dans les instructions, non opposable.",
                )
            )
        if stop is not None:
            degradations.append(
                Degradation(
                    surface="hook stop verification",
                    reason=(
                        "`session.idle` signale la fin d'un tour, mais opencode ne documente aucun moyen "
                        "de la refuser ni de relancer l'agent depuis un plugin ; rien n'a été capturé."
                    ),
                    fallback="la décision de clôture est journalisée (`client.app.log`), sans blocage ; les gates restent opposables en CI.",
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
        files.append(_instructions_file(surface, guarded, degradations, stop))
        return with_rule_layers(
            EmitPlan(
                host_id=self.host_id,
                files=tuple(files),
                merges=(_config_merge(surface, project_root),),
                degradations=tuple(degradations),
            ),
            project_root,
        )
