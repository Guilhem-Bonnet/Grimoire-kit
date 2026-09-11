"""``SessionStart``: hand the agent its persona, its claim's recall, then the directive."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from grimoire.core.claude_activation import activation_context_text
from grimoire.core.standard_state import active_task_id
from grimoire.hosts.decisions._shared import Decision, HookInput, Outcome
from grimoire.hosts.decisions.record import _record_agent_dispatch


def entry_persona_context(project_root: Path) -> tuple[str, str]:
    """The session-start stand-in for an agent no host can be told to open.

    ``collect_agents`` has always marked one persona ``entry_point``, and
    ``ProjectSurface.entry_agent`` has always known how to find it — but until
    this call site existed, the designation only changed a sentence *inside*
    the sub-agent file, which nothing reads until someone has already chosen to
    route there. A point of entry nobody enters is a label.

    So the persona is handed to the main loop instead of being launched into.
    That is not a smaller version of autostart: the session keeps the host's
    full tool surface and can still dispatch sub-agents. It is the one thing
    every host with a ``session_start`` hook makes possible.

    Returns ``(text, name)``; both empty when the project designates no entry.
    """
    from grimoire.hosts.collect import collect_agents
    from grimoire.hosts.surface import ProjectSurface

    try:
        agents = collect_agents(project_root)
    except OSError:
        return "", ""
    entry = ProjectSurface(project_name=project_root.name, agents=agents).entry_agent()
    if entry is None:
        return "", ""
    tools = ", ".join(v.value for v in entry.tools)
    text = f"""[Grimoire — persona d'entrée]
Aucun hôte ne sait ouvrir une session à l'intérieur d'un agent. Cette session
adopte donc la persona d'entrée du projet dans sa boucle principale, sans
sous-agent : **{entry.name}** — {entry.description}

1. Lis `{entry.definition_ref}` en entier avant de répondre : ce fichier porte
   la persona, ses règles et son protocole d'activation. Applique-les.
2. Tiens sa frontière d'outils pour ce que tu fais toi-même : {tools}.
3. Tu restes la boucle principale : dispatcher un sous-agent reste un choix que
   tu justifies, jamais un passage obligé.
"""
    return text, entry.name


def _claimed_task_recall(project_root: Path, task_id: str) -> str:
    """The claim's recall — only for a task someone has actually claimed.

    A task resolved from the board or a bare ``GRIMOIRE_TASK_ID`` override may
    not carry a claim at all (``proposed``, ``ready``, or an operator naming a
    task nobody has taken yet); recalling siblings for those would be reading
    material for a task that has not started, not the reminder a claim earns.
    ``task.claim`` is set once by :meth:`TaskService.claim` and survives every
    later transition, so its presence is the one honest signal here.
    """
    try:
        from grimoire.missions.service import TaskService

        service = TaskService(project_root)
        if not service.has_ledger:
            return ""
        task = service.ledger.get_task(task_id)
        if task is None or task.claim is None:
            return ""
        return service.recall(task_id).text
    except Exception:
        return ""


def _providers_status_line(project_root: Path) -> str:
    """One-line summary of LLM provider availability, for session start (issue #329).

    Dispatching a sub-agent at the right cost tier (see the Claude Code
    "Politique de dispatch") only works if the orchestrator knows which
    provider is actually reachable *right now* — the registry says what a
    project is entitled to call, but a cooldown from a recent 429 can take
    the cheapest one out of rotation for the next few minutes. Nothing is
    added when the project has never run ``grimoire standard init``: a
    routing surface that does not exist cannot be summarised, and a blank
    line here would look like a probe that ran and found nothing rather than
    a surface that was never adopted.

    Best-effort like every other piece of this context: a registry that fails
    to parse degrades to no line at all, never to a crashed hook.
    """
    try:
        from grimoire.providers.registry import REGISTRY_FILE, read_registry
        from grimoire.providers.routing import choose
        from grimoire.providers.state import load_state

        if not (project_root / REGISTRY_FILE).is_file():
            return ""
        providers = read_registry(project_root)
        now = datetime.now(UTC)
        state = load_state(project_root)
        cooling = 0
        available = 0
        for provider in providers:
            entry = state.get(provider.id)
            is_cooling = entry is not None and entry.is_cooling_down(now=now)
            if is_cooling:
                cooling += 1
            elif provider.enabled:
                available += 1
        cheapest = choose(project_root, "cheap", now=now)
        cheap_id = cheapest.id if cheapest is not None else "aucun"
        return f"Fournisseurs : {available} disponibles, {cooling} refroidis, prochain cheap={cheap_id}"
    except Exception:
        return ""


def _proposals_status_line(project_root: Path) -> str:
    """One-line pointer to pending artifact proposals, for session start (issue #395).

    Pure disk read — :func:`grimoire.proposals.count_pending` never touches
    the trace ledger, so a session start never depends on it being writable,
    same guarantee as every other piece of this context. Silent at zero: an
    empty line every session for a project with nothing pending would be
    noise, not a signal, and a project that never ran the déclencheur (no
    ``_grimoire-output/proposals/`` at all) must read exactly like one that
    ran it and found nothing — never like a probe that failed.
    """
    try:
        from grimoire.proposals import count_pending

        pending = count_pending(project_root)
        if not pending:
            return ""
        return f"{pending} proposition(s) d'artefact en attente, voir le cockpit ou `grimoire proposals`"
    except Exception:
        return ""


def decide_activation(hook: HookInput) -> Decision:
    """Session start: hand the agent its persona, its claim's recall, then the directive.

    The directive was validated 40/40 by the 2026-07-09 campaign against 0/40
    without it — an unread standard is an inert standard. It stays last on
    purpose: it is the part measured, and the part closest to the user's first
    message. The persona goes first because identity frames the protocol, not
    the reverse; the recall sits between the two — it is about the work, not
    the identity, but it belongs before the standing directive all the same.

    The providers line (issue #329) comes last of all: it is operational
    status, not identity or protocol, and it is the one part of this context
    that can legitimately be empty (no registry) without that being a defect.
    The proposals line (issue #395) sits right next to it — same register,
    operational rather than protocol — and is silent just as often (no
    proposal ever crossed the repetition threshold).
    """
    task_id = active_task_id(hook.project_root)
    directive = activation_context_text(hook.project_root, task_id=task_id)
    persona, entry_name = entry_persona_context(hook.project_root)
    if entry_name:
        _record_agent_dispatch(hook.project_root, entry_name, task_id)
    recall = _claimed_task_recall(hook.project_root, task_id)
    providers_line = _providers_status_line(hook.project_root)
    proposals_line = _proposals_status_line(hook.project_root)
    context = "\n".join(
        part for part in (persona, recall, directive, providers_line, proposals_line) if part
    )
    return Decision(
        outcome=Outcome.ALLOW,
        context=context,
        detail={"task_id": task_id, "entry_agent": entry_name, "recall_injected": bool(recall)},
    )
