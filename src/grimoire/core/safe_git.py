"""Le seul ``git`` que le tick lance : rien de ce que le worker écrit n'y est exécuté (#876, lot 4, revue de #927).

Un worker écrit son worktree, et aussi — c'est le même utilisateur — la configuration commune du dépôt
(``git config --local`` depuis le worktree écrit ``<common>/config``) et le fichier ``<worktree>/.git``. Un ``git`` lancé
par le tick sans précaution exécuterait alors son code : ``core.fsmonitor``, hooks, ``gpg.program``, ``core.sshCommand``,
``filter.*``, pilotes de fusion, ``diff.external``, ``credential.helper``, ``remote.*.uploadpack``… Le tick s'accorderait un
mandat lui-même. Ici :

- :data:`SAFE_ARGS` neutralise par ``-c`` ce qui exécute (hooks, fsmonitor, signatures, ssh, pager, diff externe,
  assistants d'identifiants, protocoles ``ext``/``fd``, maintenance automatique) ; :func:`safe_env` neutralise la
  configuration globale et système (``GIT_CONFIG_GLOBAL``/``GIT_CONFIG_SYSTEM`` vers ``os.devnull``,
  ``GIT_CONFIG_NOSYSTEM``) et retire les variables qui détourneraient git ou l'interrogeraient ;
- **dans le tick** (:func:`tick_scope`), chaque appel est en plus confiné : ``--git-dir`` et ``--work-tree`` sont dérivés
  du dépôt du projet, jamais lus dans ``<worktree>/.git`` ; un worktree n'est accepté que si ``<wt>/.git`` renvoie vers
  ``<home>/.git/worktrees/<nom>`` et que ``commondir`` et ``gitdir`` y sont réciproques (:func:`worktree_git_dir`) ; et la
  configuration locale du dépôt ne doit contenir que des clés d'une liste blanche (:func:`config_refusal`) — un filtre,
  un pilote, un ``include``, un alias, une URL de réécriture ou un assistant d'identifiants y fait tout refuser.

Hors du tick (CLI d'un humain ou d'une session), :data:`SAFE_ARGS` et :func:`safe_env` s'appliquent aussi, sans
confinement ni liste blanche : un dépôt d'utilisateur garde ses filtres (LFS…) pour ses propres commandes.

**Limites** : la liste blanche est relue à chaque appel mais pas verrouillée — une configuration réécrite entre la
lecture et l'appel passe le temps d'un appel, et ce qui n'est pas neutralisé par ``-c`` (un filtre) peut alors tourner ;
l'URL de ``origin`` est une clé permise : le worker peut la rediriger (pas d'exécution, protocoles ``ext`` et ``fd``
refusés).
"""

from __future__ import annotations

import contextlib
import contextvars
import os
import re
import subprocess
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "PUSH_ARGS",
    "SAFE_ARGS",
    "TickRepo",
    "UnsafeRepoError",
    "config_refusal",
    "identity_env",
    "refusal_for",
    "run",
    "run_bytes",
    "safe_env",
    "tick_repo",
    "tick_scope",
    "worktree_git_dir",
]

#: Tout ce qu'une configuration pourrait faire exécuter à un ``git`` qui lit, compare, fusionne en plomberie ou pousse.
SAFE_ARGS: tuple[str, ...] = (
    "-c", f"core.hooksPath={os.devnull}", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
    "-c", "commit.gpgSign=false", "-c", "tag.gpgSign=false", "-c", "push.gpgSign=false",
    "-c", "log.showSignature=false", "-c", "merge.verifySignatures=false",
    "-c", "core.sshCommand=ssh", "-c", "core.pager=cat", "-c", "diff.external=", "-c", "core.askPass=",
    "-c", "credential.helper=", "-c", "protocol.ext.allow=never", "-c", "protocol.fd.allow=never",
    "-c", "gc.auto=0", "-c", "maintenance.auto=false", "-c", f"core.attributesFile={os.devnull}",
    "-c", f"core.excludesFile={os.devnull}",
)
#: Les variables qui détourneraient git (autre dépôt, autre configuration) ou lui feraient lancer un programme.
_DROPPED_ENV = frozenset({
    "GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_PREFIX", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT", "GIT_EXTERNAL_DIFF", "GIT_PAGER", "GIT_ASKPASS", "SSH_ASKPASS", "GIT_EDITOR",
    "GIT_SEQUENCE_EDITOR", "GIT_EXEC_PATH", "GIT_TEMPLATE_DIR", "GIT_CEILING_DIRECTORIES", "GIT_DISCOVERY_ACROSS_FILESYSTEM",
})
#: Clés de configuration locale permises (en minuscules) : identité, remotes, suivi des branches, format du dépôt.
_ALLOWED = tuple(re.compile(p) for p in (
    r"core\.(repositoryformatversion|filemode|bare|logallrefupdates|ignorecase|precomposeunicode|symlinks|autocrlf|eol|safecrlf|longpaths)",
    r"extensions\.(objectformat|refstorage)",
    r"remote\..+\.(url|fetch|gh-resolved|tagopt|prune)",
    r"branch\..+\.(remote|merge|rebase|vscode-merge-base|github-pr-owner-number|description|pushremote)",
    r"user\.(name|email)",
    r"(init\.defaultbranch|fetch\.prune|pull\.rebase|pull\.ff|push\.default|push\.autosetupremote|commit\.gpgsign|gc\.auto)",
))
_TIMEOUT_S = 120.0
#: Pour pousser vers GitHub : l'assistant d'identifiants de ``gh``, jamais celui que la configuration du dépôt désigne.
PUSH_ARGS: tuple[str, ...] = ("-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential")


class UnsafeRepoError(RuntimeError):
    """Le dépôt, ou le worktree, n'est pas celui qu'il prétend, ou sa configuration fait exécuter du code."""


def safe_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _DROPPED_ENV and not k.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_", "GIT_TRACE"))}
    env.update({
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "GIT_PAGER": "cat",
    })
    env.update(extra or {})
    return env


@dataclass(frozen=True, slots=True)
class TickRepo:
    """Le dépôt du projet tel que le tick le connaît : sa racine et son dossier git commun (jamais un fichier ``.git``)."""

    home: Path
    common: Path


_TICK: contextvars.ContextVar[TickRepo | None] = contextvars.ContextVar("grimoire_tick_repo", default=None)


def _resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


def tick_repo() -> TickRepo | None:
    return _TICK.get()


@contextlib.contextmanager
def tick_scope(home: Path) -> Iterator[TickRepo | None]:
    """Le temps d'un tick : chaque ``git`` est confiné au dépôt *home* et à ses worktrees vérifiés, sous liste blanche.

    *home* sans dossier ``.git`` (pas un dépôt, ou un worktree lui-même) : aucun git n'est permis — les appels échouent.
    """
    root = _resolve(home)
    common = root / ".git"
    repo = TickRepo(root, common) if common.is_dir() and not common.is_symlink() else TickRepo(root, Path())
    token = _TICK.set(repo)
    try:
        yield repo
    finally:
        _TICK.reset(token)


def config_refusal(common: Path) -> str:
    """``""`` si la configuration locale du dépôt ne porte que des clés permises ; sinon ce qui est refusé."""
    path = common / "config"
    try:
        done = subprocess.run(  # lecture du fichier seul : --file ne suit pas les include
            ["git", "config", "--file", str(path), "--list", "--null", "--name-only"], capture_output=True,
            timeout=30, check=False, env=safe_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"configuration du dépôt illisible ({type(exc).__name__})"
    if done.returncode != 0:
        return "configuration du dépôt illisible"
    keys = [k.decode("utf-8", "replace").strip().casefold() for k in done.stdout.split(b"\0") if k.strip()]
    refused = sorted({k for k in keys if not any(p.fullmatch(k) for p in _ALLOWED)})
    if refused:
        return f"configuration locale du dépôt hors liste blanche : {', '.join(refused[:8])}"
    return ""


def worktree_git_dir(repo: TickRepo, work_tree: Path) -> tuple[Path | None, str]:
    """Le dossier git de *work_tree*, s'il est bien un worktree de *repo* : ``(dossier, "")`` ou ``(None, raison)``.

    ``<wt>/.git`` est un fichier (pas un lien) qui renvoie vers ``<common>/worktrees/<nom>`` ; ce dossier renvoie vers
    ``<common>`` (``commondir``) et vers ``<wt>/.git`` (``gitdir``).
    """
    wt = _resolve(work_tree)
    marker = wt / ".git"
    if marker.is_symlink() or not marker.is_file():
        return None, f"{wt} n'est pas un worktree lié (pas de fichier .git)"
    try:
        first = marker.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, UnicodeDecodeError, IndexError):
        return None, f"{marker} illisible"
    if not first.startswith("gitdir:"):
        return None, f"{marker} ne désigne aucun dossier git"
    raw = Path(first[len("gitdir:"):].strip())
    gitdir = _resolve(raw if raw.is_absolute() else wt / raw)
    if gitdir.parent != _resolve(repo.common / "worktrees"):
        return None, f"{wt} renvoie vers {gitdir}, hors des worktrees du dépôt du projet"
    try:
        common_ref = (gitdir / "commondir").read_text(encoding="utf-8").strip()
        back_ref = (gitdir / "gitdir").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None, f"{gitdir} incomplet (commondir, gitdir)"
    common_path = Path(common_ref)
    if _resolve(common_path if common_path.is_absolute() else gitdir / common_path) != _resolve(repo.common):
        return None, f"{gitdir}/commondir ne renvoie pas au dépôt du projet"
    if _resolve(Path(back_ref)) != _resolve(marker):
        return None, f"{gitdir}/gitdir ne renvoie pas à {marker}"
    return gitdir, ""


def _confine(cwd: Path | None) -> tuple[list[str], Path | None]:
    """``(options, dossier courant)`` : dans le tick, ``--git-dir``/``--work-tree`` dérivées du dépôt du projet ; lève sinon."""
    repo = _TICK.get()
    if repo is None:
        return (["-C", str(cwd)] if cwd is not None else []), None
    if not repo.common.parts or not repo.common.is_dir():
        raise UnsafeRepoError(f"{repo.home} n'a pas de dossier .git : aucun git n'est lancé par le tick")
    refused = config_refusal(repo.common)
    if refused:
        raise UnsafeRepoError(refused)
    where = _resolve(cwd) if cwd is not None else repo.home
    if where == repo.home:
        return [f"--git-dir={repo.common}", f"--work-tree={repo.home}"], repo.home
    gitdir, why = worktree_git_dir(repo, where)
    if gitdir is None:
        raise UnsafeRepoError(why)
    return [f"--git-dir={gitdir}", f"--work-tree={where}"], where


def run_bytes(
    args: tuple[str, ...] | list[str], *, cwd: Path | None = None, timeout: float = _TIMEOUT_S,
    input_bytes: bytes | None = None, extra_args: tuple[str, ...] = (), env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """``git`` durci, sorties en octets ; jamais d'exception : un refus ou une panne rend un code non nul et la raison."""
    try:
        where, inside = _confine(cwd)
    except UnsafeRepoError as exc:
        return subprocess.CompletedProcess(["git", *args], 128, b"", f"dépôt refusé par le tick : {exc}".encode())
    argv = ["git", *SAFE_ARGS, *extra_args, *where, *args]
    try:
        return subprocess.run(  # arguments fixes, sans shell
            argv, cwd=inside, capture_output=True, timeout=timeout, check=False, env=safe_env(env), input=input_bytes,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(argv, 124, b"", f"git {args[0] if args else ''} : {type(exc).__name__}: {exc}".encode())


def run(
    args: tuple[str, ...] | list[str], *, cwd: Path | None = None, timeout: float = _TIMEOUT_S,
    input_bytes: bytes | None = None, extra_args: tuple[str, ...] = (), env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """``git`` durci (voir le module), sorties en texte UTF-8."""
    done = run_bytes(args, cwd=cwd, timeout=timeout, input_bytes=input_bytes, extra_args=extra_args, env=env)
    return subprocess.CompletedProcess(
        done.args, done.returncode, done.stdout.decode("utf-8", "replace"), done.stderr.decode("utf-8", "replace"),
    )


def identity_env(cwd: Path | None = None) -> dict[str, str]:
    """L'auteur d'un commit fait par le kit : ``user.name``/``user.email`` de la configuration locale, sinon le kit."""
    name = run(("config", "--get", "user.name"), cwd=cwd).stdout
    email = run(("config", "--get", "user.email"), cwd=cwd).stdout
    who = str(name).strip() or "Grimoire"
    mail = str(email).strip() or "grimoire@localhost"
    return {"GIT_AUTHOR_NAME": who, "GIT_AUTHOR_EMAIL": mail, "GIT_COMMITTER_NAME": who, "GIT_COMMITTER_EMAIL": mail}


def refusal_for(path: Path) -> str:
    """Dans le tick : pourquoi un ``git`` sur *path* serait refusé (configuration, worktree non lié) ; ``""`` sinon."""
    try:
        _confine(path)
    except UnsafeRepoError as exc:
        return str(exc)
    return ""
