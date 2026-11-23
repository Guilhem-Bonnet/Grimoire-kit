"""What each host can actually execute, and what it can only be told.

:mod:`grimoire.bridges.schemas` already records *hook* availability per host.
This module extends that with the surfaces an emitter needs to decide what to
write: does the host load sub-agent files, skill folders, slash commands, a
permission table? And — the field that keeps everyone honest — can its hooks
*block*, or only advise?

A capability is claimed here only when the host documents it. When a host
lacks one, the emitter degrades explicitly (see :class:`Degradation` in
:mod:`grimoire.hosts.emitters.base`) rather than writing a file the host will
never read.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from grimoire.bridges.schemas import (
    _HOST_REGISTRY,
    HostCapabilityManifest,
    HostId,
)

# From the leaf module (issue #419), not ``grimoire.hosts.surface``: this
# module is imported on every hook call (``profile_for``/``resolve_host``),
# and pulling the full agent/model IR — ``AgentSpec``, ``ModelAffinity``,
# the Rust-optional fingerprint machinery — for one enum was exactly the
# tax a hook decision must not pay.
from grimoire.hosts.events import HookEvent

#: Hook events mapped onto the ``HostHooks`` field names used by the bridge
#: manifests, so availability has exactly one source of truth.
_HOOK_FIELDS: dict[HookEvent, str] = {
    HookEvent.SESSION_START: "session_start",
    HookEvent.USER_PROMPT_SUBMIT: "user_prompt_submit",
    HookEvent.PRE_TOOL_USE: "pre_tool_use",
    HookEvent.POST_TOOL_USE: "post_tool_use",
    HookEvent.POST_TOOL_USE_FAILURE: "post_tool_use_failure",
    HookEvent.SUBAGENT_START: "subagent_start",
    HookEvent.SUBAGENT_STOP: "subagent_stop",
    HookEvent.PRE_COMPACT: "pre_compact",
    HookEvent.STOP: "stop",
}


@dataclass(frozen=True, slots=True)
class HostProfile:
    """Executable surfaces offered by one host."""

    host_id: HostId
    display_name: str
    #: Loads per-agent definition files that get their own context window.
    subagents_native: bool = False
    #: Loads on-demand skill folders (progressive disclosure).
    skills_native: bool = False
    #: Loads user-invocable command files (slash commands).
    commands_native: bool = False
    #: Hooks can refuse a tool call or a turn closure, not merely inject text.
    blocking_hooks: bool = False
    #: When set, the only events that can actually refuse something. Some hosts
    #: block an action without documenting any way to refuse the end of a turn
    #: (Gemini CLI: ``BeforeTool``/``BeforeAgent`` block, ``AfterAgent`` does
    #: not); a single boolean would turn the second case into a claim nobody
    #: observed. ``None`` means "trust :attr:`blocking_hooks`".
    blocking_events: frozenset[HookEvent] | None = None
    #: Événements dont l'hôte **documente** un refus qu'aucune capture n'a confirmé
    #: (#1281 : « une capacité documentée mais non prouvée vaut advisory, jamais
    #: enforced »). Le kit y écrit sa garde, mais :meth:`blocks` reste faux : la
    #: matrice les rend ``advisory`` avec cette raison, pas ``enforced``.
    documented_blocking_events: frozenset[HookEvent] = frozenset()
    #: Captures réelles (chemins relatifs à ``tests/fixtures/hosts/``) qui prouvent
    #: qu'un refus de cet hôte arrête vraiment l'action. Aucun événement n'est tenu
    #: pour bloquant sans elles : :func:`unproven_blocking_claims` le vérifie, et un
    #: test transverse fait échouer la CI sur toute déclaration orpheline (#1281).
    blocking_captures: tuple[str, ...] = ()
    #: What the user must do before the host runs the project's hooks at all
    #: (Codex: review them through ``/hooks``). Empty: the hooks run as written.
    hook_approval: str = ""
    #: Can be told to open a session *inside* a named agent.
    #: True only where the host documents it (issue #716): Claude Code with
    #: ``claude --agent <name>`` (CLI reference, "Specify an agent for the
    #: current session"), ``claude --agent <name> --bg`` (agent view) and the
    #: ``agent`` setting ("Run the main thread as a named subagent"); its hooks
    #: then carry ``agent_type``. Copilot CLI documents ``copilot --agent <id>
    #: --prompt ...`` and ``/agent`` (docs.github.com, "Creating custom agents
    #: for Copilot CLI"), but the profile stays False: it describes the VS Code
    #: host, where an agent file opens only through the chat dropdown, and no
    #: source says the Copilot hook payloads carry the agent's identity (what
    #: lets Claude Code's session_start skip the entry persona). The others have
    #: no equivalent — the substitute below is a declared degradation, not folklore.
    agent_autostart: bool = False
    #: Has a declarative allow/deny/ask permission table.
    permissions_native: bool = False
    #: Reads an MCP client configuration.
    mcp_native: bool = False
    #: Entry-point file the host reads first, when it has one.
    instructions_entrypoint: str = ""
    #: Command (argv) that starts the host's ACP agent over stdio, run from the
    #: project folder (``grimoire chat``). Empty: no ACP mode known for this host.
    acp_command: tuple[str, ...] = ()
    #: Exécutable dont la présence dit que l'hôte est installé, quand ce n'est pas
    #: ``acp_command[0]`` (Codex se lance par ``npx`` mais s'installe en ``codex``).
    acp_binary: str = ""
    #: Le mode ACP de cet hôte est en preview : il peut changer ou casser sans préavis.
    acp_preview: bool = False
    #: Ce que l'utilisateur doit savoir du mode ACP de cet hôte (preview, migration d'outil…).
    acp_note: str = ""
    notes: str = ""

    @property
    def manifest(self) -> HostCapabilityManifest | None:
        return _HOST_REGISTRY.get(self.host_id)

    def supports_event(self, event: HookEvent) -> bool:
        manifest = self.manifest
        if manifest is None:
            return False
        field_name = _HOOK_FIELDS.get(event)
        if field_name is None:
            # Événement déclaré mais non câblé (``NOT_EMITTED_EVENTS``) : aucun
            # hôte ne le reçoit, donc aucun émetteur ne l'écrit.
            return False
        return bool(getattr(manifest.hooks, field_name, False))

    def blocks(self, event: HookEvent) -> bool:
        """Cet hôte peut-il **refuser** *event*, ou seulement l'informer ?"""
        if self.blocking_events is not None:
            return event in self.blocking_events
        return self.blocking_hooks

    def documents_unverified_block(self, event: HookEvent) -> bool:
        """L'hôte documente-t-il un refus sur *event* sans qu'il ait été observé ?"""
        return not self.blocks(event) and event in self.documented_blocking_events

    def writes_refusal(self, event: HookEvent) -> bool:
        """Le runtime rend-il un refus dans le dialecte de l'hôte pour *event* ?

        C'est le **comportement** du kit, distinct de ce qu'il **déclare** : un refus
        documenté mais jamais capturé est quand même écrit (sinon la garde ne servirait
        à rien le jour où l'hôte l'honore), tout en restant ``advisory`` partout où le
        kit annonce ses capacités (:meth:`blocks`, matrice, ``grimoire host list``).
        """
        return self.blocks(event) or event in self.documented_blocking_events

    def proven_blocking_events(self) -> tuple[HookEvent, ...]:
        """Événements déclenchés par l'hôte et tenus pour bloquants (refus prouvé)."""
        return tuple(e for e in self.supported_events() if self.blocks(e))

    @property
    def enforces_blocking(self) -> bool:
        """Au moins un hook de cet hôte refuse réellement (capture à l'appui)."""
        return bool(self.proven_blocking_events())

    def supported_events(self) -> tuple[HookEvent, ...]:
        return tuple(e for e in HookEvent if self.supports_event(e))

    def to_dict(self) -> dict[str, Any]:
        return {
            "host_id": self.host_id.value,
            "display_name": self.display_name,
            "surfaces": {
                "subagents": self.subagents_native,
                "skills": self.skills_native,
                "commands": self.commands_native,
                "blocking_hooks": self.enforces_blocking,
                "agent_autostart": self.agent_autostart,
                "permissions": self.permissions_native,
                "mcp": self.mcp_native,
            },
            "hook_approval": self.hook_approval,
            "unverified_blocking_events": [
                e.value for e in self.supported_events() if self.documents_unverified_block(e)
            ],
            "hook_events": [e.value for e in self.supported_events()],
            "instructions_entrypoint": self.instructions_entrypoint,
            "notes": self.notes,
        }


CLAUDE_CODE_PROFILE = HostProfile(
    host_id=HostId.CLAUDE_CODE_CLI,
    display_name="Claude Code",
    subagents_native=True,
    skills_native=True,
    commands_native=True,
    blocking_hooks=True,
    # Seul le refus PreToolUse est capturé (2.1.301). Le kit écrit aussi les refus
    # documentés (`decision: block` sur Stop/SubagentStop/UserPromptSubmit/PostToolUse,
    # code.claude.com/docs/en/hooks) : rendus à l'identique, annoncés advisory.
    blocking_events=frozenset({HookEvent.PRE_TOOL_USE}),
    documented_blocking_events=frozenset(
        {HookEvent.USER_PROMPT_SUBMIT, HookEvent.POST_TOOL_USE, HookEvent.SUBAGENT_STOP, HookEvent.STOP}
    ),
    blocking_captures=(
        "claude-code/01-pretooluse-control-allow.json",
        "claude-code/01-pretooluse-deny-exit2.json",
        "claude-code/01-pretooluse-deny-json.json",
    ),
    agent_autostart=True,
    permissions_native=True,
    mcp_native=True,
    instructions_entrypoint="CLAUDE.md",
    acp_command=("claude-agent-acp",),
    notes=(
        "Every surface has a native counterpart. Starting inside an agent: `claude --agent <name>` "
        "(code.claude.com/docs/en/cli-reference) and the `agent` setting "
        "(code.claude.com/docs/en/settings-reference); SessionStart then carries `agent_type` "
        "(code.claude.com/docs/en/hooks) and the session_start hook skips the entry persona (#716)."
    ),
)

COPILOT_PROFILE = HostProfile(
    host_id=HostId.GITHUB_COPILOT,
    display_name="GitHub Copilot (VS Code)",
    subagents_native=True,
    skills_native=True,
    commands_native=True,
    # Refus documentés (`permissionDecision: deny` + code 2 sur preToolUse,
    # `decision: block` sur Stop/SubagentStop) et écrits par le runtime, mais aucun
    # n'a été observé : les journaux relus (#1280) ne montrent que des entrées de hook.
    # Tant que ce n'est pas capturé, la garde est advisory (#1281).
    blocking_hooks=False,
    blocking_events=frozenset(),
    documented_blocking_events=frozenset({HookEvent.PRE_TOOL_USE, HookEvent.STOP, HookEvent.SUBAGENT_STOP}),
    permissions_native=False,
    mcp_native=True,
    instructions_entrypoint=".github/copilot-instructions.md",
    acp_command=("copilot", "--acp"),
    acp_preview=True,
    acp_note=(
        "Copilot CLI (`copilot --acp`) est en preview : le protocole peut changer. "
        "Copilot dans VS Code n'a aucune interface externe : le chat écrit alors dans le kanban."
    ),
    notes=(
        "No declarative permission table: permission rules are written into the pre_tool_use hook instead, "
        "advisory until a refusal is captured. "
        "Starting inside an agent: `copilot --agent <id>` in the CLI (docs.github.com, custom agents for "
        "Copilot CLI), the chat dropdown in VS Code; not wired here, the session_start hook injects the entry persona. "
        "Contrat réel (#1280, P1-0) : entrées de hooks du harness Copilot relues dans son journal de session "
        "(camelCase, preToolUse en liste `toolCalls`), sorties affichées du harness Local ; aucun refus observé, "
        "version d'hôte inconnue : rien n'est vérifié (voir `grimoire.hosts.copilot_wire`)."
    ),
)

CODEX_PROFILE = HostProfile(
    host_id=HostId.CODEX,
    display_name="Codex",
    subagents_native=True,
    skills_native=True,
    commands_native=False,
    blocking_hooks=False,
    # Refus PreToolUse, relance Stop/SubagentStop, blocage UserPromptSubmit et
    # PostToolUse documentés (learn.chatgpt.com/docs/hooks, lue le 2026-11-21),
    # mais rien de prouvé : CLI absente du poste, et un hook de projet ne
    # s'exécute qu'approuvé via /hooks. Aucun événement n'est donc tenu pour bloquant.
    blocking_events=frozenset(),
    documented_blocking_events=frozenset(
        {
            HookEvent.PRE_TOOL_USE,
            HookEvent.USER_PROMPT_SUBMIT,
            HookEvent.POST_TOOL_USE,
            HookEvent.SUBAGENT_STOP,
            HookEvent.STOP,
        }
    ),
    hook_approval="approbation requise via /hooks : Codex n'exécute un hook de projet qu'une fois approuvé",
    permissions_native=False,
    mcp_native=True,
    instructions_entrypoint="AGENTS.md",
    acp_command=("npx", "-y", "@agentclientprotocol/codex-acp"),
    acp_binary="codex",
    acp_note="Adaptateur ACP de Codex lancé par npx (@agentclientprotocol/codex-acp) : exige Node.js et la CLI `codex`.",
    notes=(
        "Hooks (.codex/hooks.json), skills (.agents/skills), sous-agents (.codex/agents/*.toml) et MCP "
        "(.codex/config.toml) écrits d'après learn.chatgpt.com/docs (lue le 2026-11-21 ; CLI non installée). "
        "Hooks de projet exécutés seulement après approbation via /hooks ; aucun refus observé : "
        "garde et gate de clôture consultatifs. Pas de commandes de projet documentées."
    ),
)

CURSOR_PROFILE = HostProfile(
    host_id=HostId.CURSOR,
    display_name="Cursor",
    subagents_native=True,
    skills_native=True,
    commands_native=False,
    # Refus documenté (`preToolUse`, `beforeShellExecution` : `permission: "deny"`, ou
    # code 2), jamais observé : aucune capture, Cursor absent du poste. Tant que ce
    # n'est pas prouvé, aucun événement ne « bloque » (matrice : advisory).
    blocking_hooks=False,
    blocking_events=frozenset(),
    documented_blocking_events=frozenset({HookEvent.PRE_TOOL_USE}),
    permissions_native=False,
    mcp_native=True,
    instructions_entrypoint=".cursor/rules/grimoire.mdc",
    acp_command=("agent", "acp"),
    notes=(
        "Hooks (.cursor/hooks.json), sous-agents (.cursor/agents/), compétences (.cursor/skills/) et "
        "règles écrits d'après cursor.com/docs (hooks, context/subagents, context/skills ; lue le "
        "2026-11-21, Cursor non installé). preToolUse et beforeShellExecution documentent un refus, "
        "stop une relance par followup_message (loop_limit 5, compté par conversation) ; rien n'est vérifié, la garde est "
        "advisory. Mode headless `agent -p` documenté, non utilisé par le kit."
    ),
)

GEMINI_PROFILE = HostProfile(
    host_id=HostId.GEMINI_CLI,
    display_name="Gemini CLI",
    subagents_native=True,
    # `.agents/skills/<slug>/SKILL.md`, activées à la demande (`activate_skill`) :
    # geminicli.com/docs/cli/skills (lue le 2026-11-22) ; Antigravity lit le même dossier.
    skills_native=True,
    commands_native=True,
    # BeforeTool et BeforeAgent documentent un refus (`decision: deny`, code 2) ;
    # AfterAgent/SessionEnd sont des hooks, pas des points de blocage. Aucune capture
    # (CLI absente du poste) : rien n'est tenu pour bloquant, la garde est advisory.
    blocking_hooks=False,
    blocking_events=frozenset(),
    documented_blocking_events=frozenset({HookEvent.PRE_TOOL_USE, HookEvent.USER_PROMPT_SUBMIT}),
    permissions_native=False,
    mcp_native=True,
    instructions_entrypoint="GEMINI.md",
    acp_command=("gemini", "--acp"),
    acp_note=(
        "Gemini CLI cède la place à Antigravity CLI pour les comptes gratuits (juin 2026) : "
        "`gemini --acp` reste valable tant que la CLI est installée ; sur un compte gratuit, "
        "migrer vers Antigravity CLI et mettre à jour la commande ACP du profil."
    ),
    notes=(
        "Hooks, commandes TOML, sous-agents et MCP sont écrits d'après geminicli.com/docs "
        "(lue le 2026-11-12, v0.64.0 annoncée ; CLI non installée sur le poste). "
        "Compétences natives dans `.agents/skills/` et `model` d'un sous-agent tiré de "
        "`dispatch.models.gemini` (relu le 2026-11-22) ; Antigravity lit ces compétences et `GEMINI.md`, "
        "ses hooks (`.agents/hooks.json`), agents et MCP ne sont pas émis. "
        "BeforeTool et BeforeAgent documentent un refus, jamais observé : garde advisory ; "
        "SessionStart/AfterTool/PreCompress informent ; le blocage de fin de tour (AfterAgent) et le "
        "format des fichiers de session ne sont pas vérifiés. Aucune session d'agent détachée documentée."
    ),
)

OPENCODE_PROFILE = HostProfile(
    host_id=HostId.OPENCODE,
    display_name="opencode",
    subagents_native=True,
    skills_native=True,
    commands_native=True,
    # Seul `tool.execute.before` (PreToolUse) est prouvé bloquant. `session.idle`
    # alimente la décision `stop` (opencode.ai/docs/en/plugins, lue le 2026-11-22), mais
    # aucun moyen de refuser la fin de tour n'est documenté : advisory, sans capture.
    blocking_hooks=True,
    blocking_events=frozenset({HookEvent.PRE_TOOL_USE}),
    blocking_captures=(
        "opencode/01-pretooluse-control-allow.json",
        "opencode/01-pretooluse-deny-throw.json",
    ),
    permissions_native=False,
    mcp_native=True,
    instructions_entrypoint=".opencode/grimoire.md",
    acp_command=("opencode", "acp"),
    notes=(
        "Agents, skills, commands et MCP sont chargés depuis .opencode/ et opencode.json (vérifié, 1.17.11). "
        "Le plugin de garde refuse un outil dans tool.execute.before (fixture) et rend la décision de "
        "clôture sur l'événement session.idle, journalisée sans blocage (observé le 2026-11-22 : "
        "opencode/02-session-idle-stop-advisory.json ; aucun refus de fin de tour documenté). `model` d'un agent tiré de `dispatch.models.opencode` (format "
        "provider/model-id). Pas de SessionStart ni PostToolUse ; table de permissions non émise (non vérifiée)."
    ),
)

_PROFILES: dict[HostId, HostProfile] = {
    p.host_id: p
    for p in (CLAUDE_CODE_PROFILE, COPILOT_PROFILE, CODEX_PROFILE, CURSOR_PROFILE, GEMINI_PROFILE, OPENCODE_PROFILE)
}

#: Short ids accepted on the command line, mapped to their host.
HOST_ALIASES: dict[str, HostId] = {
    "claude": HostId.CLAUDE_CODE_CLI,
    "claude-code": HostId.CLAUDE_CODE_CLI,
    # Graphie sans préfixe ``host-`` que le ledger a reçue (``task attach``).
    "claude-code-cli": HostId.CLAUDE_CODE_CLI,
    "copilot": HostId.GITHUB_COPILOT,
    "github-copilot": HostId.GITHUB_COPILOT,
    "codex": HostId.CODEX,
    "cursor": HostId.CURSOR,
    "gemini": HostId.GEMINI_CLI,
    "gemini-cli": HostId.GEMINI_CLI,
    "opencode": HostId.OPENCODE,
}


def resolve_host(name: str) -> HostId | None:
    """Resolve a CLI alias or a full host id, case-insensitively."""
    key = name.strip().lower()
    if key in HOST_ALIASES:
        return HOST_ALIASES[key]
    try:
        return HostId(key)
    except ValueError:
        return None


def profile_for(host_id: HostId) -> HostProfile:
    """Profile for *host_id*; unknown hosts get a prose-only profile."""
    return _PROFILES.get(
        host_id,
        HostProfile(host_id=HostId.UNKNOWN, display_name="Unknown host", notes="No known executable surface."),
    )


def all_profiles() -> tuple[HostProfile, ...]:
    return tuple(_PROFILES.values())


@dataclass(frozen=True, slots=True)
class CapabilityGap:
    """One surface a host cannot execute, and what stands in for it."""

    surface: str
    fallback: str
    events: tuple[str, ...] = field(default_factory=tuple)


def gaps_for(profile: HostProfile) -> tuple[CapabilityGap, ...]:
    """Surfaces *profile* cannot execute natively, with their substitute."""
    gaps: list[CapabilityGap] = []
    if not profile.subagents_native:
        gaps.append(CapabilityGap("subagents", "agents described in the instructions entrypoint"))
    if not profile.skills_native:
        gaps.append(CapabilityGap("skills", "skills inlined as a catalog in the instructions entrypoint"))
    if not profile.commands_native:
        gaps.append(CapabilityGap("commands", "commands listed as CLI invocations the agent must run"))
    if not profile.agent_autostart:
        # A host that cannot enter an agent can still be handed its persona
        # (#716: Claude Code can — `claude --agent` — and has no gap here).
        # Where session_start runs, the hook injects it into the main loop;
        # where it does not, the entrypoint file is the only place left.
        if profile.supports_event(HookEvent.SESSION_START):
            fallback = "entry persona injected into the main loop by the session_start hook"
            events: tuple[str, ...] = (HookEvent.SESSION_START.value,)
        else:
            fallback = f"entry persona named in {profile.instructions_entrypoint or 'the instructions entrypoint'}"
            events = ()
        gaps.append(CapabilityGap("agent_autostart", fallback, events))
    if not profile.mcp_native:
        # Hôte inconnu : on ne sait pas, on ne dit pas qu'il n'a pas de client MCP.
        if profile.host_id is HostId.UNKNOWN:
            gaps.append(CapabilityGap("mcp", "non déterminé : hôte inconnu, client MCP ni affirmé ni exclu"))
        else:
            gaps.append(CapabilityGap("mcp", "outils du kit atteints par la CLI `grimoire`, sans client MCP"))
    if profile.hook_approval:
        gaps.append(CapabilityGap("hook_approval", profile.hook_approval))
    if not profile.enforces_blocking and profile.documented_blocking_events:
        documented = tuple(e.value for e in _HOOK_FIELDS if e in profile.documented_blocking_events)
        gaps.append(
            CapabilityGap(
                "blocking_hooks",
                "refus documenté par l'hôte, garde écrite mais non vérifiée (aucune capture) : enforced by CI only",
                documented,
            )
        )
    elif not profile.enforces_blocking:
        missing = tuple(e.value for e in _HOOK_FIELDS if not profile.supports_event(e))
        gaps.append(CapabilityGap("blocking_hooks", "governance stated as instructions, enforced by CI only", missing))
    elif not profile.permissions_native:
        gaps.append(CapabilityGap("permissions", "permission rules enforced through the pre_tool_use hook"))
    return tuple(gaps)


# ---------------------------------------------------------------------------
# Parité des fonctions de gouvernance : ce que chaque hôte fait réellement de
# chacune. Une fonction livrée pour Claude Code peut rester absente ailleurs
# sans que rien ne le dise ; cette table est ce « dire ». Trois états :
#
# - ``rich`` : la fonction est pleinement disponible sur cet hôte ;
# - ``degraded`` : elle existe sous une forme réduite, la raison nomme la limite ;
# - ``absent`` : elle n'y est pas exécutée, la raison nomme ce qui reste.
#
# La table est écrite à la main, pas déduite des profils : c'est le contrat
# qu'un test exige (``tests/unit/hosts/test_host_parity_declared.py``). Ajouter
# une fonction à :class:`GovernanceFunction` sans lui déclarer un état pour
# chaque :class:`HostId` fait échouer la CI, et :func:`function_states` lève
# ``KeyError`` au premier appel plutôt que de laisser un trou passer.
# ---------------------------------------------------------------------------


class GovernanceFunction(StrEnum):
    """Une fonction de gouvernance attendue d'un hôte, nommée une fois pour tous."""

    TOOL_GUARD = "tool_guard"
    WRITE_TRACE = "write_trace"
    TASK_RECALL = "task_recall"
    COMMANDS = "commands"
    PERSONAS = "personas"
    MCP_SERVER = "mcp_server"
    WORKER_LAUNCH = "worker_launch"
    REVIEW = "review"
    ARBITER = "arbiter"
    PILOT = "pilot"


GOVERNANCE_FUNCTIONS: tuple[GovernanceFunction, ...] = tuple(GovernanceFunction)

#: Libellés affichés par ``grimoire host matrix`` et ``grimoire up``.
GOVERNANCE_LABELS: dict[GovernanceFunction, str] = {
    GovernanceFunction.TOOL_GUARD: "garde avant outil",
    GovernanceFunction.WRITE_TRACE: "compteur d'écritures et clôture",
    GovernanceFunction.TASK_RECALL: "rappel de tâche au début de session",
    GovernanceFunction.COMMANDS: "commandes",
    GovernanceFunction.PERSONAS: "personas",
    GovernanceFunction.MCP_SERVER: "serveur MCP",
    GovernanceFunction.WORKER_LAUNCH: "lancement de worker",
    GovernanceFunction.REVIEW: "relecture",
    GovernanceFunction.ARBITER: "arbitre",
    GovernanceFunction.PILOT: "pilote",
}


class ParityLevel(StrEnum):
    """Niveau de disponibilité d'une fonction de gouvernance sur un hôte."""

    RICH = "rich"
    DEGRADED = "degraded"
    ABSENT = "absent"


@dataclass(frozen=True, slots=True)
class FunctionState:
    """État d'une fonction sur un hôte ; ``degraded`` et ``absent`` exigent leur raison."""

    level: ParityLevel
    reason: str = ""

    def __post_init__(self) -> None:
        if self.level is not ParityLevel.RICH and not self.reason.strip():
            msg = f"un état {self.level.value} sans raison ne dit rien : la raison est obligatoire"
            raise ValueError(msg)
        if self.level is ParityLevel.RICH and self.reason.strip():
            msg = "un état rich n'a pas de raison : une limite se déclare degraded ou absent"
            raise ValueError(msg)

    @property
    def rich(self) -> bool:
        return self.level is ParityLevel.RICH

    def to_dict(self) -> dict[str, str]:
        return {"state": self.level.value, "reason": self.reason}


_RICH = FunctionState(ParityLevel.RICH)


def _degraded(reason: str) -> FunctionState:
    return FunctionState(ParityLevel.DEGRADED, reason)


def _absent(reason: str) -> FunctionState:
    return FunctionState(ParityLevel.ABSENT, reason)


#: Le kit ne lance des sessions de fond que par ``claude --bg`` (worker, relecteur, arbitre).
_NO_FOREIGN_WORKER = "le kit ne lance des sessions de fond que par `claude --bg` : aucun worker sur cet hôte"
_NO_FOREIGN_REVIEW = "la relecture s'exécute dans une session `claude --bg` : aucun relecteur sur cet hôte"
_NO_FOREIGN_ARBITER = "l'arbitre est une session `claude --bg` : aucune session d'arbitrage sur cet hôte"
_PILOT_SHARED = (
    "le pilote du cockpit est un processus du kit, mais les sessions qu'il lance passent par `claude --bg`, "
    "pas par cet hôte"
)
_NO_MCP_CONFIG = (
    "le serveur est déclaré dans `.mcp.json` ; l'émetteur de cet hôte ne traduit pas cette configuration, "
    "le branchement reste côté client"
)


def _prose_states(entrypoint: str) -> dict[GovernanceFunction, FunctionState]:
    """État des fonctions sur un hôte en prose (Codex) : règles écrites, rien d'exécuté."""
    return {
        GovernanceFunction.TOOL_GUARD: _absent(
            f"aucun hook de cycle de vie : la politique d'outils n'est énoncée que dans {entrypoint}, opposable en CI"
        ),
        GovernanceFunction.WRITE_TRACE: _absent(
            "ni PostToolUse ni Stop : aucun comptage d'écritures, aucune clôture bloquée"
        ),
        GovernanceFunction.TASK_RECALL: _absent("aucun SessionStart : le rappel de tâche n'est pas émis"),
        GovernanceFunction.COMMANDS: _degraded(
            f"pas de commandes natives ; chaque commande est l'invocation `grimoire host run <slug>` décrite dans {entrypoint}"
        ),
        GovernanceFunction.PERSONAS: _degraded(
            f"pas de fichiers d'agent chargés ; les personas sont listées dans {entrypoint}, à lire et appliquer"
        ),
        GovernanceFunction.MCP_SERVER: _degraded(_NO_MCP_CONFIG),
        GovernanceFunction.WORKER_LAUNCH: _absent(_NO_FOREIGN_WORKER),
        GovernanceFunction.REVIEW: _absent(_NO_FOREIGN_REVIEW),
        GovernanceFunction.ARBITER: _absent(_NO_FOREIGN_ARBITER),
        GovernanceFunction.PILOT: _degraded(_PILOT_SHARED),
    }


_CODEX_UNPROVEN = "approbation requise via /hooks et aucune capture sur un Codex réel"

_CODEX_STATES: dict[GovernanceFunction, FunctionState] = {
    # L'émetteur Codex écrit `.codex/hooks.json`, `.agents/skills/`, `.codex/agents/*.toml`
    # et `.codex/config.toml` (learn.chatgpt.com/docs, lue le 2026-11-21 ; CLI non installée).
    GovernanceFunction.TOOL_GUARD: _degraded(
        "garde PreToolUse écrite dans `.codex/hooks.json` (refus documenté), consultative tant que non prouvée : "
        + _CODEX_UNPROVEN
    ),
    GovernanceFunction.WRITE_TRACE: _degraded(
        "PostToolUse compte les écritures et Stop reçoit la décision de clôture (`decision: block` documenté), "
        "sans preuve : " + _CODEX_UNPROVEN
    ),
    GovernanceFunction.TASK_RECALL: _degraded(
        "SessionStart injecte le rappel (`additionalContext`) une fois les hooks approuvés via /hooks"
    ),
    GovernanceFunction.COMMANDS: _degraded(
        "aucun emplacement de commande de projet documenté ; chaque commande est l'invocation "
        "`grimoire host run <slug>` décrite dans AGENTS.md"
    ),
    GovernanceFunction.PERSONAS: _RICH,
    GovernanceFunction.MCP_SERVER: _degraded(
        "serveurs ajoutés à `.codex/config.toml`, que Codex ne lit que pour un projet approuvé (trusted)"
    ),
    GovernanceFunction.WORKER_LAUNCH: _absent(_NO_FOREIGN_WORKER),
    GovernanceFunction.REVIEW: _absent(_NO_FOREIGN_REVIEW),
    GovernanceFunction.ARBITER: _absent(_NO_FOREIGN_ARBITER),
    GovernanceFunction.PILOT: _degraded(_PILOT_SHARED),
}

#: Cursor (L1 de #1281) : l'émetteur écrit `.cursor/hooks.json`, `.cursor/agents/` et
#: `.cursor/skills/` d'après cursor.com/docs (lue le 2026-11-21). Cursor n'est pas
#: installé sur le poste et aucun refus n'a été capturé : ce qui dépend d'un hook est
#: dégradé (non vérifié), jamais riche.
_CURSOR_UNVERIFIED = "Cursor non installé, aucune capture : non vérifié"
_CURSOR_STATES: dict[GovernanceFunction, FunctionState] = {
    GovernanceFunction.TOOL_GUARD: _degraded(
        "garde écrite dans `.cursor/hooks.json` (`preToolUse`, `beforeShellExecution`, `failClosed`) d'après "
        f"la doc ; {_CURSOR_UNVERIFIED} — déclarée advisory, opposable en CI"
    ),
    GovernanceFunction.WRITE_TRACE: _degraded(
        "`postToolUse` compte les écritures ; `stop` ne refuse pas la clôture : tant que les gates sont "
        "rouges il relance l'agent par `followup_message`, jusqu'à la limite de 5 relances automatiques que "
        f"Cursor compte par conversation (`loop_limit`), plus aucune ensuite ; {_CURSOR_UNVERIFIED}"
    ),
    GovernanceFunction.TASK_RECALL: _degraded(
        f"`sessionStart` injecte la directive (`additional_context`) ; `beforeSubmitPrompt` n'a pas de canal "
        f"de contexte, le rappel de tâche par message n'est pas émis ; {_CURSOR_UNVERIFIED}"
    ),
    GovernanceFunction.COMMANDS: _degraded(
        "pas de commande utilisateur émise ; chaque commande est l'invocation `grimoire host run <slug>` "
        "décrite dans `.cursor/rules/grimoire.mdc`"
    ),
    GovernanceFunction.PERSONAS: _RICH,
    GovernanceFunction.MCP_SERVER: _degraded(_NO_MCP_CONFIG),
    GovernanceFunction.WORKER_LAUNCH: _absent(_NO_FOREIGN_WORKER),
    GovernanceFunction.REVIEW: _absent(_NO_FOREIGN_REVIEW),
    GovernanceFunction.ARBITER: _absent(_NO_FOREIGN_ARBITER),
    GovernanceFunction.PILOT: _degraded(_PILOT_SHARED),
}

_GEMINI_STATES: dict[GovernanceFunction, FunctionState] = {
    # L'émetteur Gemini écrit `.gemini/settings.json` : BeforeTool refuse une
    # action (`{"decision": "deny"}`), SessionStart informe, et les commandes
    # TOML, sous-agents et `mcpServers` sont écrits (geminicli.com/docs lue le
    # 2026-11-12, v0.64.0 annoncée ; la CLI n'est pas installée sur le poste).
    GovernanceFunction.TOOL_GUARD: _degraded(
        "garde écrite dans `.gemini/settings.json` (`BeforeTool`, `BeforeAgent` : `decision: deny`, code 2) "
        "d'après la doc ; CLI non installée, aucune capture : non vérifié — déclarée advisory, opposable en CI"
    ),
    GovernanceFunction.WRITE_TRACE: _degraded(
        "`AfterTool` compte les écritures, mais `AfterAgent`/`SessionEnd` ne bloquent pas la clôture "
        "(non vérifié : la CLI n'est pas installée) — le rappel de clôture reste consultatif"
    ),
    GovernanceFunction.TASK_RECALL: _RICH,
    GovernanceFunction.COMMANDS: _RICH,
    GovernanceFunction.PERSONAS: _RICH,
    GovernanceFunction.MCP_SERVER: _RICH,
    GovernanceFunction.WORKER_LAUNCH: _absent(_NO_FOREIGN_WORKER),
    GovernanceFunction.REVIEW: _absent(_NO_FOREIGN_REVIEW),
    GovernanceFunction.ARBITER: _absent(_NO_FOREIGN_ARBITER),
    GovernanceFunction.PILOT: _degraded(_PILOT_SHARED),
}

#: Copilot : entrées de hooks relues dans ses journaux (#1280), mais aucune sortie
#: acceptée ni aucun refus observé, version d'hôte inconnue.
_COPILOT_UNVERIFIED = "aucun refus capturé sur un Copilot réel (journaux #1280 : entrées seules) : non vérifié"
_COPILOT_STATES: dict[GovernanceFunction, FunctionState] = {
    GovernanceFunction.TOOL_GUARD: _degraded(
        "garde `preToolUse` écrite (`permissionDecision: deny`, code 2) d'après la doc ; "
        f"{_COPILOT_UNVERIFIED} — déclarée advisory, opposable en CI"
    ),
    GovernanceFunction.WRITE_TRACE: _degraded(
        "`PostToolUse` compte les écritures et `Stop` rend la décision de clôture (`decision: block` "
        f"documenté) ; {_COPILOT_UNVERIFIED}"
    ),
    GovernanceFunction.TASK_RECALL: _RICH,
    GovernanceFunction.COMMANDS: _RICH,
    GovernanceFunction.PERSONAS: _RICH,
    # L'émetteur Copilot écrit `.vscode/mcp.json` (clé `servers`, VS Code) ; la CLI lit
    # le `.mcp.json` du scaffolder (docs.github.com, lue le 2026-11-21). L'agent cloud ne
    # lit aucun fichier : sa limite est déclarée dans les dégradations du plan.
    GovernanceFunction.MCP_SERVER: _RICH,
    GovernanceFunction.WORKER_LAUNCH: _absent(_NO_FOREIGN_WORKER),
    GovernanceFunction.REVIEW: _absent(_NO_FOREIGN_REVIEW),
    GovernanceFunction.ARBITER: _absent(_NO_FOREIGN_ARBITER),
    GovernanceFunction.PILOT: _degraded(_PILOT_SHARED),
}

_OPENCODE_STATES: dict[GovernanceFunction, FunctionState] = {
    GovernanceFunction.TOOL_GUARD: _RICH,
    GovernanceFunction.WRITE_TRACE: _degraded(
        "la décision de clôture est rendue sur `session.idle` et journalisée (`client.app.log`), sans refus : "
        "opencode ne documente aucun blocage de fin de tour ; pas de comptage des écritures (PostToolUse)"
    ),
    GovernanceFunction.TASK_RECALL: _absent(
        "aucun SessionStart ni UserPromptSubmit : le rappel de tâche n'est pas émis"
    ),
    GovernanceFunction.COMMANDS: _RICH,
    GovernanceFunction.PERSONAS: _RICH,
    GovernanceFunction.MCP_SERVER: _RICH,
    GovernanceFunction.WORKER_LAUNCH: _absent(_NO_FOREIGN_WORKER),
    GovernanceFunction.REVIEW: _absent(_NO_FOREIGN_REVIEW),
    GovernanceFunction.ARBITER: _absent(_NO_FOREIGN_ARBITER),
    GovernanceFunction.PILOT: _degraded(_PILOT_SHARED),
}

_CLAUDE_CODE_STATES: dict[GovernanceFunction, FunctionState] = dict.fromkeys(GOVERNANCE_FUNCTIONS, _RICH)

#: Hôte inconnu : on ne lui prête aucune fonction ; le client MCP reste indéterminé.
_UNKNOWN_STATES: dict[GovernanceFunction, FunctionState] = {
    function: _absent("hôte inconnu : aucune fonction de gouvernance n'est constatée")
    for function in GOVERNANCE_FUNCTIONS
}
_UNKNOWN_STATES[GovernanceFunction.MCP_SERVER] = _degraded("hôte inconnu : client MCP ni affirmé ni exclu")

_FUNCTION_STATES: dict[HostId, dict[GovernanceFunction, FunctionState]] = {
    HostId.CLAUDE_CODE_CLI: _CLAUDE_CODE_STATES,
    HostId.GITHUB_COPILOT: _COPILOT_STATES,
    HostId.CODEX: _CODEX_STATES,
    HostId.CURSOR: _CURSOR_STATES,
    HostId.GEMINI_CLI: _GEMINI_STATES,
    HostId.OPENCODE: _OPENCODE_STATES,
    HostId.UNKNOWN: _UNKNOWN_STATES,
}


def undeclared_functions(host_id: HostId) -> tuple[GovernanceFunction, ...]:
    """Fonctions de :class:`GovernanceFunction` sans état déclaré pour *host_id*."""
    declared = _FUNCTION_STATES.get(host_id, {})
    return tuple(f for f in GOVERNANCE_FUNCTIONS if f not in declared)


def function_states(host_id: HostId) -> dict[GovernanceFunction, FunctionState]:
    """États de toutes les fonctions pour *host_id* ; ``KeyError`` nommé si un état manque."""
    declared = _FUNCTION_STATES.get(host_id)
    if declared is None:
        msg = f"aucun état de gouvernance déclaré pour {host_id.value}"
        raise KeyError(msg)
    missing = [f.value for f in undeclared_functions(host_id)]
    if missing:
        msg = f"{host_id.value} ne déclare pas : {', '.join(missing)}"
        raise KeyError(msg)
    return declared


def function_entries(host_id: HostId) -> tuple[tuple[GovernanceFunction, FunctionState], ...]:
    """Les couples (fonction, état) dans l'ordre de :data:`GOVERNANCE_FUNCTIONS`."""
    states = function_states(host_id)
    return tuple((function, states[function]) for function in GOVERNANCE_FUNCTIONS)


def parity_line(host_id: HostId) -> str:
    """Une ligne : pour *host_id*, ce qui est dégradé et ce qui est absent."""
    profile = profile_for(host_id)
    degraded = [GOVERNANCE_LABELS[f] for f, s in function_entries(host_id) if s.level is ParityLevel.DEGRADED]
    absent = [GOVERNANCE_LABELS[f] for f, s in function_entries(host_id) if s.level is ParityLevel.ABSENT]
    if not degraded and not absent:
        return (
            f"{profile.display_name} — gouvernance complète : les {len(GOVERNANCE_FUNCTIONS)} fonctions "
            "sont disponibles."
        )
    parts: list[str] = []
    if degraded:
        parts.append(f"dégradé : {', '.join(degraded)}")
    if absent:
        parts.append(f"absent : {', '.join(absent)}")
    return f"{profile.display_name} — " + " ; ".join(parts) + "."


def parity_report(host_id: HostId) -> dict[str, Any]:
    """Forme sérialisable de la parité d'un hôte, pour ``grimoire up -o json``."""
    return {
        "host_id": host_id.value,
        "display_name": profile_for(host_id).display_name,
        "line": parity_line(host_id),
        "functions": [
            {"function": function.value, "label": GOVERNANCE_LABELS[function], **state.to_dict()}
            for function, state in function_entries(host_id)
        ],
    }


# ---------------------------------------------------------------------------
# Contrats réels (K0, #765) : ce qui a été observé sur l'hôte, pas ce qu'il
# documente. ``HostProfile`` ci-dessus décrit les surfaces que le kit sait
# écrire ; ce qui suit décrit ce que le kit a *vu* faire. Le fencing d'un bail
# (refuser les effets d'une session périmée) ne repose que sur le second.
#
# La table est jumelle des ``manifest.json`` de ``tests/fixtures/hosts/<hôte>/``
# (``tests/unit/test_host_contracts.py`` verrouille l'égalité). Règle unique :
# une capacité sans fixture vaut ``False``.
# ---------------------------------------------------------------------------

#: Les capacités tranchées par les neuf points de K0, dans l'ordre de la fiche.
CONTRACT_KEYS: tuple[str, ...] = (
    "pretooluse_deny",
    "session_end",
    "quota_signal",
    "liveness_probe",
    "subagent_model",
    "resume_after_quota",
    "bg_workspace_trust",
    "bg_cost_source",
    "bg_wake",
    "local_token_usage",
    "systemd_user_linger",
)


class ContractStatus(StrEnum):
    """Niveau de preuve d'une capacité sur une version d'hôte."""

    #: Observée sur l'hôte réel ; au moins une fixture la porte.
    VERIFIED = "verified"
    #: Jamais observée (ou observée seulement en partie). Vaut ``False``.
    UNVERIFIED = "unverified"
    #: Observée comme absente sur cette version d'hôte. Vaut ``False``.
    ABSENT = "absent"


@dataclass(frozen=True, slots=True)
class ContractEvidence:
    """Preuve d'une capacité : son statut, ses fixtures, le canal observé."""

    status: ContractStatus = ContractStatus.UNVERIFIED
    #: Chemins relatifs à ``tests/fixtures/hosts/``.
    fixtures: tuple[str, ...] = ()
    #: Par où l'information passe réellement (charge utile, journal, commande).
    channel: str = ""

    def __post_init__(self) -> None:
        if self.status is ContractStatus.VERIFIED and not self.fixtures:
            msg = "a verified capability needs at least one fixture"
            raise ValueError(msg)

    @property
    def verified(self) -> bool:
        return self.status is ContractStatus.VERIFIED


@dataclass(frozen=True, slots=True)
class HostContract:
    """Ce que le kit a observé d'un hôte, à une version donnée."""

    host_id: HostId
    #: Version de l'hôte sur laquelle les captures ont été faites ; vide si aucune.
    host_version: str = ""
    evidence: Mapping[str, ContractEvidence] = field(default_factory=dict)

    def verified(self, capability: str) -> bool:
        """``True`` seulement si la capacité est vérifiée ; inconnue vaut ``False``."""
        entry = self.evidence.get(capability)
        return entry is not None and entry.verified

    @property
    def fenced(self) -> bool:
        """Un refus au PreToolUse arrête réellement l'outil (observé)."""
        return self.verified("pretooluse_deny")


def _unverified_contract(host_id: HostId) -> HostContract:
    return HostContract(
        host_id=host_id,
        evidence={key: ContractEvidence() for key in CONTRACT_KEYS},
    )


_V = ContractStatus.VERIFIED

_CLAUDE_CODE_CONTRACT = HostContract(
    host_id=HostId.CLAUDE_CODE_CLI,
    host_version="2.1.301",
    evidence={
        "pretooluse_deny": ContractEvidence(
            _V,
            (
                "claude-code/01-pretooluse-control-allow.json",
                "claude-code/01-pretooluse-deny-exit2.json",
                "claude-code/01-pretooluse-deny-json.json",
            ),
            "exit code 2 + stderr, or exit 0 + hookSpecificOutput.permissionDecision=deny",
        ),
        "session_end": ContractEvidence(_V, ("claude-code/02-session-end.json",), "hook SessionEnd"),
        "quota_signal": ContractEvidence(fixtures=("claude-code/03-quota-signal.json",)),
        "liveness_probe": ContractEvidence(_V, ("claude-code/04-agents-json.json",), "claude agents --json"),
        "subagent_model": ContractEvidence(
            _V,
            ("claude-code/05-subagent-stop.json",),
            "agent_transcript_path (ligne assistant : message.model)",
        ),
        "resume_after_quota": ContractEvidence(fixtures=("claude-code/06-resume-after-quota.json",)),
        "bg_workspace_trust": ContractEvidence(_V, ("claude-code/07-bg-untrusted-workspace.json",), "claude --bg"),
        "bg_cost_source": ContractEvidence(fixtures=("claude-code/07-bg-untrusted-workspace.json",)),
        "bg_wake": ContractEvidence(fixtures=("claude-code/07-bg-untrusted-workspace.json",)),
        "local_token_usage": ContractEvidence(
            _V,
            ("claude-code/08-token-usage.json",),
            "flux stream-json (result.usage, modelUsage) et journal de session (assistant.message.usage)",
        ),
        "systemd_user_linger": ContractEvidence(_V, ("claude-code/09-systemd-linger.json",), "loginctl show-user"),
    },
)

_OPENCODE_CONTRACT = HostContract(
    host_id=HostId.OPENCODE,
    host_version="1.17.11",
    evidence={
        **{key: ContractEvidence() for key in CONTRACT_KEYS},
        "pretooluse_deny": ContractEvidence(
            _V,
            (
                "opencode/01-pretooluse-control-allow.json",
                "opencode/01-pretooluse-deny-throw.json",
            ),
            "plugin .opencode/plugin : le hook tool.execute.before lève une Error",
        ),
    },
)

_U = ContractStatus.UNVERIFIED

#: Copilot (P1-0, #1280) : traces réelles relues le 2026-11-20 — journal de session
#: du harness Copilot dans VS Code (2026-05..07) et sessions de chat du harness Local
#: (2026-04). Elles montrent des entrées de hooks et des journaux d'usage, mais
#: aucune sortie de hook acceptée ni aucun refus, et aucune version d'hôte
#: (``copilotVersion: "unknown"``). Les fixtures sont rattachées, rien n'est vérifié :
#: ``host_version`` reste vide, donc :func:`contract_for` ne retient jamais rien.
_COPILOT_CONTRACT = HostContract(
    host_id=HostId.GITHUB_COPILOT,
    host_version="",
    evidence={
        **{key: ContractEvidence() for key in CONTRACT_KEYS},
        "pretooluse_deny": ContractEvidence(
            _U,
            (
                "copilot/copilot-harness/pretooluse-single.json",
                "copilot/copilot-harness/pretooluse-batch.json",
            ),
            "journal de session (hook.start.data.input, preToolUse)",
        ),
        "subagent_model": ContractEvidence(
            _U,
            ("copilot/copilot-harness/subagent-model.json",),
            "journal de session (assistant.message.model, rattaché par parentToolCallId)",
        ),
        "local_token_usage": ContractEvidence(
            _U,
            ("copilot/copilot-harness/session-shutdown-usage.json",),
            "journal de session (session.shutdown.modelMetrics)",
        ),
    },
)

_CONTRACTS: dict[HostId, HostContract] = {
    HostId.CLAUDE_CODE_CLI: _CLAUDE_CODE_CONTRACT,
    # Codex, Cursor, Gemini : aucune capture (K0 du 2026-10-16, poste sans
    # opérateur). Tout est « non vérifié », donc faux, donc hôte non fencé.
    HostId.GITHUB_COPILOT: _COPILOT_CONTRACT,
    HostId.CODEX: _unverified_contract(HostId.CODEX),
    HostId.CURSOR: _unverified_contract(HostId.CURSOR),
    HostId.GEMINI_CLI: _unverified_contract(HostId.GEMINI_CLI),
    # opencode 1.17.11 : seul pretooluse_deny a été sondé (fournisseur local factice).
    HostId.OPENCODE: _OPENCODE_CONTRACT,
}


def recorded_contract(host_id: HostId) -> HostContract:
    """Contrat tel que capturé, à la version qu'il porte (``host_version``).

    À réserver à l'audit de la table et des fixtures : une décision (fencing,
    récolte) passe par :func:`contract_for`, qui exige la version de l'hôte.
    """
    return _CONTRACTS.get(host_id) or _unverified_contract(host_id)


def contract_for(host_id: HostId, host_version: str | None, *, project_root: Path | None = None) -> HostContract:
    """Contrat de *host_id* valable pour *host_version* ; rien de vérifié sinon.

    Une preuve vaut pour la version sur laquelle elle a été capturée, pas pour
    les autres. Version absente (``None``, vide), différente de la version
    capturée ou jamais capturée : contrat entièrement non vérifié.

    Aucune preuve n'est retenue (capturée ou sondée) quand la configuration effective de l'hôte désactive
    les hooks (#886) : la sonde teste un dossier temporaire, pas les réglages réels (voir
    :func:`~grimoire.hosts.contract_probe.hooks_disabled_reason`). *project_root* : le dossier du projet,
    par défaut le dossier courant (celui d'où l'hôte lance ses hooks).
    """
    recorded = recorded_contract(host_id)
    wanted = (host_version or "").strip()
    contract = (
        recorded
        if wanted and recorded.host_version and wanted == recorded.host_version
        else _probed_contract(host_id, wanted)
    )
    if contract is None or not contract.fenced:
        return contract or _unverified_contract(host_id)
    if host_id is HostId.CLAUDE_CODE_CLI:
        from grimoire.hosts.contract_probe import hooks_disabled_reason

        if hooks_disabled_reason(project_root):
            return _unverified_contract(host_id)
    return contract


def _probed_contract(host_id: HostId, version: str) -> HostContract | None:
    """Contrat issu d'une sonde réussie sur *version* exacte (#876) ; ``None`` sinon.

    Seul ``pretooluse_deny`` est établi par la sonde ; le reste demeure non vérifié.
    Un échec de sonde, ou une version jamais sondée, ne donne rien.
    """
    from grimoire.hosts.contract_probe import CAPABILITY, proof_for

    proof = proof_for(host_id, version) if version else None
    if proof is None or not proof.ok:
        return None
    evidence = {key: ContractEvidence() for key in CONTRACT_KEYS}
    evidence[CAPABILITY] = ContractEvidence(_V, (f"probe:{proof.key}",), f"sonde du {proof.probed_at}")
    return HostContract(host_id=host_id, host_version=version, evidence=evidence)


def is_fenced(host_id: HostId, host_version: str | None, *, project_root: Path | None = None) -> bool:
    """Un bail perdu peut-il être récolté sur cet hôte, à cette version ? Non, sauf preuve.

    La preuve est celle de la version capturée : toute autre version, ou une
    version inconnue (``None``), vaut ``False``.
    """
    return contract_for(host_id, host_version, project_root=project_root).fenced


def claim_host_is_fenced(host: str, host_version: str | None, *, project_root: Path | None = None) -> bool:
    """:func:`is_fenced` pour l'hôte tel qu'un claim le nomme (K3, issue #768).

    *host* est une graphie du ledger (``host-claude-code-cli``, ``claude-code-cli``,
    ``local``, ``mcp``…). Un nom que :func:`resolve_host` ne connaît pas n'est pas
    un hôte prouvé : ``False``, comme une version absente.
    """
    host_id = resolve_host(host)
    return host_id is not None and is_fenced(host_id, host_version, project_root=project_root)


#: Clé de contrat (:data:`CONTRACT_KEYS`) dont la preuve vérifiée établit qu'un refus
#: arrête réellement l'action sur cet événement. Un événement absent d'ici ne peut pas
#: être prouvé bloquant : aucune capture de refus de fin de tour (`decision: block`
#: sur Stop) n'est versée sous ``tests/fixtures/hosts/``, donc Stop reste documenté.
PROVING_KEYS: dict[HookEvent, str] = {HookEvent.PRE_TOOL_USE: "pretooluse_deny"}


#: Champ de décision à plat que chaque dialecte lit (#1281, L7) ; ``hookSpecificOutput.permissionDecision``
#: (Claude Code, Codex, Copilot) est lu pour tout hôte. Un hôte absent d'ici n'a aucun champ à plat.
FLAT_DECISION_FIELDS: dict[str, str] = {
    "cursor": "permission",
    "copilot": "permissionDecision",
    "gemini": "decision",
    "antigravity": "decision",
}
_DECISION_FIELDS = ("permission", "permissionDecision", "decision")


def capture_shows_refusal(capture: Mapping[str, Any]) -> bool:
    """La capture montre-t-elle un **refus** honoré par l'hôte, pas une autorisation ?

    Il faut que l'action ne se soit pas exécutée (``observed.tool_executed`` faux) et
    qu'un verdict de refus soit lu : code 2, ``permissionDecision: deny`` sur stdout,
    ou exception levée par le plugin (opencode). Une capture témoin (allow) ne prouve rien.

    Le champ à plat n'est lu que dans le dialecte de l'hôte de la capture
    (:data:`FLAT_DECISION_FIELDS`), et un seul champ de décision qui vaut autre chose que
    ``deny`` invalide la capture : une sortie contradictoire ne prouve rien. Quand la capture
    dit si le hook a été appelé (``hook_called``, ``hook_calls``), il faut qu'il l'ait été.
    """
    observed = capture.get("observed")
    if not isinstance(observed, Mapping) or observed.get("tool_executed") is not False:
        return False
    if "hook_called" in observed and observed.get("hook_called") is not True:
        return False
    if "hook_calls" in observed and not (isinstance(observed["hook_calls"], int) and observed["hook_calls"] > 0):
        return False
    stdout = observed.get("hook_stdout")
    decisions: list[Any] = []
    if isinstance(stdout, Mapping):
        specific = stdout.get("hookSpecificOutput")
        if isinstance(specific, Mapping) and "permissionDecision" in specific:
            decisions.append(specific["permissionDecision"])
        decisions += [stdout[name] for name in _DECISION_FIELDS if name in stdout]
    if any(value != "deny" for value in decisions):
        return False
    denied = False
    if isinstance(stdout, Mapping):
        specific = stdout.get("hookSpecificOutput")
        denied = isinstance(specific, Mapping) and specific.get("permissionDecision") == "deny"
        flat = FLAT_DECISION_FIELDS.get(str(capture.get("host", "")))
        denied = denied or (flat is not None and stdout.get(flat) == "deny")
    return observed.get("hook_exit_code") == 2 or denied or bool(observed.get("hook_throws"))


def _read_capture(path: Path) -> Mapping[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, Mapping) else {}


def unproven_blocking_claims(profile: HostProfile, fixtures_root: Path) -> tuple[str, ...]:
    """Ce qui manque pour que les refus déclarés de *profile* soient prouvés (#1281).

    Chaque événement tenu pour bloquant (:meth:`HostProfile.proven_blocking_events`)
    doit être rattaché à une clé de contrat (:data:`PROVING_KEYS`) vérifiée, et
    :attr:`HostProfile.blocking_captures` doit nommer au moins une fixture de cette
    preuve, présente sous *fixtures_root* (``tests/fixtures/hosts/``), dont le verdict
    lu est un refus (:func:`capture_shows_refusal`). Vide : rien d'orphelin.
    """
    host = profile.host_id.value
    proven = profile.proven_blocking_events()
    if not proven:
        return ()
    if not profile.blocking_captures:
        events = ", ".join(e.value for e in proven)
        return (f"{host} : refus tenu pour prouvé ({events}) sans aucune capture référencée",)
    contract = recorded_contract(profile.host_id)
    problems: list[str] = []
    proving: set[str] = set()
    for event in proven:
        key = PROVING_KEYS.get(event)
        evidence = contract.evidence.get(key) if key else None
        if key is None or evidence is None or not evidence.verified:
            problems.append(f"{host} : {event.value} tenu pour bloquant sans clé de contrat vérifiée qui le prouve")
            continue
        refusals = [
            c
            for c in profile.blocking_captures
            if c in evidence.fixtures and capture_shows_refusal(_read_capture(fixtures_root / c))
        ]
        if not refusals:
            problems.append(f"{host} : aucune capture de {key} ne montre un refus honoré ({event.value})")
        proving.update(evidence.fixtures)
    for capture in profile.blocking_captures:
        if not (fixtures_root / capture).is_file():
            problems.append(f"{host} : capture introuvable {capture}")
        elif capture not in proving:
            problems.append(f"{host} : {capture} n'est pas une preuve vérifiée d'un événement bloquant")
    return tuple(problems)
