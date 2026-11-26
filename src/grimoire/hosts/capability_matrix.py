"""Matrice de capacités d'un hôte, système par système et surface par surface (épique #1280, lot C4).

Rend visible ce qui est appliqué, ce qui est seulement émis, ce qui est dégradé et ce qui n'est pas pris en
charge — sans rien promouvoir. **Aucune cellule n'est écrite à la main** : chacune est dérivée du code qui
décide déjà :

* hooks (avant outil, après outil, session, sous-agents, compaction, clôture) :
  :class:`~grimoire.hosts.capabilities.HostProfile` (``supports_event``, ``blocks``,
  ``documented_blocking_events``, ``blocking_captures``) et le contrat enregistré
  (:func:`~grimoire.hosts.capabilities.recorded_contract`, ``pretooluse_deny``) ;
* rattachement de session : ``RESUME_COMMANDS`` / ``LOGS_COMMANDS`` de :mod:`grimoire.missions.session_link` ;
* mémoire, outils MCP, skills et agents : :func:`~grimoire.hosts.capabilities.function_states` et le profil,
  plus les dégradations que l'émetteur déclare dans son plan ;
* dispatch et modèle des sous-agents : dégradation ``dispatch model`` de l'émetteur et contrat
  ``subagent_model`` ;
* orchestrateur autonome par rôle : ``WORKER_HOSTS`` et
  :func:`~grimoire.missions.orchestrator_policy.claude_only_refusal` ;
* chat du cockpit : ``acp_command`` / ``acp_preview`` / ``acp_note`` du profil et le repli kanban
  (:mod:`grimoire.hosts.acp_hosts`) ;
* flotte et jetons : ``USAGE_SOURCE_BY_HOST`` (lecteurs de journal de session) et le contrat
  ``local_token_usage`` / ``liveness_probe``.

Quatre états (:class:`CellState`) : ``proven`` (appliqué **et** prouvé par une capture versée sous
``tests/fixtures/hosts/``), ``unverified`` (émis ou lu, jamais observé), ``degraded`` (existe sous forme
réduite, avec son repli), ``unsupported`` (absent, avec la raison). Un état ``proven`` exige ses captures ;
un événement déclaré bloquant sans capture n'est jamais rendu ``proven`` et remplit ``problems`` (un test
fait échouer la CI dessus).

Surfaces : seul Copilot en a plusieurs (VS Code, CLI, agent cloud). Les captures du dépôt viennent d'une
seule surface (:data:`CAPTURE_SURFACE`, tirée du manifeste) : sur une autre surface, rien n'est ``proven``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from grimoire.bridges.schemas import HostId
from grimoire.hosts.capabilities import (
    GovernanceFunction,
    HostProfile,
    ParityLevel,
    function_states,
    gaps_for,
    profile_for,
    recorded_contract,
    unproven_blocking_claims,
)
from grimoire.hosts.emitters.base import Degradation
from grimoire.hosts.events import HookEvent

__all__ = [
    "CAPTURE_SURFACE",
    "SYSTEMS",
    "CapabilityMatrix",
    "Cell",
    "CellState",
    "Surface",
    "build_capability_matrix",
    "render_markdown",
    "session_logs_status",
    "surfaces_for",
]


class CellState(StrEnum):
    """Niveau de preuve et de disponibilité d'une capacité sur une surface."""

    PROVEN = "proven"
    UNVERIFIED = "unverified"
    DEGRADED = "degraded"
    UNSUPPORTED = "unsupported"


#: Libellés français des états.
STATE_LABELS: dict[CellState, str] = {
    CellState.PROVEN: "appliqué et prouvé par capture",
    CellState.UNVERIFIED: "émis, non vérifié",
    CellState.DEGRADED: "dégradé",
    CellState.UNSUPPORTED: "non pris en charge",
}


@dataclass(frozen=True, slots=True)
class Cell:
    """L'état d'un système sur une surface, avec ce qui le justifie."""

    state: CellState
    detail: str
    #: Le repli, obligatoire quand l'état est ``degraded``.
    fallback: str = ""
    #: Captures (chemins relatifs à ``tests/fixtures/hosts/``) qui prouvent la cellule ; obligatoires si ``proven``.
    captures: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.state is CellState.PROVEN and not self.captures:
            raise ValueError("une cellule prouvée cite ses captures")
        if self.state is CellState.DEGRADED and not self.fallback.strip():
            raise ValueError("une cellule dégradée dit son repli")
        if self.state in (CellState.DEGRADED, CellState.UNSUPPORTED) and not self.detail.strip():
            raise ValueError("une cellule dégradée ou non prise en charge dit pourquoi")

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "label": STATE_LABELS[self.state],
            "detail": self.detail,
            "fallback": self.fallback,
            "captures": list(self.captures),
        }


@dataclass(frozen=True, slots=True)
class Surface:
    """Une surface d'un hôte : ``kind`` = ``host`` (hôte à surface unique), ``ide``, ``cli`` ou ``cloud``."""

    id: str
    label: str
    kind: str

    @property
    def remote(self) -> bool:
        return self.kind == "cloud"


_SINGLE = Surface("host", "hôte", "host")

#: Surfaces par hôte. Seul Copilot en a plusieurs : sa configuration MCP aussi (``copilot_mcp``,
#: référence ``docs/reference/hotes.md`` « MCP sous Copilot »), ses captures viennent de VS Code seulement.
_SURFACES: dict[HostId, tuple[Surface, ...]] = {
    HostId.GITHUB_COPILOT: (
        Surface("vscode", "VS Code Copilot Chat", "ide"),
        Surface("cli", "Copilot CLI", "cli"),
        Surface("cloud", "Agent cloud", "cloud"),
    ),
}

#: Surface d'où viennent les captures versées (manifeste ``copilot/manifest.json`` : « journal de session de
#: Copilot dans VS Code »). Un hôte absent d'ici : ses captures valent pour son unique surface.
CAPTURE_SURFACE: dict[HostId, str] = {HostId.GITHUB_COPILOT: "vscode"}


def surfaces_for(host_id: HostId) -> tuple[Surface, ...]:
    return _SURFACES.get(host_id, (_SINGLE,))


@dataclass(frozen=True, slots=True)
class _Ctx:
    host_id: HostId
    profile: HostProfile
    degradations: tuple[Degradation, ...]
    states: Mapping[GovernanceFunction, Any]

    def degradation(self, surface: Surface, *prefixes: str) -> Degradation | None:
        """La dégradation déclarée par l'émetteur dont le nom commence par un préfixe.

        Une dégradation qui nomme le cloud ne vaut que sur la surface distante (et y passe en premier) ; les
        autres valent sur toutes les surfaces.
        """
        matching = [item for item in self.degradations if item.surface.startswith(prefixes)]
        matching.sort(key=lambda item: "cloud" not in item.surface)
        for item in matching:
            if "cloud" not in item.surface or surface.remote:
                return item
        return None


# ── systèmes ────────────────────────────────────────────────────────────────


_REMOTE = "surface distante : le kit n'y lit ni n'y lance rien en local"


def _hooks(ctx: _Ctx, surface: Surface, events: tuple[HookEvent, ...], *, limits: bool = False) -> Cell:
    profile = ctx.profile
    supported = [e for e in events if profile.supports_event(e)]
    names = ", ".join(e.value for e in events)
    if not supported:
        gap = next((g for g in gaps_for(profile) if g.surface == "blocking_hooks"), None)
        suffix = f" ; repli : {gap.fallback}" if gap else ""
        return Cell(CellState.UNSUPPORTED, f"l'hôte ne déclenche pas {names}{suffix}")
    missing = [e.value for e in events if e not in supported]
    note = f" ; non déclenché : {', '.join(missing)}" if missing else ""
    if limits:
        declared = [d.surface for d in ctx.degradations if d.surface.startswith("hook") and ("cloud" not in d.surface or surface.remote)]
        if declared:
            note += f" ; limites déclarées : {', '.join(declared)}"
    blocking = [e for e in supported if profile.blocks(e)]
    if blocking:
        if profile.blocking_captures:
            return Cell(
                CellState.PROVEN,
                f"refus honoré sur {', '.join(e.value for e in blocking)}{note}",
                captures=profile.blocking_captures,
            )
        return Cell(CellState.UNVERIFIED, f"DÉCLARÉ BLOQUANT SANS CAPTURE ({', '.join(e.value for e in blocking)}){note}")
    documented = [e for e in supported if profile.documents_unverified_block(e)]
    if documented:
        return Cell(
            CellState.UNVERIFIED,
            f"refus documenté par l'hôte sur {', '.join(e.value for e in documented)}, garde écrite, aucune capture "
            f"(consultatif){note}",
        )
    return Cell(CellState.UNVERIFIED, f"émis, aucun refus déclaré pour cet événement{note}")


def _session_link(ctx: _Ctx, surface: Surface) -> Cell:
    from grimoire.missions.session_link import LOGS_COMMANDS, RESUME_COMMANDS

    if surface.remote:
        return Cell(CellState.UNSUPPORTED, _REMOTE)
    host = ctx.host_id.value
    if host in RESUME_COMMANDS and host in LOGS_COMMANDS:
        return Cell(CellState.UNVERIFIED, "identifiant rattaché au claim ; commandes de reprise et de journal connues")
    return Cell(
        CellState.DEGRADED,
        "identifiant de session rattaché au claim, mais aucune commande de reprise ni de journal connue",
        fallback="la carte de tâche montre l'identifiant seul, jamais une commande inventée",
    )


def _memory(ctx: _Ctx, surface: Surface) -> Cell:
    recall = ctx.states[GovernanceFunction.TASK_RECALL]
    mcp = ctx.states[GovernanceFunction.MCP_SERVER]
    if recall.level is ParityLevel.RICH and mcp.level is ParityLevel.RICH:
        return Cell(CellState.UNVERIFIED, "rappel de tâche au début de session et outils mémoire par le serveur MCP du kit")
    reasons = [s.reason for s in (recall, mcp) if s.level is not ParityLevel.RICH]
    return Cell(CellState.DEGRADED, " ; ".join(reasons), fallback="mémoire atteinte par la CLI `grimoire`, sans rappel automatique")


def _mcp(ctx: _Ctx, surface: Surface) -> Cell:
    declared = ctx.degradation(surface, "mcp")
    if declared is not None:
        return Cell(CellState.DEGRADED, declared.reason, fallback=declared.fallback)
    state = ctx.states[GovernanceFunction.MCP_SERVER]
    if state.level is ParityLevel.RICH:
        return Cell(CellState.UNVERIFIED, "configuration MCP du kit émise ; aucun appel d'outil capturé sur l'hôte")
    return Cell(
        CellState.DEGRADED,
        state.reason,
        fallback="outils du kit atteints par la CLI `grimoire`, sans client MCP",
    )


def _skills_agents(ctx: _Ctx, surface: Surface) -> Cell:
    profile = ctx.profile
    missing = [name for name, ok in (("agents", profile.subagents_native), ("skills", profile.skills_native)) if not ok]
    if not missing:
        return Cell(CellState.UNVERIFIED, "fichiers d'agents et de skills émis, chargés par l'hôte d'après sa documentation")
    gap_text = "; ".join(g.fallback for g in gaps_for(profile) if g.surface in {"subagents", "skills"})
    return Cell(CellState.DEGRADED, f"pas de chargement natif : {', '.join(missing)}", fallback=gap_text or "décrits dans l'entrypoint")


def _dispatch_model(ctx: _Ctx, surface: Surface) -> Cell:
    contract = recorded_contract(ctx.host_id)
    evidence = contract.evidence.get("subagent_model")
    if not ctx.profile.subagents_native:
        return Cell(CellState.UNSUPPORTED, "l'hôte ne charge pas de sous-agents natifs")
    if evidence is not None and evidence.verified:
        return Cell(CellState.PROVEN, f"modèle du sous-agent lu par {evidence.channel}", captures=evidence.fixtures)
    declared = ctx.degradation(surface, "dispatch model")
    seen = f" ; vu en partie dans {evidence.channel}" if evidence is not None and evidence.fixtures else ""
    if declared is not None:
        return Cell(CellState.DEGRADED, declared.reason + seen, fallback=declared.fallback)
    return Cell(CellState.UNVERIFIED, f"aucune capture du modèle du sous-agent{seen}")


def _policy_alias(host_id: HostId) -> str:
    from grimoire.hosts.detection import alias_for_host
    from grimoire.missions.worker_host import CLAUDE_CODE_HOST, GEMINI_HOST, OPENCODE_HOST

    known = {HostId.CLAUDE_CODE_CLI: CLAUDE_CODE_HOST, HostId.OPENCODE: OPENCODE_HOST, HostId.GEMINI_CLI: GEMINI_HOST}
    return known.get(host_id) or alias_for_host(host_id)


def _bg_evidence(ctx: _Ctx) -> Cell | None:
    evidence = recorded_contract(ctx.host_id).evidence.get("bg_workspace_trust")
    if evidence is not None and evidence.verified:
        return Cell(CellState.PROVEN, f"session de fond lancée par le kit ({evidence.channel})", captures=evidence.fixtures)
    return None


def _orchestrator_role(ctx: _Ctx, surface: Surface, role: str) -> Cell:
    from grimoire.missions.orchestrator_policy import OrchestratorPolicy, claude_only_refusal
    from grimoire.missions.worker_host import WORKER_HOSTS

    if surface.remote:
        return Cell(CellState.UNSUPPORTED, _REMOTE)
    alias = _policy_alias(ctx.host_id)
    if role == "worker":
        if alias not in WORKER_HOSTS:
            return Cell(CellState.UNSUPPORTED, f"{alias!r} n'est pas un hôte de worker (admis : {', '.join(WORKER_HOSTS)})")
    else:
        refusal = claude_only_refusal(OrchestratorPolicy(host=alias), role)
        if refusal:
            return Cell(CellState.UNSUPPORTED, refusal)
    return _bg_evidence(ctx) or Cell(CellState.UNVERIFIED, f"rôle lancé par le kit sur {alias}, aucune capture de session de fond")


def _cockpit_chat(ctx: _Ctx, surface: Surface) -> Cell:
    profile = ctx.profile
    if surface.remote:
        return Cell(CellState.UNSUPPORTED, "aucune interface locale à piloter : la session tourne chez GitHub")
    use_acp = bool(profile.acp_command) and surface.kind in ("cli", "host")
    if use_acp:
        command = " ".join(profile.acp_command)
        if profile.acp_preview:
            return Cell(
                CellState.DEGRADED,
                f"mode ACP `{command}` en preview ; {profile.acp_note}".rstrip(" ;"),
                fallback="chat écrit dans le kanban si l'agent ACP est indisponible",
            )
        return Cell(CellState.UNVERIFIED, f"mode ACP `{command}` ; aucune capture d'une session ACP")
    reason = (
        "aucune interface externe pour cette surface"
        if profile.acp_command
        else f"{profile.display_name} n'a pas de mode ACP connu"
    )
    return Cell(CellState.DEGRADED, reason, fallback="le chat écrit une consigne dans le kanban ; les sessions y répondent par leurs notes")


def _journal_reader(ctx: _Ctx, surface: Surface, what: str) -> Cell:
    from grimoire.traces.transcript_usage import USAGE_SOURCE_BY_HOST

    if surface.remote:
        return Cell(CellState.UNSUPPORTED, _REMOTE)
    source = USAGE_SOURCE_BY_HOST.get(ctx.host_id.value)
    if source is None:
        return Cell(CellState.UNSUPPORTED, f"aucun lecteur de journal de session pour {ctx.profile.display_name} : {what} inconnus")
    contract = recorded_contract(ctx.host_id)
    evidence = contract.evidence.get("local_token_usage")
    if evidence is not None and evidence.verified:
        return Cell(CellState.PROVEN, f"{what} lus dans {evidence.channel}", captures=evidence.fixtures)
    channel = f" ({evidence.channel})" if evidence is not None and evidence.channel else ""
    where = ""
    if ctx.host_id is HostId.GITHUB_COPILOT:
        from grimoire.traces.copilot_usage import LOGS_ENV

        where = f" ; emplacement des journaux à configurer ({LOGS_ENV}), aucun défaut"
    return Cell(
        CellState.UNVERIFIED,
        f"lecteur du journal de session implémenté (source {source}){channel} ; format lu dans des fixtures, "
        f"aucune capture réelle{where}",
    )


@dataclass(frozen=True, slots=True)
class _System:
    id: str
    label: str
    cell: Callable[[_Ctx, Surface], Cell]


def _event_system(system_id: str, label: str, events: tuple[HookEvent, ...], *, limits: bool = False) -> _System:
    return _System(system_id, label, lambda ctx, surface: _hooks(ctx, surface, events, limits=limits))


def _role_system(system_id: str, label: str, role: str) -> _System:
    return _System(system_id, label, lambda ctx, surface: _orchestrator_role(ctx, surface, role))


_SYSTEMS: tuple[_System, ...] = (
    _event_system("pre_tool_hook", "Hooks avant outil", (HookEvent.PRE_TOOL_USE,), limits=True),
    _event_system("post_tool_hook", "Hooks après outil", (HookEvent.POST_TOOL_USE,)),
    _event_system("session_hook", "Hooks de session", (HookEvent.SESSION_START, HookEvent.USER_PROMPT_SUBMIT)),
    _event_system("subagent_hook", "Hooks de sous-agents", (HookEvent.SUBAGENT_START, HookEvent.SUBAGENT_STOP)),
    _event_system("compaction_hook", "Hook de compaction", (HookEvent.PRE_COMPACT,)),
    _event_system("closure_hook", "Hook de clôture", (HookEvent.STOP,)),
    _System("session_link", "Rattachement de session", _session_link),
    _System("memory", "Mémoire", _memory),
    _System("mcp", "Outils MCP", _mcp),
    _System("skills_agents", "Skills et agents", _skills_agents),
    _System("dispatch_model", "Dispatch et modèle des sous-agents", _dispatch_model),
    _role_system("orchestrator_worker", "Orchestrateur autonome : worker", "worker"),
    _role_system("orchestrator_reviewer", "Orchestrateur autonome : relecteur", "relecteur"),
    _role_system("orchestrator_arbiter", "Orchestrateur autonome : arbitre", "arbitre"),
    _role_system("orchestrator_session", "Orchestrateur autonome : session orchestrateur", "orchestrateur"),
    _System("cockpit_chat", "Chat du cockpit", _cockpit_chat),
    _System("fleet", "Flotte en direct", lambda ctx, surface: _journal_reader(ctx, surface, "sessions, modèle et compteurs")),
    _System("tokens", "Jetons", lambda ctx, surface: _journal_reader(ctx, surface, "jetons")),
)

#: Identifiants des systèmes, dans l'ordre d'affichage.
SYSTEMS: tuple[str, ...] = tuple(s.id for s in _SYSTEMS)


def _scoped(ctx: _Ctx, surface: Surface, cell: Cell) -> Cell:
    """Sur une surface autre que celle des captures, rien n'est prouvé : ``proven`` retombe à ``unverified``."""
    captured_on = CAPTURE_SURFACE.get(ctx.host_id)
    if captured_on is None or surface.id == captured_on or cell.state not in (CellState.PROVEN, CellState.UNVERIFIED):
        return cell
    return Cell(
        CellState.UNVERIFIED,
        f"{cell.detail} ; captures faites sur {captured_on} seulement, aucune sur {surface.id}",
    )


@dataclass(frozen=True, slots=True)
class CapabilityMatrix:
    """La matrice d'un hôte : surfaces, systèmes, et ce qui contredit ses propres déclarations."""

    host_id: HostId
    display_name: str
    surfaces: tuple[Surface, ...]
    #: ``{système: {surface: cellule}}``, dans l'ordre de :data:`SYSTEMS`.
    cells: dict[str, dict[str, Cell]]
    labels: dict[str, str]
    #: Déclarations bloquantes sans capture, ou tout autre écart entre profil et captures. Vide : cohérent.
    problems: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.problems

    def cell(self, system: str, surface: str) -> Cell:
        return self.cells[system][surface]

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host_id.value,
            "display_name": self.display_name,
            "surfaces": [{"id": s.id, "label": s.label, "kind": s.kind} for s in self.surfaces],
            "states": {state.value: label for state, label in STATE_LABELS.items()},
            "systems": [
                {
                    "id": system,
                    "label": self.labels[system],
                    "cells": {surface: cell.to_dict() for surface, cell in self.cells[system].items()},
                }
                for system in self.cells
            ],
            "problems": list(self.problems),
        }


def build_capability_matrix(
    host_id: HostId,
    *,
    degradations: Iterable[Degradation] = (),
    fixtures_root: Path | None = None,
) -> CapabilityMatrix:
    """La matrice de *host_id*, dérivée du profil, du contrat enregistré et des dégradations de l'émetteur.

    *degradations* : celles du plan de l'émetteur (``Emitter.plan(...).degradations``) ; sans elles, les cellules
    qui en dépendent (modèle de dispatch, MCP de l'agent cloud) se rabattent sur ``unverified``. *fixtures_root*
    (``tests/fixtures/hosts/``, présent dans un clone du kit) : active le contrôle des captures de refus
    (:func:`~grimoire.hosts.capabilities.unproven_blocking_claims`).
    """
    profile = profile_for(host_id)
    ctx = _Ctx(host_id, profile, tuple(degradations), function_states(host_id))
    surfaces = surfaces_for(host_id)
    cells = {
        system.id: {surface.id: _scoped(ctx, surface, system.cell(ctx, surface)) for surface in surfaces}
        for system in _SYSTEMS
    }
    problems = [
        f"{system} ({surface}) : {cell.detail}"
        for system, row in cells.items()
        for surface, cell in row.items()
        if cell.detail.startswith("DÉCLARÉ BLOQUANT SANS CAPTURE")
    ]
    if fixtures_root is not None:
        problems += unproven_blocking_claims(profile, fixtures_root)
    return CapabilityMatrix(
        host_id=host_id,
        display_name=profile.display_name,
        surfaces=surfaces,
        cells=cells,
        labels={s.id: s.label for s in _SYSTEMS},
        problems=tuple(problems),
    )


def session_logs_status(project_root: Path, host_id: HostId) -> dict[str, Any]:
    """Où lire les journaux de session de l'hôte (Copilot seulement : « emplacement non configuré » sinon) ; vide ailleurs.

    Lecture seule : rien n'est ouvert, seul le réglage est lu.
    """
    if host_id is not HostId.GITHUB_COPILOT:
        return {}
    from grimoire.traces.copilot_usage import copilot_logs_location

    return copilot_logs_location(project_root).to_dict()


def render_markdown(matrix: CapabilityMatrix) -> str:
    """La matrice en table Markdown (états seuls, un détail par cellule reste dans ``grimoire host status``)."""
    header = "| Système | " + " | ".join(s.label for s in matrix.surfaces) + " |"
    rule = "|---|" + "---|" * len(matrix.surfaces)
    lines = [header, rule]
    for system, row in matrix.cells.items():
        states = " | ".join(STATE_LABELS[row[s.id].state] for s in matrix.surfaces)
        lines.append(f"| {matrix.labels[system]} | {states} |")
    return "\n".join(lines)
