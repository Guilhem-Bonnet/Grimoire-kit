"""Outil MCP ``atelier_suggest`` : proposer une modification d'un bloc de l'Atelier de spec (#1197, #1342).

Module voisin de :mod:`grimoire.mcp.server` (au plafond du cliquet de lignes). L'agent est la maîtrise d'œuvre :
il **dépose** une suggestion dans le magasin du chantier ; il ne modifie jamais le fichier de phase et n'a aucun
moyen de l'accepter ou de la refuser (il n'existe ici ni outil ni paramètre de décision : l'humain tranche à
l'écran du cockpit). La fonction est nue, :func:`register` l'enregistre avec le décorateur du serveur.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

__all__ = ["atelier_suggest", "register"]


def _error(message: str) -> Any:
    from grimoire.mcp.server import _tool_error

    return _tool_error({"error": message})


def atelier_suggest(
    chantier: str,
    block_id: str,
    text: str,
    reason: str = "",
    posture: str = "explorer",
    project_path: str = ".",
) -> Any:
    """Suggest a rewrite of one block of a chantier's spec document; a human accepts or refuses it on screen.

    Nothing is written to the document: the suggestion waits in the chantier's store until the human decides in
    the cockpit (Atelier tab, which must be enabled). You cannot accept or refuse a suggestion, and the block
    keeps its text until the human accepts. If the block changed since you read it, the call is refused: read the
    chantier again (`GET atelier/chantiers/<slug>` gives each block id).

    Args:
        chantier: Slug of the chantier (the default one is `principal`).
        block_id: Id of the block to rewrite, as served with the document (`<phase>.<fingerprint>`).
        text: The full proposed text of the block (replaces the block, no headings or `---` lines).
        reason: Why, in one short sentence (300 characters at most).
        posture: `explorer`, `sceptique` or `simplificateur`.
        project_path: Path to project root (default: current directory).
    """
    from grimoire.tools.workspace_atelier import ConflictError
    from grimoire.tools.workspace_atelier_suggestions import suggest

    try:
        done = suggest(project_path, chantier, block_id, text, reason, posture)
    except (ValueError, ConflictError, PermissionError, FileNotFoundError, TimeoutError) as exc:
        return _error(str(exc))
    return json.dumps(done, indent=2, ensure_ascii=False)


def register(tool: Callable[[Any], Callable[[Any], Any]], writes: Callable[..., Any]) -> None:
    """Enregistre ``atelier_suggest`` (écriture dans le magasin du chantier seulement, non destructive)."""
    tool(writes(destructive=False, idempotent=True))(atelier_suggest)
