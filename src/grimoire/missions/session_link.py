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


#: Graphies historiques ou abrégées → valeur de ``HostId``. Le ledger a reçu
#: ``claude-code-cli`` (rattachement par ``task attach`` / ``task_claim``) en plus
#: de ``host-claude-code-cli`` (hooks) : les deux restent lues, une seule s'écrit.
HOST_ALIASES: dict[str, str] = {
    "claude-code-cli": HostId.CLAUDE_CODE_CLI.value,
    "claude": HostId.CLAUDE_CODE_CLI.value,
}


def canonical_host(host: str) -> str:
    """La graphie canonique (valeur de ``HostId``) de *host* ; un hôte inconnu reste tel quel."""
    cleaned = (host or "").strip()
    return HOST_ALIASES.get(cleaned, cleaned)


def resume_command(session_host: str, session_id: str) -> str:
    """La commande qui reprend *session_id* sur *session_host*, ou ``""`` si inconnue."""
    template = RESUME_COMMANDS.get(canonical_host(session_host), "")
    if not template or not session_id:
        return ""
    return template.format(session_id=session_id)


def session_fields(task: Any) -> dict[str, Any]:
    """``session_id``, ``host`` et ``resume_command`` d'une tâche — vides sans claim rattaché."""
    claim = getattr(task, "claim", None)
    if claim is None:
        return {"session_id": "", "host": "", "resume_command": ""}
    session_id = str(getattr(claim, "session_id", "") or "")
    host = canonical_host(str(getattr(claim, "session_host", "") or ""))
    return {
        "session_id": session_id,
        "host": host,
        "resume_command": resume_command(host, session_id),
    }
