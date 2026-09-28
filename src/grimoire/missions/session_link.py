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


def resume_command(session_host: str, session_id: str) -> str:
    """La commande qui reprend *session_id* sur *session_host*, ou ``""`` si inconnue."""
    template = RESUME_COMMANDS.get(session_host, "")
    if not template or not session_id:
        return ""
    return template.format(session_id=session_id)


def session_fields(task: Any) -> dict[str, Any]:
    """``session_id``, ``host`` et ``resume_command`` d'une tâche — vides sans claim rattaché."""
    claim = getattr(task, "claim", None)
    if claim is None:
        return {"session_id": "", "host": "", "resume_command": ""}
    session_id = str(getattr(claim, "session_id", "") or "")
    host = str(getattr(claim, "session_host", "") or "")
    return {
        "session_id": session_id,
        "host": host,
        "resume_command": resume_command(host, session_id),
    }
