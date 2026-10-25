"""Les ``git`` du tick, quand un rôle autonome est activé : ce que la configuration du dépôt ferait exécuter est neutralisé.

Un worker écrit son worktree, et aussi — c'est le même utilisateur — la configuration du dépôt (``git config --local``
depuis le worktree écrit ``<common>/config``). Un ``git`` lancé par le tick y lirait des commandes à exécuter : hooks,
``core.fsmonitor``, filtres ``clean``/``smudge``, pilotes de fusion, signatures (``gpg.program``), ``core.sshCommand``,
assistant d'identifiants. Le temps d'un tick qui pilote un rôle autonome (:func:`tick_scope`), ces clés sont
neutralisées pour **tous** les git que le processus lance, par l'environnement (``GIT_CONFIG_COUNT``,
``GIT_CONFIG_KEY_<n>``, ``GIT_CONFIG_VALUE_<n>`` : l'équivalent de ``git -c``, qui prime sur les fichiers) :

- toujours : ``core.hooksPath`` vers ``os.devnull``, ``core.fsmonitor=false``, signatures coupées (``commit``, ``tag``,
  ``push``, ``log.showSignature``, ``merge.verifySignatures``) ;
- seulement si le dépôt ou l'un de ses worktrees les déclare **localement** : chaque ``filter.<nom>.clean|smudge|process``
  et chaque ``merge.<nom>.driver`` vidés, ``core.sshCommand`` remis à la valeur globale de l'utilisateur (à défaut
  ``ssh``), ``credential.helper`` remis aux assistants globaux de l'utilisateur.

Rien n'est refusé pour une configuration ordinaire ; l'identité et les identifiants **globaux** de l'utilisateur ne sont
pas touchés (ce sont des réglages du poste, pas du dépôt du worker). Les sessions ``claude`` que le tick lance ne
reçoivent pas ces variables (:func:`session_environ`). Hors d'un tel tick, rien ne change.

**Limites** : la neutralisation est calculée à l'entrée du tick ; une clé posée par le worker pendant le tick tourne le
temps de ce tick (sauf hooks, fsmonitor et signatures, toujours coupés). Les pilotes de diff (``diff.<nom>.command``,
``textconv``, ``diff.external``) ne sont pas neutralisés : un pilote vide casse ``git diff``, et le tick n'en lance pas.
Les sessions tournent sous le même utilisateur du système : un processus qui écrit ce que le tick lit avant lui (le
journal d'autorité, le ledger) contourne une garde de fichier ; seule une isolation par utilisateur du système la
fermerait.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
import re
import subprocess
from collections.abc import Iterator, Mapping
from pathlib import Path

__all__ = ["git_env", "hardened", "hardening", "same_repo", "session_environ", "tick_scope"]

#: Toujours neutralisé dans un tick qui pilote un rôle autonome.
_ALWAYS: tuple[tuple[str, str], ...] = (
    ("core.hooksPath", os.devnull), ("core.fsmonitor", "false"), ("commit.gpgSign", "false"), ("tag.gpgSign", "false"),
    ("push.gpgSign", "false"), ("log.showSignature", "false"), ("merge.verifySignatures", "false"),
)
_DRIVER = re.compile(r"(filter\.(.+)\.(clean|smudge|process)|merge\.(.+)\.driver)")
#: Les variables qui détourneraient git vers un autre dépôt (un hook git les exporte).
_REDIRECT = frozenset({
    "GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_PREFIX",
})
_ACTIVE: contextvars.ContextVar[bool] = contextvars.ContextVar("grimoire_tick_hardened", default=False)
#: Ce que le tick a posé dans l'environnement, et le ``GIT_CONFIG_COUNT`` d'avant : ce qu'une session ne reçoit pas.
_OURS: dict[str, str | None] = {}


def git_env() -> dict[str, str]:
    """L'environnement d'un git du kit : celui du processus, sans les variables qui détourneraient git."""
    return {k: v for k, v in os.environ.items() if k not in _REDIRECT}


def _git(cwd: Path, *args: str) -> str:
    try:
        done = subprocess.run(  # arguments fixes, sans shell ; lire la configuration n'exécute rien
            ["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30, check=False, env=git_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout if done.returncode == 0 else ""


def _local_keys(home: Path) -> set[str]:
    """Les clés que le dépôt et ses worktrees déclarent eux-mêmes (portées ``local`` et ``worktree``), en minuscules."""
    roots = [home]
    for line in _git(home, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            roots.append(Path(line[len("worktree "):]))
    keys: set[str] = set()
    for root in roots:
        for entry in _git(root, "config", "--list", "--show-scope", "--null", "--name-only").split("\0"):
            scope, _, key = entry.strip().partition("\t")
            if scope in ("local", "worktree") and key:
                keys.add(key.strip())
    return keys


def hardening(home: Path) -> list[tuple[str, str]]:
    """Les paires ``(clé, valeur)`` qui neutralisent ce que la configuration du dépôt *home* ferait exécuter."""
    pairs = list(_ALWAYS)
    local = _local_keys(home)
    for key in sorted(local):
        if _DRIVER.fullmatch(key.casefold()):
            pairs.append((key, ""))  # un filtre ou un pilote vide n'est pas lancé (le contenu passe tel quel)
    folded = {k.casefold() for k in local}
    if "core.sshcommand" in folded:
        pairs.append(("core.sshCommand", _git(home, "config", "--global", "--get", "core.sshCommand").strip() or "ssh"))
    for key in sorted(k for k in local if k.casefold() == "credential.helper" or re.fullmatch(r"credential\..+\.helper", k.casefold())):
        pairs.append((key, ""))  # vide la liste, puis remet les assistants globaux de l'utilisateur pour cette clé
        pairs.extend((key, h) for h in _git(home, "config", "--global", "--get-all", key).splitlines() if h)
    return pairs


def hardened() -> bool:
    """Vrai le temps d'un tick qui pilote un rôle autonome."""
    return _ACTIVE.get()


@contextlib.contextmanager
def tick_scope(home: Path, *, active: bool) -> Iterator[None]:
    """Le temps du tick : si *active* (un rôle autonome est activé), chaque git lancé par le processus est neutralisé.

    Inactif, rien ne change : les git du kit sont ceux de toujours.
    """
    if not active or _ACTIVE.get():
        yield
        return
    pairs = hardening(home)
    before = os.environ.get("GIT_CONFIG_COUNT")
    base = int(before) if before and before.isdigit() else 0
    added = {"GIT_CONFIG_COUNT": str(base + len(pairs))}
    for i, (key, value) in enumerate(pairs, start=base):
        added[f"GIT_CONFIG_KEY_{i}"] = key
        added[f"GIT_CONFIG_VALUE_{i}"] = value
    saved = {k: os.environ.get(k) for k in added}
    os.environ.update(added)
    _OURS.clear()
    _OURS.update({k: saved[k] for k in added})
    token = _ACTIVE.set(True)
    try:
        yield
    finally:
        _ACTIVE.reset(token)
        for name, previous in saved.items():
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous
        _OURS.clear()


def session_environ(extra: Mapping[str, str] | None) -> dict[str, str] | None:
    """L'environnement d'une session lancée par le tick : celui du processus, sans la neutralisation du tick.

    Hors d'un tick durci : ``{**os.environ, **extra}`` (ou ``None`` sans *extra*), comme avant.
    """
    if not _ACTIVE.get():
        return {**os.environ, **extra} if extra else None
    env = dict(os.environ)
    for key, value in _OURS.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return {**env, **(extra or {})}


def same_repo(home: Path, work_tree: Path) -> str:
    """``""`` si *work_tree* partage le dossier git commun de *home* ; sinon pourquoi il est refusé."""
    def common(path: Path) -> str:
        found = _git(path, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
        return str(Path(found).resolve()) if found else ""

    mine, theirs = common(home), common(work_tree)
    if not mine or not theirs:
        return f"{work_tree} : dossier git illisible"
    if mine != theirs:
        return f"{work_tree} n'est pas un worktree du dépôt du projet ({theirs})"
    return ""
