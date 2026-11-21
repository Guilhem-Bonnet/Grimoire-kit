"""Configuration MCP de Copilot dans VS Code (épique #1280, lot P2-2).

Source : https://code.visualstudio.com/docs/agents/reference/mcp-configuration
(lue le 2026-11-21). Le fichier d'espace de travail ``.vscode/mcp.json`` porte la
clé ``servers`` ; un serveur stdio y est ``{"type": "stdio", "command", "args", "env"}``
et ``${workspaceFolder}`` y est une variable documentée. Un serveur distant y est
``{"type": "http", "url"}``.

Le fichier appartient aussi à l'utilisateur (autres serveurs, ``inputs``) : la fusion
ne fait qu'ajouter un serveur absent, jamais remplacer ni retirer une entrée — comme
``.gemini/settings.json`` et ``opencode.json``. Sans marqueur de propriété possible
dans du JSON, c'est le nom du serveur qui borne ce que le kit écrit ; un fichier
déclaré ``hosts.preserve`` n'est jamais touché.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from grimoire.hosts.emitters.base import JsonMerge
from grimoire.hosts.hook_launcher import _PROJECT_DIR_VARS
from grimoire.hosts.surface import McpServerSpec

VSCODE_MCP = Path(".vscode") / "mcp.json"
WORKSPACE_FOLDER = "${workspaceFolder}"


def workspace_command(command: str) -> str:
    """Remplacer la variable de racine de Claude Code par celle de VS Code (documentée)."""
    for var in _PROJECT_DIR_VARS:
        if command.startswith(var):
            return WORKSPACE_FOLDER + command[len(var) :].replace('"', "")
    return command


def server_entry(server: McpServerSpec) -> dict[str, Any]:
    """L'entrée ``servers.<nom>`` de *server*."""
    if server.url and not server.command:
        return {"type": "http", "url": server.url}
    entry: dict[str, Any] = {"type": "stdio", "command": workspace_command(server.command)}
    if server.args:
        entry["args"] = list(server.args)
    return entry


def vscode_mcp_merge(servers: tuple[McpServerSpec, ...]) -> JsonMerge | None:
    """La fusion dans ``.vscode/mcp.json``, ou ``None`` quand le projet ne déclare aucun serveur."""
    if not servers:
        return None
    entries = {server.name: server_entry(server) for server in servers}

    def merge(data: dict[str, Any]) -> dict[str, Any]:
        current = data.get("servers")
        current = current if isinstance(current, dict) else {}
        for name, entry in entries.items():
            # Un serveur déjà déclaré est celui de l'utilisateur : on n'ajoute que ce qui manque.
            current.setdefault(name, entry)
        data["servers"] = current
        return data

    return JsonMerge(relpath=VSCODE_MCP, merge=merge, label=".vscode/mcp.json")
