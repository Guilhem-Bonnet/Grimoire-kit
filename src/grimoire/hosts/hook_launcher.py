"""Quel programme une configuration de hook générée doit lancer (issue #760).

Un hook est lancé par l'hôte (VS Code, Claude Code), avec *son* environnement,
pas celui du shell où ``grimoire host sync`` a tourné. Écrire le nom nu
``grimoire-hook`` ne marche que si ce nom se résout dans le PATH de l'hôte :
vrai pour ``pipx``/``uv tool install``, faux pour ``uv pip install`` ou
``pip install`` dans un venv. Le programme vit alors dans ``.venv/bin``
(``.venv\\Scripts`` sous Windows), que seul un shell où le venv est activé
voit. Constaté sous Windows : tous les outils du concierge bloqués sous
Copilot sur une installation neuve.

Résolution, du plus portable au plus local :

1. ``GRIMOIRE_HOOK_COMMAND`` posé : sa valeur, telle quelle (choix explicite).
2. ``grimoire-hook`` se résout dans le PATH *sans* le venv actif : nom nu,
   comme avant. Rien ne change pour une installation globale.
3. Le programme est dans un venv situé sous la racine du projet : chemin
   relatif à cette racine, identique sur chaque poste qui a son ``.venv``.
4. Sinon, venv hors du projet : chemin absolu, propre à la machine, et
   signalé comme tel.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

HOOK_SCRIPT = "grimoire-hook"
ENV_OVERRIDE = "GRIMOIRE_HOOK_COMMAND"
MCP_SCRIPT = "grimoire-mcp"
MCP_ENV_OVERRIDE = "GRIMOIRE_MCP_COMMAND"


@dataclass(frozen=True)
class HookLauncher:
    """Le programme d'un hook, sous la forme que chaque émetteur écrit.

    ``posix`` et ``windows`` sont des chemins *relatifs à la racine du projet*
    quand ``project_relative`` est vrai ; sinon ``posix`` est la commande à
    écrire telle quelle et ``windows`` vaut ``None``.
    """

    posix: str
    windows: str | None = None
    project_relative: bool = False
    machine_specific: bool = False


BARE = HookLauncher(posix=HOOK_SCRIPT)


def _venv_script_dirs(prefix: str, base_prefix: str, environ: Mapping[str, str]) -> set[str]:
    roots = []
    if prefix != base_prefix:
        roots.append(prefix)
    if environ.get("VIRTUAL_ENV"):
        roots.append(environ["VIRTUAL_ENV"])
    dirs: set[str] = set()
    for root in roots:
        for sub in ("bin", "Scripts"):
            dirs.add(os.path.normcase(os.path.normpath(str(Path(root) / sub))))
    return dirs


def path_without_active_venv(
    environ: Mapping[str, str] | None = None,
    *,
    prefix: str | None = None,
    base_prefix: str | None = None,
) -> str:
    """Le PATH tel que le voit un processus qui n'a pas activé le venv courant."""
    env = os.environ if environ is None else environ
    excluded = _venv_script_dirs(
        sys.prefix if prefix is None else prefix,
        sys.base_prefix if base_prefix is None else base_prefix,
        env,
    )
    entries = env.get("PATH", "").split(os.pathsep)
    kept = [e for e in entries if e and os.path.normcase(os.path.normpath(e)) not in excluded]
    return os.pathsep.join(kept)


def _script_next_to(executable: str, script: str) -> Path | None:
    scripts = Path(executable).parent
    for name in (script, f"{script}.exe"):
        candidate = scripts / name
        if candidate.is_file():
            return candidate
    return None


def resolve_hook_launcher(
    project_root: Path,
    *,
    script: str = HOOK_SCRIPT,
    override_env: str = ENV_OVERRIDE,
    environ: Mapping[str, str] | None = None,
    executable: str | None = None,
    prefix: str | None = None,
    base_prefix: str | None = None,
) -> HookLauncher:
    """Résoudre le programme à écrire dans les hooks de *project_root*. Ne lève jamais.

    *script* et *override_env* servent les autres console scripts que l'hôte
    lance lui-même, comme ``grimoire-mcp`` (:data:`MCP_SCRIPT`).
    """
    env = os.environ if environ is None else environ
    override = env.get(override_env, "").strip()
    if override:
        return HookLauncher(posix=override)
    bare = BARE if script == HOOK_SCRIPT else HookLauncher(posix=script)
    global_path = path_without_active_venv(env, prefix=prefix, base_prefix=base_prefix)
    if shutil.which(script, path=global_path):
        return bare
    found = _script_next_to(sys.executable if executable is None else executable, script)
    if found is None:
        return bare  # rien de mieux à écrire ; ``grimoire doctor`` le signale
    venv_root = found.parent.parent
    try:
        rel = venv_root.resolve().relative_to(project_root.resolve())
    except (OSError, ValueError):
        return HookLauncher(posix=found.as_posix(), machine_specific=True)
    parts = rel.parts
    return HookLauncher(
        posix=str(PurePosixPath(*parts, "bin", script)),
        windows=str(PureWindowsPath(*parts, "Scripts", f"{script}.exe")),
        project_relative=True,
    )


#: Forme documentée par Claude Code pour un chemin projet dans un ``.mcp.json``
#: de projet : la variable n'existe que dans l'environnement du serveur, d'où
#: le défaut obligatoire (https://code.claude.com/docs/en/mcp).
MCP_PROJECT_DIR = "${CLAUDE_PROJECT_DIR:-.}"
_PROJECT_DIR_VARS = (
    MCP_PROJECT_DIR,
    "${CLAUDE_PROJECT_DIR}",
    "$CLAUDE_PROJECT_DIR",
    "%CLAUDE_PROJECT_DIR%",
    "${workspaceFolder}",
    "${workspaceRoot}",
)


def mcp_command(
    project_root: Path,
    *,
    environ: Mapping[str, str] | None = None,
    executable: str | None = None,
    prefix: str | None = None,
    base_prefix: str | None = None,
) -> str:
    """La commande du serveur MCP Grimoire à écrire dans le ``.mcp.json`` du projet.

    Même résolution que les hooks (issue #760) ; un venv sous la racine donne
    ``${CLAUDE_PROJECT_DIR:-.}/.venv/bin/grimoire-mcp`` (``Scripts\…exe`` sous
    Windows), un venv hors du projet son chemin absolu.
    """
    launcher = resolve_hook_launcher(
        project_root,
        script=MCP_SCRIPT,
        override_env=MCP_ENV_OVERRIDE,
        environ=environ,
        executable=executable,
        prefix=prefix,
        base_prefix=base_prefix,
    )
    if not launcher.project_relative:
        return launcher.posix
    rel = launcher.windows if os.name == "nt" and launcher.windows else launcher.posix
    return f"{MCP_PROJECT_DIR}/{rel.replace(chr(92), '/')}"


def expand_project_dir(command: str, project_root: Path) -> str:
    """Remplacer la variable de racine de projet d'une commande par *project_root*."""
    for var in _PROJECT_DIR_VARS:
        if command.startswith(var):
            return str(project_root) + command[len(var) :].replace('"', "")
    return command


def command_program(command: str) -> str:
    """Le programme d'une ligne de commande de hook, sans ses arguments.

    Un programme entre guillemets (ou ``"$CLAUDE_PROJECT_DIR"/chemin``) peut
    contenir des espaces ; hors guillemets, le programme s'arrête au premier
    blanc. Les antislashs sont conservés (chemins Windows).
    """
    text = command.strip()
    quote = ""
    end = len(text)
    for index, char in enumerate(text):
        if quote:
            quote = "" if char == quote else quote
        elif char in "\"'":
            quote = char
        elif char.isspace():
            end = index
            break
    return text[:end]


def hook_program_problem(program: str, project_root: Path, *, environ: Mapping[str, str] | None = None) -> str | None:
    """Pourquoi *program* ne se lance pas hors du venv actif, ou ``None`` s'il se lance.

    Un nom nu doit se résoudre dans le PATH sans le venv actif (ce que voit
    l'hôte). Un chemin non absolu se résout contre la racine du projet (là où
    l'hôte lance la commande : ``$CLAUDE_PROJECT_DIR`` pour Claude Code, le
    workspace pour Copilot) et reste confiné à cette racine ; un chemin doit
    exister *et* être exécutable. Les variables de racine
    (``$CLAUDE_PROJECT_DIR``, ``${workspaceFolder}``) sont remplacées par la
    racine du projet.
    """
    env = os.environ if environ is None else environ
    program = expand_project_dir(program.strip().strip("\"'"), project_root)
    if "/" not in program and "\\" not in program:
        found = shutil.which(program, path=path_without_active_venv(env)) is not None
        return None if found else "not found"
    native = program.replace("\\", os.sep) if os.sep != "\\" else program
    if Path(native).is_absolute():
        candidate = Path(native)
    else:
        root = project_root.resolve()
        candidate = (root / native).resolve()
        if not candidate.is_relative_to(root):
            return "outside the project"
    if not candidate.is_file():
        return "not found"
    if shutil.which(str(candidate)) is not None:
        return None
    if os.name == "nt" and candidate.suffix.lower() == ".sh" and shutil.which("bash") is not None:
        return None  # un script shell se lance par bash (Git Bash) sous Windows
    return "not launchable on this OS" if os.name == "nt" else "not executable"


def hook_program_resolves(program: str, project_root: Path, *, environ: Mapping[str, str] | None = None) -> bool:
    """Le *program* d'une commande de hook se lance-t-il hors du venv actif ?"""
    return hook_program_problem(program, project_root, environ=environ) is None
