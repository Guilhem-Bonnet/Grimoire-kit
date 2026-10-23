"""Contrôle ``hook_commands`` de ``grimoire doctor`` (issue #760).

Séparé de :mod:`grimoire.cli.cmd_up` pour tenir sous le plafond de taille des
modules.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from grimoire.cli.env_checks import EnvCheck
from grimoire.hosts.hook_launcher import hook_program_resolves


_HOOK_PROGRAMS = frozenset({"grimoire-hook", "grimoire-hook.exe"})


def _program(command: str) -> str:
    """Le programme d'une commande de hook du kit : ce qui précède ``--host``."""
    return command.partition(" --host ")[0].strip()


def _launches_grimoire_hook(command: str) -> bool:
    """Le programme lancé est-il ``grimoire-hook`` ? Un hook maison qui le cite
    seulement (``grimoire-hook-gateway.sh --hook-id …``) n'est pas un hook du kit."""
    name = re.split(r"[\\/]", _program(command).replace('"', "").replace("'", ""))[-1]
    return name in _HOOK_PROGRAMS


def _generated_hook_commands(target: Path) -> list[tuple[str, str]]:
    """``(fichier, commande)`` de chaque hook du kit, pour l'OS courant.

    Copilot porte une variante ``windows``/``linux``/``osx`` par entrée ; c'est
    elle que l'hôte lance sur cet OS quand elle existe.
    """
    os_key = {"nt": "windows"}.get(os.name, "osx" if sys.platform == "darwin" else "linux")
    sources = [*sorted((target / ".github" / "hooks").glob("*.json")), target / ".claude" / "settings.json"]
    found: list[tuple[str, str]] = []
    for path in sources:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        hooks = data.get("hooks") if isinstance(data, dict) else None
        if not isinstance(hooks, dict):
            continue
        for entries in hooks.values():
            for entry in entries if isinstance(entries, list) else []:
                # Claude Code imbrique ``{"matcher", "hooks": [...]}`` ; Copilot non.
                inner = entry.get("hooks", [entry]) if isinstance(entry, dict) else []
                for spec in inner if isinstance(inner, list) else []:
                    if not isinstance(spec, dict):
                        continue
                    command = str(spec.get(os_key) or spec.get("command") or "")
                    if _launches_grimoire_hook(command):
                        found.append((path.relative_to(target).as_posix(), command))
    return found


def check_hook_commands(target: Path) -> list[EnvCheck]:
    """Chaque commande de hook générée se lance-t-elle dans l'environnement de l'hôte ? (issue #760)

    L'hôte (VS Code, Claude Code) lance ses hooks sans le venv activé du shell
    courant : un ``grimoire-hook`` installé dans ce seul venv ne s'y résout
    pas, et tous les hooks tombent sans message de Grimoire.
    """
    commands = _generated_hook_commands(target)
    if not commands:
        return []
    broken = sorted(
        {
            f"{relpath}: {_program(command)}"
            for relpath, command in commands
            if not hook_program_resolves(_program(command), target)
        }
    )
    if broken:
        return [EnvCheck(
            "hook_commands",
            passed=False,
            level="fail",
            detail="hook program not found outside the active venv: " + "; ".join(broken),
            remedy=(
                "install the kit for the host (uv tool install grimoire-kit, or pipx), "
                "or run `grimoire host sync` to write the venv path into the hooks"
            ),
            optional=False,
        )]
    return [EnvCheck(
        "hook_commands",
        passed=True,
        level="ok",
        detail=f"{len(commands)} hook command(s) resolve outside the active venv",
    )]
