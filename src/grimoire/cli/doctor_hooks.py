"""Contrôles ``hook_commands`` (issue #760) et ``hook_path`` (issue #1022) de ``grimoire doctor``.

Séparé de :mod:`grimoire.cli.cmd_up` pour tenir sous le plafond de taille des
modules.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

from grimoire.cli.env_checks import EnvCheck
from grimoire.hosts.hook_launcher import command_program, hook_program_problem

_HOOK_PROGRAMS = frozenset({"grimoire-hook", "grimoire-hook.exe"})


def _program(command: str) -> str:
    """Le programme d'une commande de hook du kit.

    Avant ``--host`` quand il y est (chemin non guillemeté avec espaces) ; sinon
    le premier mot : un script de gateway porte d'autres arguments.
    """
    if " --host " in command:
        return command.partition(" --host ")[0].strip()
    return command_program(command)


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
    problems: set[str] = set()
    for relpath, command in commands:
        program = _program(command)
        reason = hook_program_problem(program, target)
        if reason:
            problems.add(f"{relpath}: {program} ({reason})")
    broken = sorted(problems)
    if broken:
        return [EnvCheck(
            "hook_commands",
            passed=False,
            level="fail",
            detail="hook program not launchable outside the active venv: " + "; ".join(broken),
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


def _unresolvable_hooks(target: Path, unit_path: str) -> list[str]:
    """Les hooks utilisateur/projet dont le programme ne se résout pas dans *unit_path*.

    Le dossier d'un hook appelé par chemin absolu (voir ``timer._unit_path``) est
    normalement gardé dans le ``PATH`` de l'unité (#1111) : il ne manque que
    lorsqu'il est temporaire, absent ou porte une fin de ligne. Un nom nu se
    résout comme avant, dans le ``PATH`` de l'unité. Les chemins relatifs et les
    variables non résolues (``$CLAUDE_PROJECT_DIR/...``) sont laissés au shell
    qui lance le hook : les signaler flaguerait tous les hooks de projet.
    """
    from grimoire.missions.timer import _hook_commands_with_settings, _hook_program

    problems: list[str] = []
    for settings, command in _hook_commands_with_settings(target):
        program = _hook_program(command)
        if program is None:
            continue  # commande vide ou illisible : rien à résoudre
        normalise = program.replace("\\", "/")
        resolu = Path(os.path.expandvars(str(Path(normalise).expanduser())))
        if "/" in normalise and not resolu.is_absolute():
            continue  # relatif ou variable non résolue : le shell décide, pas le PATH de l'unité
        # `shutil.which` ne cherche que des noms nus : on interroge le nom du
        # programme, le dossier absolu étant (ou non) entré dans le PATH de l'unité.
        if resolu.name and (not unit_path or shutil.which(resolu.name, path=unit_path) is None):
            problems.append(f"{command} ({settings})")
    return list(dict.fromkeys(problems))


def check_hook_path(target: Path) -> list[EnvCheck]:
    """Un hook de l'hôte se résout-il dans le ``PATH`` de l'unité du tick ? (issue #1022)

    Le tick lance ses sessions avec le ``PATH`` réduit de ``timer._unit_path``.
    Un hook qui réécrit une commande vers un programme absent de ce ``PATH``
    fait échouer la session en 127 : le worker s'arrête au premier geste. Silence
    quand tout se résout — ce contrôle ne fait jamais échouer ``doctor`` : une
    unité n'est peut-être pas installée.
    """
    from grimoire.missions.timer import _unit_path

    # Même calcul que `timer._path_line` : l'interpréteur qui pose le minuteur
    # est celui qui exécute `doctor`, et son dossier porte `grimoire-hook`.
    unit_path = _unit_path(os.environ.get("PATH", ""), Path(sys.executable).parent, target)
    problems = _unresolvable_hooks(target, unit_path)
    if not problems:
        return []
    return [EnvCheck(
        "hook_path",
        passed=True,
        level="warn",
        detail="hook program not resolvable in the tick unit's PATH: " + "; ".join(problems),
        remedy=(
            "move the hook program out of a temporary directory, or link it into a "
            "directory the unit PATH keeps (next to `claude`, `gh`, `git` or `node`)"
        ),
    )]
