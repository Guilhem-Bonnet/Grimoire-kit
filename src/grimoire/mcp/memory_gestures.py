"""Outils MCP de correction de la mémoire : retirer, rétablir, réétiqueter (P23).

Module voisin de :mod:`grimoire.mcp.server` (au plafond du cliquet de lignes) :
les fonctions sont nues, ``server.py`` les enregistre par :func:`register`
avec son décorateur ``_tool``. Chacune passe par
:func:`grimoire.memory.gestures.run_gesture`, le point d'entrée de la CLI
(``grimoire memory retract|unretract|tags``) : mêmes validations, même
cloisonnement par projet, mêmes refus, même journal des écritures. **Aucun
outil n'est plus permissif que son équivalent CLI.** Aucun ne supprime : un
backend qui ne sait pas modifier un souvenir est refusé avec un message, pas
traité comme une suppression.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from grimoire.core.config import GrimoireConfig
from grimoire.core.exceptions import GrimoireError

__all__ = [
    "grimoire_memory_retract",
    "grimoire_memory_unretract",
    "grimoire_memory_update_tags",
    "register",
]


def _error(message: str) -> Any:
    from grimoire.mcp.server import _tool_error

    return _tool_error({"error": message})


def _run(project_path: str, gesture: str, entry_id: str, **kwargs: Any) -> Any:
    from grimoire.memory.gestures import run_gesture
    from grimoire.memory.manager import MemoryManager

    target = Path(project_path).resolve()
    try:
        cfg = GrimoireConfig.find_and_load(target)
        mgr = MemoryManager.from_config(cfg, project_root=target)
        mgr.surface = "mcp"
        payload = run_gesture(mgr, gesture, entry_id, **kwargs)
    except GrimoireError as exc:
        return _error(str(exc))
    return json.dumps(payload, indent=2, ensure_ascii=False)


def grimoire_memory_retract(entry_id: str, reason: str, project_path: str = ".") -> str:
    """Retire a memory from service without deleting it.

    The memory is no longer returned by ``grimoire_memory_search`` (unless
    ``include_retracted`` is set) but stays readable, with who retracted it, when
    and why. Reversible with ``grimoire_memory_unretract``. Refused on a backend
    that cannot modify a memory (nothing is deleted) and on another project's
    memory. A reason is mandatory.

    Args:
        entry_id: ID of the memory to retract.
        reason: Why it should no longer be served.
        project_path: Path to project root (default: current directory).
    """
    return _run(project_path, "retract", entry_id, reason=reason)  # type: ignore[no-any-return]


def grimoire_memory_unretract(entry_id: str, reason: str = "", project_path: str = ".") -> str:
    """Undo a retraction: the memory is served again.

    Args:
        entry_id: ID of the retracted memory.
        reason: Optional note, traced in the write journal.
        project_path: Path to project root (default: current directory).
    """
    return _run(project_path, "unretract", entry_id, reason=reason)  # type: ignore[no-any-return]


def grimoire_memory_update_tags(
    entry_id: str,
    add: list[str] | None = None,
    remove: list[str] | None = None,
    replace: list[str] | None = None,
    reason: str = "",
    project_path: str = ".",
) -> str:
    """Add, remove or replace a memory's tags (its text and metadata do not change).

    Args:
        entry_id: ID of the memory to retag.
        add: Tags to add.
        remove: Tags to remove.
        replace: Replace all tags with these (exclusive with add/remove).
        reason: Optional note, traced in the write journal.
        project_path: Path to project root (default: current directory).
    """
    return _run(  # type: ignore[no-any-return]
        project_path, "update_tags", entry_id, reason=reason,
        add=add or [], remove=remove or [], replace=replace,
    )


def register(tool: Callable[[Any], Callable[[Any], Any]], writes: Callable[..., Any]) -> None:
    """Enregistre les trois outils avec le décorateur et les annotations du serveur."""
    # Aucun n'est destructif : le souvenir reste en place, le retrait se défait.
    tool(writes(destructive=False, idempotent=False))(grimoire_memory_retract)
    tool(writes(destructive=False, idempotent=False))(grimoire_memory_unretract)
    tool(writes(destructive=False, idempotent=True))(grimoire_memory_update_tags)
    # Boîte à idées (I13) : même raison, ``server.py`` est au plafond du cliquet de lignes.
    from grimoire.mcp import idea_tools

    idea_tools.register(tool, writes)
