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
from grimoire.core.grounding import GROUNDING_RULE, UNCERTAINTIES_BLOCK_RULE
from grimoire.hosts.emitters.base import (
    OWNED_COMMAND_MARKERS,
    Degradation,
    EmitPlan,
    EmittedFile,
    Emitter,
    context_load_instruction,
    managed_header,
    map_verbs,
    parallel_brainstorm_section,
    persona_section,
)
from grimoire.hosts.emitters.claude_code import DISPATCH_MODEL_HOOK, FAIL_CLOSED_TIMEOUT, tier_for
from grimoire.hosts.emitters.copilot_mcp import KIT_SERVERS, vscode_mcp_merge
from grimoire.hosts.emitters.rules import with_rule_layers
from grimoire.hosts.hook_launcher import BARE, HookLauncher, resolve_hook_launcher
from grimoire.hosts.model_table import TIERS, load_model_table, resolve_model
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
    ToolVerb.WEB: ("web",),  # le tool set VS Code s'appelle `web` (`web/fetch`) ; `fetch` seul est ignoré en silence
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


def _dispatch_policy_section() -> str:
    """Ce que l'agent d'entrée exige des personas qu'il route.

    Même politique que `.github/hooks/README.md`, portée dans le seul fichier
    que l'agent d'entrée charge vraiment. Aucun nom de modèle : cet hôte ne
    documente pas de sélection par palier (dégradation « model affinity »).
    """
    return f"""## Politique de dispatch

Quand tu routes une tranche de travail vers une autre persona, sa classe de
vérifiabilité dit la relecture que son résultat mérite avant d'être cru :

- **V0** — {Verifiability.V0.explanation}
- **V1** — {Verifiability.V1.explanation}
- **V2** — {Verifiability.V2.explanation}

Exige de chaque persona routée, en fin de réponse, un bloc
```grimoire-uncertainties``` portant une liste JSON d'objets
`{{"where": ..., "what": ..., "why": ...}}` — un par point qu'elle n'a pas pu
vérifier. Une persona qui clôt sans ce bloc n'a pas rendu un résultat
vérifiable, elle a rendu une opinion. Un chiffre ou un verdict qu'elle ne
relie à aucune commande exécutée ni à aucun fichier:ligne se relit comme
non vérifié, quel que soit son ton.

{parallel_brainstorm_section("résultat relu selon la classe ci-dessus")}"""


def _agent_model(agent: AgentSpec, copilot_models: dict[str, str]) -> str:
    """Le ``model`` Copilot de *agent*, ou ``""`` (rien n'est émis).

    Même affinité que Claude Code (:func:`tier_for`, reasoning x cost -> palier
    ``strong``/``mid``/``cheap``), traduite par la table palier x hôte x fournisseur
    (``dispatch.models.copilot.<palier>``, alias historique ``dispatch.copilot_models``) :
    le nom est celui que le projet déclare,
    jamais un nom que le kit aurait deviné. Palier ``inherit`` (aucun signal
    fort) ou palier sans correspondance : pas de ``model``, le modèle du
    sélecteur s'applique.

    Doc « Custom agent file reference » (code.visualstudio.com/docs/agents/reference/
    custom-agent-file-reference) : ``model`` accepte un modèle ou une liste ordonnée,
    harness Local et Copilot ; ``model-policy`` vaut ``preferred`` par défaut. Il
    n'est pas émis : ``preferred`` est déjà le défaut, ``required`` enlèverait à
    l'utilisateur le choix d'un autre modèle, et la référence dit que le harness
    Copilot ne le transmet pas à son SDK.
    """
    tier = tier_for(agent)
    return copilot_models.get(tier, "") if tier != "inherit" else ""


def _model_tiers(project_root: Path) -> dict[str, str]:
    """Les paliers de la politique que le projet traduit en nom de modèle Copilot."""
    table = load_model_table(project_root)
    choices = (resolve_model(tier, "copilot", table=table) for tier in TIERS)
    return {choice.tier: choice.model for choice in choices if choice.model}


def _agent_file(
    agent: AgentSpec,
    surface: ProjectSurface,
    owned_skills: tuple[SkillSpec, ...] = (),
    copilot_models: dict[str, str] | None = None,
) -> EmittedFile:
    # `model` n'est émis que si l'affinité de la persona donne un palier ET que
    # le projet déclare le nom Copilot de ce palier (voir `_agent_model`). Aucune
    # valeur de sélection automatique équivalente à `inherit` n'est documentée :
    # sans correspondance, la lacune reste déclarée par la dégradation
    # « model affinity » du plan plutôt que comblée par un nom inventé.
    fields: dict[str, Any] = {
        "description": agent.description,
        "tools": list(map_verbs(agent.tools, _TOOL_TABLE)),
        "model": _agent_model(agent, copilot_models or {}),
        "user-invocable": agent.entry_point,
    }
    routed = [other for other in surface.agents if not other.entry_point and other.name != agent.name]
    boundary = ", ".join(v.value for v in agent.tools)
    if agent.entry_point and routed:
        # Issue #622, deux retours utilisateurs (« pas assez de droits »,
        # « capable de rien ») : un point d'entrée déclaré `read, search` qui ne
        # fait que router dépend d'un VS Code qui sait lancer des sous-agents et
        # d'un modèle qui appelle l'outil. Décision (2026-09-25) : il reçoit
        # l'union des outils des personas qu'il route, plus `agent` — sur une
        # demande directe et bornée il agit lui-même, sinon il délègue
        # (#656). Sur VS Code (harness Local), déléguer exige l'outil `agent`
        # ET une liste `agents:` : la référence du fichier d'agent classe `agents`
        # « Local only » ; le harness Copilot délègue par son outil natif `task`
        # sans cette liste. Le fichier est unique pour les deux : `agents:` reste
        # (c'est lui qui borne la délégation tant que Local existe, retiré en
        # février 2027), et le plan le déclare (dégradation « agents allow-list »).
        # Pas de `handoffs:` : un bouton par persona
        # après chaque réponse n'aide pas.
        union: list[str] = list(fields["tools"])
        for other in routed:
            union.extend(t for t in map_verbs(other.tools, _TOOL_TABLE) if t not in union)
        fields["tools"] = [*union, "agent"]
        fields["agents"] = [other.name for other in routed]
        verbs = ", ".join(dict.fromkeys(v.value for other in (agent, *routed) for v in other.tools))
        boundary = f"{verbs} (l'union de tes personas), plus la délégation par l'outil `agent` aux personas listées dans `agents`"
    header = Emitter.frontmatter(fields)
    role = (
        "Point d'entrée : sur une demande directe et bornée (`grimoire-agent-dispatch` en juge), tu "
        "fais le travail toi-même avec tes outils ; sinon tu délègues à la persona adaptée avec l'outil "
        "`agent` (brief : contexte, objectif, contraintes) — recherche large ou mesure vers une persona "
        "économique, conclusions seulement — et tu rends son résultat ; la vérification reste la "
        "tienne. Tu ne réponds jamais « je n'ai pas les droits » : tu agis, ou le spécialiste agit."
        if agent.entry_point
        else "Agent routé en interne : tu traites une tranche de travail, tu ne clos pas la tâche globale."
    )
    persona = persona_section(agent)
    content = f"""{header}
{managed_header(".md")}

Tu actives la persona Grimoire **{agent.name}** du projet {surface.project_name}.

{persona}

1. {context_load_instruction(agent)}
2. {role}
3. Frontière d'outils : {boundary}. N'en sors pas.
4. {GROUNDING_RULE}
"""
    content = (
        f"{content}\n{_dispatch_policy_section()}" if agent.entry_point else f"{content}5. {UNCERTAINTIES_BLOCK_RULE}\n"
    )
    for skill in owned_skills:
        content = f"{content}\n{_attached_skill_section(skill)}"
    return EmittedFile(relpath=GH_DIR / "agents" / f"{agent.name}.agent.md", content=content)


def _skill_file(skill: SkillSpec) -> EmittedFile:
    header = Emitter.frontmatter(
        {
            "name": skill.slug,
            "description": skill.description,
            # Champs d'invocation documentés (code.visualstudio.com/docs/copilot/
            # customization/agent-skills), écrits seulement quand la source les déclare.
            "argument-hint": skill.argument_hint,
            "user-invocable": skill.user_invocable,
            "disable-model-invocation": skill.disable_model_invocation,
            "license": skill.license,
            "compatibility": skill.compatibility,
        }
    )
    content = f"{header}\n{managed_header('.md')}\n\n{skill.body}"
    return EmittedFile(relpath=GH_DIR / "skills" / skill.slug / "SKILL.md", content=content)


def _prompt_file(command: CommandSpec) -> EmittedFile:
    header = Emitter.frontmatter(
        {
            "description": command.description,
            "argument-hint": command.argument_hint,
            "agent": "agent",
            "tools": list(map_verbs(command.tools, _TOOL_TABLE)),
        }
    )
    body = command.body
    if command.argument_hint and "${input" not in body:
        body = body.rstrip() + f"\n\nArgument fourni : ${{input:argument:{command.argument_hint}}}\n"
    content = f"{header}\n{managed_header('.md')}\n\n{body}"
    return EmittedFile(relpath=GH_DIR / "prompts" / f"{command.slug}.prompt.md", content=content)


#: Famille d'outils neutre -> noms d'outils du harness « Copilot » (référence :
#: https://docs.github.com/en/copilot/reference/hooks-reference). Les fichiers
#: portent des noms d'événements PascalCase : l'hôte y rapporte le nom d'outil
#: façon Claude (``Bash``, ``Edit``…), et un matcher en liste littérale
#: ``a|b|c`` se déclenche quand un jeton égale le nom runtime *ou* son nom
#: Claude. Les deux nommages sont écrits quand même : la garde ne doit pas
#: dépendre de la table de correspondance de l'hôte.
#:
#: Pas de ``MultiEdit``/``NotebookEdit`` : ces outils n'existent pas chez Copilot,
#: et le fichier n'écrit que des noms que la référence documente.
_MATCHER_TABLE: dict[str, tuple[str, ...]] = {
    # `bash` et `powershell` (Windows) sont tous deux rapportés `Bash`.
    "execute": ("Bash", "bash", "powershell"),
    # `create` (rapporté `Write`) crée un fichier ; `edit`, `str_replace_editor`
    # et `apply_patch` sont tous rapportés `Edit`.
    "write": ("Edit", "Write", "edit", "create", "str_replace_editor", "apply_patch"),
    # Copilot n'a pas de table de permissions déclarative (dégradation
    # « permissions ») : le refus de lecture d'un secret n'existe que dans la
    # garde. La lecture de fichier reste donc dans le matcher, contrairement à
    # Claude Code où la table de refus la couvre sans processus. La recherche
    # (`grep`/`rg` rapportés `Grep`, `glob` rapporté `Glob`) lit ce qu'elle parcourt :
    # même garde que la lecture.
    "secret": ("Read", "view", "Grep", "grep", "rg", "Glob", "glob"),
    "network": ("WebFetch", "WebSearch", "web_fetch", "web_search"),
    # Délégation à un sous-agent : `task`, rapporté `Agent` (`Task` accepté).
    "delegation": ("Agent", "Task", "task"),
}

#: Les outils intégrés que la référence documente, sous leurs deux nommages,
#: qu'ils soient ou non dans un matcher du kit. Tout autre nom (outil MCP, outil
#: propre à VS Code, outil futur) est inconnu : le kit ne sait pas s'il écrit.
_BUILTIN_TOOLS: tuple[str, ...] = tuple(
    dict.fromkeys(
        [
            *(tool for tools in _MATCHER_TABLE.values() for tool in tools),
            *("Grep", "grep", "rg", "Glob", "glob"),
            *("AskUserQuestion", "ask_user", "TodoWrite", "update_todo"),
        ]
    )
)

#: Seconde entrée d'un hook à matcher : tout outil dont le nom n'est *pas* un
#: outil intégré connu. Une valeur qui n'est ni un nom ni une liste ``a|b`` est,
#: selon la référence, une regex sensible à la casse ancrée en ``^(?:…)$``,
#: testée sur le nom Claude (ou runtime faute d'équivalent). Le motif exclut des
#: noms exacts et accepte tout le reste : un nom inconnu, quelle que soit sa
#: casse, passe par la décision. Les deux nommages sont exclus, pour que les
#: outils intégrés ne lancent pas la décision deux fois quel que soit le nom testé.
UNKNOWN_TOOL_MATCHER = "(?!(?:" + "|".join(_BUILTIN_TOOLS) + ")$).*"

#: Événements dont l'entrée accepte un ``matcher`` dans le harness Copilot et
#: pour lesquels le kit en déclare un. Le harness « Local » (déprécié) ignore ce
#: champ : tout outil y déclenche le hook, comme avant, et la décision filtre.
_MATCHED_EVENTS = frozenset({HookEvent.PRE_TOOL_USE, HookEvent.POST_TOOL_USE})

#: Délai de la garde avant outil (secondes). Chez Copilot un hook qui dépasse son
#: délai laisse passer l'appel (échec ouvert, garde comprise) alors qu'une sortie
#: non nulle le refuse : un délai court élargit le cas où la garde ne juge pas.
#: Même pire cas borné que Claude Code (voir ``claude_code.FAIL_CLOSED_TIMEOUT``,
#: #1276), donc même plancher.
GUARD_TIMEOUT = FAIL_CLOSED_TIMEOUT


def matcher_for(hook: HookSpec) -> str:
    """Le ``matcher`` Copilot de *hook* : liste littérale de noms, ou ``""`` (tous les outils)."""
    if hook.event not in _MATCHED_EVENTS:
        return ""
    tools: list[str] = []
    for family in hook.matcher:
        for tool in _MATCHER_TABLE.get(family, ()):
            if tool not in tools:
                tools.append(tool)
    return "|".join(tools)


def _hook_timeout(hook: HookSpec) -> int:
    if hook.event is HookEvent.PRE_TOOL_USE and hook.enforcement is Enforcement.BLOCKING:
        return max(hook.timeout, GUARD_TIMEOUT)
    return hook.timeout


def _powershell_program(program: str) -> str:
    """Un chemin entre guillemets ne se lance sous PowerShell qu'avec l'opérateur ``&``."""
    return f"& {program}" if program.startswith('"') else program


def _dispatch_entry(launcher: HookLauncher = BARE) -> dict[str, Any]:
    """L'entrée consultative ``grimoire.dispatch-model`` du fichier ``PreToolUse`` (#1280, P1-4).

    Même lanceur que la garde, ``--decision`` en plus, matcher sur les seuls
    outils de délégation, délai court (3 s) : un dépassement laisse passer
    l'appel tel quel, ce qui est le comportement voulu d'un hook consultatif.
    Elle vient *après* les entrées de la garde et ne la remplace pas ; elle
    n'affirme jamais de permission (voir ``runtime._render_copilot_advisory_pre_tool``).
    Le harness Local ignore ``matcher`` : elle y part sur chaque outil, et la
    décision s'abstient hors délégation (dégradation déclarée par le plan).
    """
    spec = DISPATCH_MODEL_HOOK
    (entry,) = _hook_entries(spec, launcher)
    for key in ("command", "bash", "powershell"):
        entry[key] = f"{entry[key]} --decision {spec.decision}"
    return {**entry, "matcher": matcher_for(spec)}


#: Hook consultatif ``SubagentStart`` (#1280, P3) : le contexte de dispatch du sous-agent.
#: Propre à Copilot (Claude Code et les autres hôtes gardent leurs hooks). Délai court :
#: un dépassement laisse démarrer le sous-agent sans contexte ajouté.
SUBAGENT_START_HOOK = HookSpec(
    event=HookEvent.SUBAGENT_START,
    decision="grimoire.subagent-start",
    enforcement=Enforcement.ADVISORY,
    timeout=3,
    rationale="Dit au sous-agent la classe de vérifiabilité et le palier recommandés pour sa tâche.",
)


def _subagent_start_file(launcher: HookLauncher = BARE) -> EmittedFile:
    """``grimoire-subagent-start.json`` : une entrée, ``--decision`` en plus du lanceur commun."""
    (entry,) = _hook_entries(SUBAGENT_START_HOOK, launcher)
    for key in ("command", "bash", "powershell"):
        entry[key] = f"{entry[key]} --decision {SUBAGENT_START_HOOK.decision}"
    payload: dict[str, Any] = {"version": 1, "hooks": {"SubagentStart": [entry]}}
    return EmittedFile(
        relpath=GH_DIR / "hooks" / "grimoire-subagent-start.json",
        content=json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        managed=False,
        owned_if_contains=OWNED_COMMAND_MARKERS,
    )


def _hook_entries(hook: HookSpec, launcher: HookLauncher = BARE) -> list[dict[str, Any]]:
    """Les entrées de *hook* : une, ou deux pour un hook à matcher (outils connus, puis inconnus)."""
    event = _WIRE_NAMES[hook.event]
    entry: dict[str, Any] = {"type": "command"}
    if launcher.project_relative and launcher.windows:
        # Issue #760 : un venv du projet n'est pas dans le PATH de VS Code. Le
        # chemin part de la racine (``cwd``), et Windows a sa propre forme.
        posix = Emitter.hook_command(HOST_ALIAS, event, f"./{launcher.posix}")
        windows = Emitter.hook_command(HOST_ALIAS, event, f".\\{launcher.windows}")
        cwd: str | None = "."
    else:
        program = Emitter.quoted_program(launcher.posix)
        posix = Emitter.hook_command(HOST_ALIAS, event, program)
        windows = Emitter.hook_command(HOST_ALIAS, event, _powershell_program(program))
        cwd = None
    entry["command"] = posix
    entry["bash"] = posix
    entry["powershell"] = windows
    if cwd:
        entry["cwd"] = cwd
    entry["timeoutSec"] = _hook_timeout(hook)
    if hook.decision == DISPATCH_MODEL_HOOK.decision:
        return [entry]
    matcher = matcher_for(hook)
    if matcher:
        # Avant les matchers, chaque appel passait par la décision : la seconde
        # entrée garde ce comportement pour les outils que le kit ne connaît pas.
        return [{**entry, "matcher": matcher}, {**entry, "matcher": UNKNOWN_TOOL_MATCHER}]
    return [entry]


def _hook_file(hook: HookSpec, launcher: HookLauncher = BARE, *, dispatch: bool = False) -> EmittedFile:
    """Un fichier ``.github/hooks/grimoire-<événement>.json`` au format du harness Copilot.

    ``{"version": 1, "hooks": {"<Événement>": [entrée]}}``, entrée portant
    ``bash`` (Unix) et ``powershell`` (Windows), plus ``command`` comme repli.
    Pas de ``windows`` (issue #760) : le harness Copilot ne le documente que
    pour les hooks propres à un agent, et le harness Local prend ``powershell``
    comme repli de ``windows`` — la forme Windows du lanceur passe donc par
    ``powershell``, lu par les deux harness. ``command`` porte la forme Unix :
    le harness Copilot le copie dans ``bash``/``powershell`` seulement quand
    ceux-ci manquent, il ne sert qu'aux VS Code antérieurs aux deux replis.
    ``windows`` n'est pas réintroduit pour ces derniers : la référence GitHub ne
    le liste pas pour un fichier de hook, ne dit rien d'un champ inconnu, et
    écarte un élément malformé — l'élément ici, c'est la garde elle-même.

    Le délai s'écrit ``timeoutSec`` seul : les deux harness le lisent, et ils
    donnent des priorités opposées à ``timeout`` et ``timeoutSec`` posés ensemble.

    *dispatch* ajoute, après les entrées de *hook* (``PreToolUse`` seulement),
    l'entrée consultative du modèle imposé aux sous-agents (:func:`_dispatch_entry`).
    """
    event = _WIRE_NAMES[hook.event]
    entries = _hook_entries(hook, launcher)
    if dispatch and hook.event is HookEvent.PRE_TOOL_USE:
        entries.append(_dispatch_entry(launcher))
    payload: dict[str, Any] = {"version": 1, "hooks": {event: entries}}
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
        f"| Serveurs MCP | {len([m for m in surface.mcp_servers if m.name in KIT_SERVERS])} — `.vscode/mcp.json` |",
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
        "Cet hôte ne documente aucun nom de modèle par palier : `model` n'est",
        "émis que pour un palier déclaré sous `dispatch.copilot_models` (voir la",
        "dégradation « model affinity » ci-dessous), le contrat",
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
        launcher = resolve_hook_launcher(project_root)
        files: list[EmittedFile] = []
        by_slug = {skill.slug: skill for skill in surface.skills}
        attached_slugs = {slug for agent in surface.agents for slug in agent.skills}
        copilot_models = _model_tiers(project_root)
        files.extend(
            _agent_file(
                agent,
                surface,
                owned_skills=tuple(by_slug[slug] for slug in agent.skills if slug in by_slug),
                copilot_models=copilot_models,
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
        files.extend(_hook_file(hook, launcher, dispatch=True) for hook in surface.hooks)
        files.append(_subagent_start_file(launcher))

        degradations: list[Degradation] = list(Emitter.launcher_degradations(launcher))
        if not surface.permissions.is_empty():
            degradations.append(
                Degradation(
                    surface="permissions",
                    reason="Copilot n'expose pas de table de permissions déclarative.",
                    fallback="règles appliquées par le hook PreToolUse (mêmes refus, même formulation).",
                )
            )
        if any(matcher_for(h) for h in surface.hooks):
            degradations.append(
                Degradation(
                    surface="hook matchers",
                    reason=(
                        "Les hooks à matcher (garde PreToolUse, trace PostToolUse) nomment les outils "
                        "intégrés de Copilot qu'ils jugent, et une seconde entrée rattrape tout outil "
                        "non intégré (MCP, outils propres à VS Code). Restent hors garde : les outils "
                        "intégrés de recherche, web, question, liste de tâches et délégation (Grep, Glob, "
                        "WebFetch, WebSearch, AskUserQuestion, TodoWrite, Agent/Task), un outil tiers "
                        "homonyme de l'un d'eux, et la seconde entrée si l'hôte ne compile pas son motif "
                        "(entrée sautée, la première reste). Le harness Local ignore `matcher` : tout "
                        "outil y passe par la décision."
                    ),
                    fallback=(
                        "aucun de ces outils n'écrit ni n'exécute : une écriture ou une commande passe "
                        "par `edit`/`create`/`bash`, jugés. La recherche (Grep/Glob) parcourt des fichiers "
                        "sans le refus de lecture des secrets, que la décision ne leur applique pas non plus."
                    ),
                )
            )
        if any(h.event is HookEvent.PRE_TOOL_USE and h.enforcement is Enforcement.BLOCKING for h in surface.hooks):
            degradations.append(
                Degradation(
                    surface="hook timeout",
                    reason=(
                        "Chez Copilot, un hook qui dépasse son délai laisse passer l'appel d'outil, "
                        "garde PreToolUse comprise (seule une sortie non nulle refuse)."
                    ),
                    fallback=f"délai de la garde porté à {GUARD_TIMEOUT} s ; `grimoire doctor` le rappelle.",
                )
            )
        if any(h.event is HookEvent.PRE_TOOL_USE for h in surface.hooks):
            degradations.append(
                Degradation(
                    surface="dispatch model",
                    reason=(
                        "Le modèle de la politique de dispatch est proposé aux sous-agents (`task`, "
                        "`runSubagent`, `Agent`/`Task`) par une entrée consultative du hook PreToolUse, "
                        "mais Copilot nomme ses modèles autrement que les paliers de la politique et ne "
                        "documente pas les valeurs que `task` accepte : aucune correspondance n'est inventée. "
                        "L'entrée réécrite (`modifiedArgs`, ou aussi `updatedInput` pour une charge snake_case) "
                        "est rendue sans `permissionDecision` ; un lot de plusieurs appels n'est jamais réécrit. "
                        "Le harness Local ignore `matcher` : l'entrée y part sur chaque outil (3 s au plus)."
                    ),
                    fallback=(
                        "sans `dispatch.models.copilot.<palier>` (alias `dispatch.copilot_models`) dans la politique d'orchestration du standard, "
                        "le palier recommandé est seulement dit à l'agent (additionalContext) ; "
                        "`dispatch.impose_model: off` coupe la décision."
                    ),
                )
            )
        unmapped = sorted(
            agent.name
            for agent in surface.agents
            if (tier := tier_for(agent)) != "inherit" and tier not in copilot_models
        )
        if unmapped:
            degradations.append(
                Degradation(
                    surface="model affinity",
                    reason=(
                        "L'agent personnalisé VS Code (.github/agents/*.agent.md) accepte un nom de "
                        "modèle explicite ou une liste de repli via `model`, mais aucune valeur de "
                        "sélection automatique équivalente à `inherit` n'y est documentée, et Copilot "
                        "nomme ses modèles autrement que les paliers du kit. Personas dont le palier n'a "
                        f"pas de nom Copilot déclaré : {', '.join(unmapped)}."
                    ),
                    fallback=(
                        "`model` n'est émis que pour un palier déclaré sous `dispatch.models.copilot.<palier>` (alias `dispatch.copilot_models`) "
                        "de la politique d'orchestration ; les autres personas n'ont pas de `model` et le "
                        "modèle actuel du sélecteur VS Code s'applique."
                    ),
                )
            )
        if any(a.entry_point for a in surface.agents) and any(not a.entry_point for a in surface.agents):
            degradations.append(
                Degradation(
                    surface="agents allow-list",
                    reason=(
                        "`agents:` (liste des sous-agents que l'agent d'entrée peut lancer) est « Local only » "
                        "dans la référence du fichier d'agent VS Code ; le harness Copilot délègue par son "
                        "outil natif `task`, sans cette liste, et le harness Local sera retiré en février 2027."
                    ),
                    fallback=(
                        "`agents:` reste émis (un seul fichier sert les deux harness, la liste borne la "
                        "délégation sous Local) ; sous le harness Copilot la délégation n'est pas bornée "
                        "par cette liste."
                    ),
                )
            )
        degradations.append(
            Degradation(
                surface="mcp cloud agent",
                reason=(
                    "`.vscode/mcp.json` (VS Code) est écrit, et la CLI Copilot lit `.mcp.json` à la racine ; "
                    "l'agent cloud de GitHub ne lit aucun fichier du dépôt : sa configuration MCP se saisit "
                    "dans les réglages du dépôt (Settings > Copilot > MCP servers), avec `tools` obligatoire."
                ),
                fallback=(
                    "la configuration à saisir est dans docs/reference/hotes.md ; un serveur stdio local "
                    "du poste n'y existe pas, le kit n'y écrit donc rien."
                ),
            )
        )
        blocking = [h for h in surface.hooks if h.enforcement is Enforcement.BLOCKING]
        files.append(_readme(surface, blocking, degradations))
        mcp_merge = vscode_mcp_merge(surface.mcp_servers)
        merges = (mcp_merge,) if mcp_merge is not None else ()
        plan = EmitPlan(host_id=self.host_id, files=tuple(files), merges=merges, degradations=tuple(degradations))
        return with_rule_layers(plan, project_root)
