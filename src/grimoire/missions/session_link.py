"""Ce qu'une carte de tâche dit de la session qui la porte (issue #638, lot A).

Une seule projection pour les deux surfaces qui rendent une carte — l'espace
Exécuter du cockpit (:func:`grimoire.tools.workspace_api.tasks_view`) et
l'outil MCP ``task_show`` — pour qu'un humain et un agent lisent la même
chose. La commande de reprise n'existe que pour un hôte dont on la connaît
(Claude Code : ``claude --resume <session_id>``) ; pour tout autre hôte, la
carte montre l'identifiant seul plutôt qu'une commande inventée.
"""

from __future__ import annotations

from typing import Any

from grimoire.bridges.schemas import HostId

#: Hôte → gabarit de commande de reprise, ``{session_id}`` inclus. Un hôte
#: absent d'ici n'a pas de commande connue : la carte n'en affiche aucune.
RESUME_COMMANDS: dict[str, str] = {
    HostId.CLAUDE_CODE_CLI.value: "claude --resume {session_id}",
}

#: Hôte → gabarit de commande qui montre le journal de la session (P69v2).
#: Même doctrine que :data:`RESUME_COMMANDS` : un hôte absent d'ici n'a pas de
#: commande connue, le bloc Activité n'en affiche aucune — jamais inventée.
LOGS_COMMANDS: dict[str, str] = {
    HostId.CLAUDE_CODE_CLI.value: "claude logs {session_id}",
}


def canonical_host(host: str) -> str:
    """La graphie canonique (valeur de ``HostId``) de *host*.

    Une seule table d'alias, celle de la CLI (:func:`grimoire.hosts.capabilities.resolve_host`,
    insensible à la casse) : le ledger a reçu ``claude-code-cli`` (``task attach`` /
    ``task_claim``) en plus de ``host-claude-code-cli`` (hooks), et ``task claim --host``
    prend du texte libre (``claude``, ``claude-code``, ``Claude``). Un hôte inconnu reste
    tel quel, nettoyé.
    """
    from grimoire.hosts.capabilities import resolve_host

    cleaned = (host or "").strip()
    resolved = resolve_host(cleaned)
    return resolved.value if resolved is not None else cleaned


def resume_command(session_host: str, session_id: str) -> str:
    """La commande qui reprend *session_id* sur *session_host*, ou ``""`` si inconnue."""
    template = RESUME_COMMANDS.get(canonical_host(session_host), "")
    if not template or not session_id:
        return ""
    return template.format(session_id=session_id)


def logs_command(session_host: str, session_id: str) -> str:
    """La commande qui montre le journal de *session_id* sur *session_host*, ou ``""`` si inconnue."""
    template = LOGS_COMMANDS.get(canonical_host(session_host), "")
    if not template or not session_id:
        return ""
    return template.format(session_id=session_id)


def session_fields(task: Any) -> dict[str, Any]:
    """``session_id``, ``host``, ``resume_command`` et ``logs_command`` d'une tâche — vides sans claim rattaché."""
    claim = getattr(task, "claim", None)
    if claim is None:
        return {"session_id": "", "host": "", "resume_command": "", "logs_command": ""}
    session_id = str(getattr(claim, "session_id", "") or "")
    host = canonical_host(str(getattr(claim, "session_host", "") or ""))
    return {
        "session_id": session_id,
        "host": host,
        "resume_command": resume_command(host, session_id),
        "logs_command": logs_command(host, session_id),
    }
