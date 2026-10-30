"""Outil MCP ``idea_add`` : garder une idée hors périmètre au lieu de la perdre (I13).

Module voisin de :mod:`grimoire.mcp.server` (au plafond du cliquet de lignes) : la fonction est
nue, :func:`register` l'enregistre avec le décorateur et les annotations du serveur. Même journal
que ``grimoire idea add`` (:class:`grimoire.missions.ideas.IdeaStore`), source ``agent``.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

__all__ = ["IDEA_ADD_LIMIT", "idea_add", "register"]

#: Idées qu'une session peut garder : de quoi ne pas perdre ce qu'on croise, pas de quoi noyer la boîte.
IDEA_ADD_LIMIT = 20
_idea_adds: dict[str, int] = {}


def _error(message: str) -> Any:
    from grimoire.mcp.server import _tool_error

    return _tool_error({"error": message})


def idea_add(text: str, project_path: str = ".", ledger_root: str = "_grimoire-runtime-output/ledger") -> Any:
    """Park an out-of-scope idea in the project's idea box instead of losing it (I13).

    Use it when you cross an idea that does not belong to your task: it is appended to
    `ideas.jsonl` (source `agent`) and a human decides later with `grimoire idea promote|drop`
    or from the cockpit. Nothing is triaged and no task is created. Bounded: 500 characters
    per idea, 20 ideas per session; a refused call says why.

    Args:
        text: The idea, one or two sentences, at most 500 characters.
        project_path: Path to project root (default: current directory).
        ledger_root: Mission Ledger directory, relative to the project root.
    """
    from grimoire.missions.ideas import IDEA_MAX_CHARS, IdeaError, IdeaStore

    session = os.environ.get("GRIMOIRE_SESSION_ID") or os.environ.get("CLAUDE_CODE_SESSION_ID") or ""
    key = f"{Path(project_path).resolve()}|{session}"
    if len(text.strip()) > IDEA_MAX_CHARS:
        return _error(f"idée trop longue : {len(text.strip())} caractères, {IDEA_MAX_CHARS} au plus")
    if _idea_adds.get(key, 0) >= IDEA_ADD_LIMIT:
        return _error(f"limite atteinte : {IDEA_ADD_LIMIT} idées par session ; le reste attend la prochaine session")
    try:
        idea = IdeaStore(Path(project_path).resolve(), Path(ledger_root)).add(text, "agent")
    except (IdeaError, OSError) as exc:
        return _error(str(exc))
    _idea_adds[key] = _idea_adds.get(key, 0) + 1
    return json.dumps(idea.to_dict(), indent=2, ensure_ascii=False)


def register(tool: Callable[[Any], Callable[[Any], Any]], writes: Callable[..., Any]) -> None:
    """Enregistre ``idea_add`` avec le décorateur et les annotations du serveur (écriture, non destructive)."""
    tool(writes(destructive=False, idempotent=False))(idea_add)
