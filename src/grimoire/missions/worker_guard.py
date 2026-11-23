"""Ce qui doit tenir avant tout appel à opencode pour un worker sans surveillance (épique #1281, lot L4).

``opencode`` charge et exécute le code des plugins qu'il découvre dès qu'il lit sa configuration, y compris pour une
commande de diagnostic. Aucune commande opencode n'est donc lancée tant que ces conditions, vérifiées sans opencode,
ne tiennent pas (:func:`integrity_refusal`) :

- le plugin de garde du worktree est, à l'octet près, celui que l'émetteur du kit produit pour ce worktree (sinon il
  est réécrit depuis l'émetteur, puis revérifié) ;
- aucun autre fichier de code n'est découvrable comme plugin ou outil (``.opencode/plugin``, ``plugins``, ``tools``) ;
- aucun fichier de configuration opencode du worktree (``opencode.json`` ou ``opencode.jsonc``, à la racine ou dans
  ``.opencode/``) ne peut réorienter un fournisseur ;
- le manifeste de dépendances que opencode installe lui-même ne déclare rien d'autre que son paquet de plugin ;
- aucun lien symbolique sur ce chemin.

Puis une preuve fonctionnelle (:func:`hook_witness_refusal`) : le programme de garde que le plugin appelle, lancé avec
une charge témoin qui ne s'exécute jamais mais que la politique refuse, doit répondre par un refus. Pour un worker sans
surveillance, un programme introuvable ou qui laisse passer bloque le lancement, quel que soit le profil.

L'environnement de chaque commande opencode est minimal et explicite (:func:`minimal_env`) : rien de l'environnement
du tick n'y passe hors d'une liste fermée, aucun jeton ni aucune variable qui changerait la découverte de la
configuration d'opencode.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "CODE_SUFFIXES",
    "ENV_ALLOWED",
    "RunResult",
    "exact_run",
    "expected_plugin",
    "hook_witness_refusal",
    "integrity_refusal",
    "minimal_env",
    "opencode_config_home",
]

#: Suffixes d'un fichier qu'opencode peut charger comme plugin ou outil.
CODE_SUFFIXES = frozenset({".js", ".ts", ".mjs", ".cjs", ".mts", ".cts", ".jsx", ".tsx"})
#: Dossiers où opencode découvre du code (noms singuliers et pluriels, https://opencode.ai/docs/config/).
_CODE_DIRS = ("plugin", "plugins", "tool", "tools")
_CONFIG_FILES = ("opencode.json", "opencode.jsonc", "config.json")
#: Fichiers d'installation de paquets qu'opencode lirait dans ``.opencode/`` : un réglage du gestionnaire est refusé.
_INSTALLER_FILES = ("bunfig.toml", ".npmrc", ".yarnrc", ".yarnrc.yml")
#: Le seul paquet que le manifeste ``.opencode/package.json`` posé par opencode lui-même déclare.
_PLUGIN_PACKAGE = "@opencode-ai/plugin"
#: Variables de l'environnement du tick qui passent à opencode : ni jeton, ni clé, ni réglage de découverte.
ENV_ALLOWED = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LC_CTYPE",
    "LC_MESSAGES",
    "TERM",
    "TZ",
    "TMPDIR",
    "TEMP",
    "TMP",
    "SSH_AUTH_SOCK",
    "VIRTUAL_ENV",
    "XDG_RUNTIME_DIR",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "XDG_CACHE_HOME",
    "SYSTEMROOT",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
)
#: La charge témoin du hook : une commande destructive que la politique du kit refuse dans tous les profils. Elle
#: n'est jamais exécutée : seul le programme de garde la lit.
_WITNESS_COMMAND = "git push --force origin main"
_HOOK_DECL = re.compile(r"^const HOOK = (\{.*\})$", re.MULTILINE)
_GUARDED_DECL = re.compile(r"^const GUARDED = (\[.*\])$", re.MULTILINE)
_GUARD_ARGS = re.compile(r"spawnSync\(hookProgram\(directory\), (\[[^\]]*\]), \{")
_DEFAULT_ARGS = ("--host", "opencode", "--event", "PreToolUse")
_WITNESS_TIMEOUT_S = 30.0
_RUN_TIMEOUT_S = 60.0


@dataclass(frozen=True, slots=True)
class RunResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


#: ``(argv, cwd, env, stdin)`` → résultat, l'environnement passé **tel quel** (jamais fusionné à celui du tick).
Runner = Callable[[Sequence[str], Path, Mapping[str, str], str], RunResult]


def exact_run(argv: Sequence[str], cwd: Path, env: Mapping[str, str], stdin: str = "") -> RunResult:
    """Lance *argv* sans shell avec exactement *env* ; un binaire absent ou un délai dépassé est un échec lisible."""
    try:
        done = subprocess.run(  # arguments fixes, sans shell, environnement explicite
            list(argv),
            cwd=cwd,
            env=dict(env),
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_RUN_TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError:
        return RunResult(127, "", "binaire introuvable")
    except subprocess.TimeoutExpired:
        return RunResult(124, "", "délai dépassé")
    except OSError as exc:
        return RunResult(126, "", type(exc).__name__)
    return RunResult(done.returncode, done.stdout or "", done.stderr or "")


def opencode_config_home(root: Path) -> Path:
    """Le dossier de configuration utilisateur qu'opencode voit pour un worker (``XDG_CONFIG_HOME``) : celui du kit.

    opencode lit sa configuration globale sous ``$XDG_CONFIG_HOME/opencode`` (vérifié par ``opencode debug paths``) :
    un dossier du kit, vide de configuration, empêche une configuration utilisateur d'imposer un fournisseur ou un
    plugin au worker.
    """
    return root / "_grimoire-output" / "control" / "opencode-config"


def minimal_env(extra: Mapping[str, str], *, source: Mapping[str, str] | None = None) -> dict[str, str]:
    """L'environnement d'une commande opencode : la liste fermée :data:`ENV_ALLOWED`, puis *extra*.

    ``gh`` et ``git`` du worker gardent leur configuration réelle quand ``XDG_CONFIG_HOME`` est redirigé :
    ``GH_CONFIG_DIR`` pointe sur le dossier ``gh`` d'origine, et ``GIT_CONFIG_GLOBAL`` sur la configuration git XDG
    quand elle est la seule configuration globale.
    """
    environ = os.environ if source is None else source
    env = {k: environ[k] for k in ENV_ALLOWED if environ.get(k)}
    if "XDG_CONFIG_HOME" in extra:
        home = Path(environ.get("HOME") or Path.home())
        real = Path(environ.get("XDG_CONFIG_HOME") or home / ".config")
        env["GH_CONFIG_DIR"] = environ.get("GH_CONFIG_DIR") or str(real / "gh")
        git_xdg = real / "git" / "config"
        if git_xdg.is_file() and not (home / ".gitconfig").exists():
            env["GIT_CONFIG_GLOBAL"] = str(git_xdg)
    env.update(extra)
    return env


def expected_plugin(cwd: Path) -> str | None:
    """Le plugin de garde que l'émetteur opencode du kit produit pour *cwd* ; ``None`` s'il n'en produit pas."""
    from grimoire.hosts.collect import build_surface
    from grimoire.hosts.emitters.opencode import PLUGIN_RELPATH, OpenCodeEmitter

    try:
        plan = OpenCodeEmitter().plan(build_surface(cwd, write_cache=False), cwd)
    except Exception:  # une surface illisible ne prouve rien : pas de plugin attendu, le worker ne part pas
        return None
    return next((f.content for f in plan.files if f.relpath == PLUGIN_RELPATH), None)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _plugin_matches(path: Path, expected: str) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    try:
        return _digest(path.read_bytes()) == _digest(expected.encode("utf-8"))
    except OSError:
        return False


def _code_refusal(base: Path, allowed: Path | None) -> str:
    """Le premier fichier de code découvrable sous *base* (dossiers de plugins et d'outils) autre que *allowed*."""
    for name in _CODE_DIRS:
        folder = base / name
        if folder.is_symlink():
            return f"{folder.name}/ est un lien symbolique"
        if not folder.is_dir():
            continue
        for entry in sorted(folder.rglob("*")):
            if entry.is_symlink():
                return f"lien symbolique sous {name}/"
            if entry.is_file() and entry.suffix.lower() in CODE_SUFFIXES and (allowed is None or entry != allowed):
                return f"fichier de code non émis par le kit sous {name}/"
    return ""


def _package_refusal(base: Path) -> str:
    manifest = base / "package.json"
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return "manifeste de paquets illisible"
        deps = data.get("dependencies") if isinstance(data, dict) else None
        if set(data) - {"dependencies"} or not isinstance(deps, dict) or set(deps) - {_PLUGIN_PACKAGE}:
            return "manifeste de paquets qui déclare autre chose que le paquet de plugin d'opencode"
    if any((base / name).exists() for name in _INSTALLER_FILES):
        return "réglage de gestionnaire de paquets présent"
    return ""


def _schema_only(path: Path) -> bool:
    """Vrai si *path* est la configuration vide qu'opencode écrit lui-même (``{"$schema": …}`` seul)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and set(data) <= {"$schema"}


def _config_dir_refusal(base: Path, allowed: Path | None, *, empty_config_ok: bool = False) -> str:
    """Les règles communes à ``<worktree>/.opencode`` et au dossier de configuration du kit.

    *empty_config_ok* : dans le dossier de configuration du kit, opencode écrit lui-même un ``opencode.jsonc`` qui ne
    porte que ``$schema`` ; celui-là est admis, tout autre contenu est refusé.
    """
    if base.is_symlink():
        return f"{base.name} est un lien symbolique"
    if not base.exists():
        return ""
    for name in _CONFIG_FILES:
        found = base / name
        if found.is_symlink() or (found.exists() and not (empty_config_ok and _schema_only(found))):
            return f"fichier de configuration opencode présent dans {base.name}/"
    return _code_refusal(base, allowed) or _package_refusal(base)


def integrity_refusal(cwd: Path, *, config_home: Path | None = None) -> str:
    """``""`` si rien d'autre que le plugin de garde du kit ne peut être chargé par opencode dans *cwd* ; sinon le motif.

    Ne lance jamais opencode. Un plugin de garde absent ou modifié est réécrit depuis l'émetteur, puis revérifié.
    Les motifs sont fixes : ni contenu de fichier, ni sortie de programme.
    """
    from grimoire.core.fsio import atomic_write
    from grimoire.hosts.emitters.opencode import PLUGIN_RELPATH

    expected = expected_plugin(cwd)
    if expected is None:
        return "aucun plugin de garde opencode émis par le kit pour ce worktree (hook PreToolUse bloquant absent)"
    oc_dir = cwd / ".opencode"
    if oc_dir.is_symlink():
        return ".opencode est un lien symbolique"
    plugin = cwd / PLUGIN_RELPATH
    if not _plugin_matches(plugin, expected):
        if plugin.is_symlink():
            return "le plugin de garde est un lien symbolique"
        plugin.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(plugin, expected)
        if not _plugin_matches(plugin, expected):
            return "plugin de garde différent de la sortie de l'émetteur après réécriture"
    if any((cwd / name).exists() for name in _CONFIG_FILES[:2]):
        return "fichier de configuration opencode à la racine du worktree"
    if refusal := _config_dir_refusal(oc_dir, plugin):
        return refusal
    if config_home is not None and (
        refusal := _config_dir_refusal(config_home / "opencode", None, empty_config_ok=True)
    ):
        return f"configuration utilisateur du kit : {refusal}"
    return ""


def _plugin_text(cwd: Path) -> str:
    from grimoire.hosts.emitters.opencode import PLUGIN_RELPATH

    try:
        return (cwd / PLUGIN_RELPATH).read_text(encoding="utf-8")
    except OSError:
        return ""


def _json_decl(pattern: re.Pattern[str], text: str) -> object:
    match = pattern.search(text)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except ValueError:
        return None


def hook_program(cwd: Path) -> str:
    """Le programme que le plugin de garde de *cwd* appelle, lu dans le plugin (déjà vérifié à l'octet)."""
    decl = _json_decl(_HOOK_DECL, _plugin_text(cwd))
    posix = str(decl.get("posix") or "") if isinstance(decl, dict) else ""
    if posix and isinstance(decl, dict) and decl.get("windows"):
        return str(cwd / posix)  # chemin relatif à la racine du projet, comme ``hookProgram`` du plugin
    return posix


def hook_args(cwd: Path) -> tuple[str, ...]:
    """Les arguments que le plugin passe au programme de garde pour ``tool.execute.before``."""
    args = _json_decl(_GUARD_ARGS, _plugin_text(cwd))
    if isinstance(args, list) and args and all(isinstance(a, str) for a in args):
        return tuple(args)
    return _DEFAULT_ARGS


def guards_execution(cwd: Path) -> bool:
    """Vrai si le plugin de garde de *cwd* soumet l'exécution de commandes (``bash``) au programme de garde."""
    guarded = _json_decl(_GUARDED_DECL, _plugin_text(cwd))
    return isinstance(guarded, list) and "bash" in guarded


def hook_witness_refusal(cwd: Path, env: Mapping[str, str], runner: Runner) -> str:
    """``""`` si le programme de garde refuse la charge témoin ; sinon pourquoi le worker ne part pas.

    Même entrée que celle que le plugin envoie (``PreToolUse``, outil ``bash``) ; la commande témoin n'est jamais
    exécutée. Programme introuvable, sortie illisible ou décision qui laisse passer : refus, quel que soit le profil.
    """
    program = hook_program(cwd)
    if not program:
        return "programme de garde introuvable dans le plugin"
    payload = json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "bash",
            "tool_input": {"command": _WITNESS_COMMAND},
            "cwd": str(cwd),
            "session_id": "grimoire-witness",
            "tool_use_id": "grimoire-witness",
        }
    )
    if not guards_execution(cwd):
        return "le plugin de garde ne soumet pas l'exécution de commandes au programme de garde"
    done = runner([program, *hook_args(cwd)], cwd, env, payload)
    if done.returncode != 0:
        return f"programme de garde indisponible (sortie {done.returncode})"
    decision = ""
    with contextlib.suppress(ValueError, AttributeError):
        decision = str(json.loads(done.stdout or "{}").get("hookSpecificOutput", {}).get("permissionDecision") or "")
    if decision not in ("deny", "ask"):
        return "le programme de garde ne refuse pas la charge témoin"
    return ""
