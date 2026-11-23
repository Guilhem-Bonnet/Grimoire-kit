"""Contrôles ``hook_commands`` (#760), ``hook_timeout`` (#1276, #1277) et ``hook_path`` (#1022) de ``grimoire doctor``.

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


def _generated_hook_commands(target: Path) -> list[tuple[str, str, bool]]:
    """``(fichier, commande, échec fermé)`` de chaque hook du kit, pour l'OS courant.

    *échec fermé* : le gestionnaire porte ``"onFailure": "block"`` (Claude Code) ;
    un programme introuvable y refuse alors chaque appel d'outil gardé.

    Copilot porte une variante par OS : ``bash``/``powershell`` (harness
    Copilot, #1280), ou ``windows``/``linux``/``osx`` (harness Local, fichiers
    antérieurs) ; c'est elle que l'hôte lance sur cet OS quand elle existe.
    """
    if os.name == "nt":
        os_keys: tuple[str, ...] = ("windows", "powershell")
    else:
        os_keys = ("osx" if sys.platform == "darwin" else "linux", "bash")
    sources = [*sorted((target / ".github" / "hooks").glob("*.json")), target / ".claude" / "settings.json"]
    found: list[tuple[str, str, bool]] = []
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
                    variant = next((spec[key] for key in os_keys if spec.get(key)), None)
                    command = str(variant or spec.get("command") or "")
                    if _launches_grimoire_hook(command):
                        fails_closed = spec.get("onFailure") == "block"
                        found.append((path.relative_to(target).as_posix(), command, fails_closed))
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
    for relpath, command, fails_closed in commands:
        program = _program(command)
        reason = hook_program_problem(program, target)
        if reason:
            closed = ", onFailure: block — every guarded tool call will be refused" if fails_closed else ""
            problems.add(f"{relpath}: {program} ({reason}{closed})")
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


#: Fichiers de réglages Claude Code lus par ``hook_timeout``, du partagé au local.
_CLAUDE_SETTINGS = (".claude/settings.json", ".claude/settings.local.json")


def _fail_closed_guards(target: Path) -> dict[tuple[str, str], tuple[str, object]]:
    """Gardes ``PreToolUse`` du kit en échec fermé, par ``(matcher, commande)``.

    Valeur : ``(fichier, timeout brut)``. ``settings.json`` est lu, puis
    ``settings.local.json`` : une même garde (même matcher, même commande)
    redéclarée en local prend le délai local. Claude Code fusionne les hooks
    des deux fichiers ; une garde présente d'un seul côté est gardée telle quelle.
    """
    guards: dict[tuple[str, str], tuple[str, object]] = {}
    for relpath in _CLAUDE_SETTINGS:
        try:
            data = json.loads((target / relpath).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        hooks = data.get("hooks") if isinstance(data, dict) else None
        entries = hooks.get("PreToolUse") if isinstance(hooks, dict) else None
        for entry in entries if isinstance(entries, list) else []:
            inner = entry.get("hooks") if isinstance(entry, dict) else None
            matcher = str(entry.get("matcher") or "") if isinstance(entry, dict) else ""
            for spec in inner if isinstance(inner, list) else []:
                if not isinstance(spec, dict) or spec.get("onFailure") != "block":
                    continue
                command = str(spec.get("command") or "")
                if _launches_grimoire_hook(command):
                    guards[(matcher, command)] = (relpath, spec.get("timeout"))
    return guards


def check_guard_timeout(target: Path) -> list[EnvCheck]:
    """Une garde Claude Code en échec fermé a-t-elle un délai tenable sous charge ?

    Avec ``"onFailure": "block"``, un dépassement du ``timeout`` (secondes)
    refuse l'outil : un délai court, celui des projets installés en 3.92.0
    (10 s), refuse des commandes anodines sur une machine chargée. Lit
    ``.claude/settings.json`` puis ``.claude/settings.local.json`` (le local
    prime, #1277). Un délai booléen ou non numérique est invalide : ``bool``
    sous-classe ``int``, ``true`` vaudrait sinon 1 s. Rien à dire sans garde en
    échec fermé.
    """
    from grimoire.hosts.emitters.claude_code import FAIL_CLOSED_TIMEOUT

    guards = _fail_closed_guards(target)
    if not guards:
        return []
    short: list[str] = []
    invalid: list[str] = []
    for relpath, timeout in guards.values():
        if timeout is None:
            continue  # délai par défaut de Claude Code (60 s)
        if isinstance(timeout, bool) or not isinstance(timeout, int | float):
            invalid.append(f"{relpath}: {json.dumps(timeout)}")
        elif timeout < FAIL_CLOSED_TIMEOUT:
            short.append(f"{relpath}: {timeout:g} s")
    problems: list[str] = []
    if invalid:
        problems.append(
            f"PreToolUse guard fails closed with an invalid timeout ({'; '.join(invalid)}) — not a number of seconds"
        )
    if short:
        problems.append(
            f"PreToolUse guard fails closed with timeout {'; '.join(short)} "
            f"(< {FAIL_CLOSED_TIMEOUT} s) — a slow answer under load is refused by Claude Code"
        )
    if problems:
        remedy = "run `grimoire up` to re-emit the guard with its current timeout"
        if any(item.startswith(_CLAUDE_SETTINGS[1]) for item in invalid + short):
            # `up` n'écrit pas le fichier local : la correction y reste à la main.
            remedy += f"; set a timeout >= {FAIL_CLOSED_TIMEOUT} (seconds) in {_CLAUDE_SETTINGS[1]}"
        return [EnvCheck(
            "hook_timeout",
            passed=False,
            level="warn",
            detail="; ".join(problems),
            remedy=remedy,
        )]
    return [EnvCheck(
        "hook_timeout",
        passed=True,
        level="ok",
        detail=f"fail-closed PreToolUse guard timeout >= {FAIL_CLOSED_TIMEOUT} s",
    )]


def check_copilot_guard_timeout(target: Path) -> list[EnvCheck]:
    """La garde PreToolUse Copilot du kit, et ce que son délai garantit (#1280).

    Chez GitHub Copilot, un hook qui dépasse son délai laisse passer l'appel
    d'outil, garde comprise ; seule une sortie non nulle refuse. Le délai ne
    peut donc pas rendre la garde fermée, seulement rendre ce cas rare : sous
    le plancher du kit, avertissement ; au-dessus, une note le rappelle. Rien
    à dire sans fichier de garde Copilot du kit.
    """
    from grimoire.hosts.emitters.copilot import GUARD_TIMEOUT

    hooks_dir = target / ".github" / "hooks"
    if not hooks_dir.is_dir():
        return []
    guards: list[tuple[str, float]] = []
    for path in sorted(hooks_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        hooks = data.get("hooks") if isinstance(data, dict) else None
        if not isinstance(hooks, dict):
            continue
        entries = hooks.get("PreToolUse", hooks.get("preToolUse"))
        for spec in entries if isinstance(entries, list) else []:
            if not isinstance(spec, dict):
                continue
            commands = (spec.get(key) for key in ("command", "bash", "powershell", "windows", "linux", "osx"))
            launched = [str(c) for c in commands if c and _launches_grimoire_hook(str(c))]
            if not launched:
                continue
            if any("--decision grimoire.dispatch-model" in c for c in launched):
                # Entrée consultative (modèle des sous-agents, #1280 P1-4) : délai court voulu, pas une garde.
                continue
            # Même priorité que le harness Copilot : `timeoutSec`, puis son alias, puis 30 s.
            raw = spec.get("timeoutSec", spec.get("timeout", 30))
            delay = float(raw) if isinstance(raw, int | float) else 30.0
            guards.append((path.relative_to(target).as_posix(), delay))
    if not guards:
        return []
    short = [f"{relpath}: {delay:g} s" for relpath, delay in guards if delay < GUARD_TIMEOUT]
    if short:
        return [EnvCheck(
            "hook_timeout_copilot",
            passed=False,
            level="warn",
            detail=(
                f"Copilot PreToolUse guard timeout below {GUARD_TIMEOUT} s ({'; '.join(short)}) — "
                "GitHub Copilot lets a tool call through when a hook times out, so a short timeout "
                "leaves more calls unjudged under load"
            ),
            remedy="run `grimoire up` to re-emit the Copilot hooks with the current timeout",
        )]
    return [EnvCheck(
        "hook_timeout_copilot",
        passed=True,
        level="info",
        detail=(
            f"Copilot PreToolUse guard timeout >= {GUARD_TIMEOUT} s; GitHub Copilot fails open on a "
            "hook timeout (guard included), so the guard refuses on error but not on timeout"
        ),
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
