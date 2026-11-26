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

__all__ = ["atelier_comment", "atelier_comments", "atelier_suggest", "register"]


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


def atelier_comment(
    chantier: str,
    text: str,
    block_id: str = "",
    quote: str = "",
    start: int | None = None,
    reply_to: str = "",
    suggestion: str = "",
    project_path: str = ".",
) -> Any:
    """Comment on a passage of a chantier's spec document, or reply in an open thread; the human reads it on screen.

    Nothing is written to the document. The comment is anchored to the passage you quote and shows beside it in the
    cockpit (Atelier tab, which must be enabled); you are always the author "agent". You can neither resolve, reopen
    nor delete a thread: the human does. Read the open threads first with `atelier_comments` and answer in them.

    Args:
        chantier: Slug of the chantier (the default one is `principal`).
        text: What you say (2000 characters at most).
        block_id: Id of the block you comment on (`<phase>.<fingerprint>`, as served with the document); required
            unless you reply.
        quote: The exact passage of that block you refer to (300 characters at most); omit it to comment the block
            as a whole. If the passage occurs more than once in the block, also give `start`.
        start: Offset of `quote` in the block text, when it is ambiguous.
        reply_to: Id of an open thread (`com-…`) to answer in instead of opening a new one.
        suggestion: Id of a suggestion you just filed with `atelier_suggest` (`sug-…`) that this comment goes with.
        project_path: Path to project root (default: current directory).
    """
    from grimoire.tools.workspace_atelier import ConflictError
    from grimoire.tools.workspace_atelier_comments import agent_comment

    try:
        done = agent_comment(project_path, chantier, text, block_id, quote, start, reply_to, suggestion)
    except (ValueError, ConflictError, PermissionError, FileNotFoundError, TimeoutError) as exc:
        return _error(str(exc))
    return json.dumps(done, indent=2, ensure_ascii=False)


def atelier_comments(chantier: str, project_path: str = ".") -> Any:
    """List the open comment threads of a chantier's spec document, with the quoted passage, to answer them.

    Each thread gives its id, author, text and replies, the passage it quotes (`quote`), where that passage lives
    today (`anchor.state`: `ancre`, `retrouve`, or `orphelin` when the passage was changed or deleted; never guessed)
    and the current text of its block (`block_text`). Answer with `atelier_comment` and `reply_to`.

    Args:
        chantier: Slug of the chantier (the default one is `principal`).
        project_path: Path to project root (default: current directory).
    """
    from grimoire.tools.workspace_atelier import ConflictError
    from grimoire.tools.workspace_atelier_comments import agent_threads

    try:
        done = agent_threads(project_path, chantier)
    except (ValueError, ConflictError, PermissionError, FileNotFoundError, TimeoutError) as exc:
        return _error(str(exc))
    return json.dumps(done, indent=2, ensure_ascii=False)


def register(tool: Callable[[Any], Callable[[Any], Any]], writes: Callable[..., Any]) -> None:
    """Enregistre ``atelier_suggest`` et ``atelier_comment`` (écriture dans le magasin du chantier seulement, non
    destructive) et ``atelier_comments`` (lecture). Aucun outil ne résout, ne rouvre ni ne supprime un fil."""
    from mcp.types import ToolAnnotations

    tool(writes(destructive=False, idempotent=True))(atelier_suggest)
    tool(writes(destructive=False, idempotent=True))(atelier_comment)
    reads = ToolAnnotations.model_validate(
        {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
    )
    tool(reads)(atelier_comments)
