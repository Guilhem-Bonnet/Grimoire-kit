"""Contrôle Codex de ``grimoire doctor`` (épique #1281, lot L2).

Codex n'exécute un hook de projet (``.codex/hooks.json``) qu'une fois approuvé
par l'utilisateur via ``/hooks`` ; l'approbation est liée à l'empreinte du hook,
donc à refaire après chaque régénération (https://learn.chatgpt.com/docs/hooks,
« Trust and review »). Le kit ne sait pas lire cette approbation : il ne peut
que la rappeler, et tenir la garde pour consultative tant qu'elle n'est pas
prouvée.

Séparé de :mod:`grimoire.cli.doctor_hooks` pour tenir sous le plafond de taille.
"""

from __future__ import annotations

import json
from pathlib import Path

from grimoire.cli.doctor_hooks import _launches_grimoire_hook
from grimoire.cli.env_checks import EnvCheck

CODEX_HOOKS = Path(".codex/hooks.json")


def _kit_commands(data: object) -> list[str]:
    """Commandes de ``grimoire-hook`` d'un ``hooks.json`` Codex (événement → groupes → gestionnaires)."""
    hooks = data.get("hooks") if isinstance(data, dict) else None
    found: list[str] = []
    for groups in hooks.values() if isinstance(hooks, dict) else []:
        for group in groups if isinstance(groups, list) else []:
            handlers = group.get("hooks") if isinstance(group, dict) else None
            for handler in handlers if isinstance(handlers, list) else []:
                command = handler.get("command") if isinstance(handler, dict) else None
                if isinstance(command, str) and _launches_grimoire_hook(command):
                    found.append(command)
    return found


def check_codex_hooks(target: Path) -> list[EnvCheck]:
    """Les hooks Codex du kit sont-ils là, et que faut-il pour qu'ils tournent ?"""
    path = target / CODEX_HOOKS
    if not path.is_file():
        return []
    rel = CODEX_HOOKS.as_posix()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [
            EnvCheck(
                "codex_hooks",
                passed=True,
                level="warn",
                detail=f"{rel} is not valid JSON: Codex runs none of its hooks",
                remedy="fix its syntax, then run `grimoire up` (or `grimoire host sync --host codex`)",
            )
        ]
    commands = _kit_commands(data)
    if not commands:
        return []
    return [
        EnvCheck(
            "codex_hooks",
            passed=True,
            level="warn",
            detail=(
                f"{len(commands)} Grimoire hook(s) in {rel}: approbation requise via /hooks — Codex runs a "
                "project hook only once it is trusted, and trust is tied to the hook's hash (re-review after "
                "each regeneration); the kit cannot read that approval, so the guard stays advisory"
            ),
            remedy="open Codex in this project, run `/hooks` and trust the Grimoire hooks",
        )
    ]
