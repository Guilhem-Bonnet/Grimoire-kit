"""Verrou inter-processus portable et écriture atomique — implémentation de référence.

``locked(path)`` donne l'exclusion mutuelle sur *path* : ``flock`` quand
``fcntl`` existe, sinon création exclusive d'un fichier verrou (Windows).
``atomic_write(path, data)`` écrit un temporaire voisin, le synchronise sur
disque, puis le renomme par-dessus : un lecteur voit l'ancien contenu ou le
nouveau, jamais un fichier tronqué, et une panne avant le renommage laisse
l'ancien fichier intact.

Contrairement aux anciens helpers (``missions.ledger._ledger_file_lock``,
``tools.stigmergy._board_lock``), une impossibilité de verrouiller n'est pas
avalée : elle lève, plutôt que de laisser croire à une exclusion inexistante.

Ce module est la référence, pas encore l'unique implémentation : les copies
héritées (verrous et écritures « temporaire + replace ») sont listées dans
``tests/unit/test_fsio.py`` (``_LEGACY_LOCKERS``, ``_LEGACY_ATOMIC_WRITERS``),
des listes qui ne font que rétrécir ; toute nouvelle copie fait échouer le test.
"""

from __future__ import annotations

import contextlib
import os
import stat
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

try:  # POSIX
    import fcntl as _fcntl
except ImportError:  # Windows
    _fcntl = None  # type: ignore[assignment]

__all__ = ["atomic_write", "locked"]

#: Un verrou de repli plus vieux que cela est tenu pour abandonné (processus tué).
_STALE_AFTER_S = 60.0
_POLL_S = 0.01


#: Attente entre deux tentatives de libération refusée (Windows : un lecteur concurrent tient le fichier).
_RELEASE_DELAY_S = 0.05
_RELEASE_TRIES = 20
#: Le test de pid n'existe qu'en POSIX ; ailleurs on ne relit pas un verrou frais (la lecture gêne le ``unlink`` du porteur).
_PID_LIVENESS = os.name == "posix"


def _lock_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.lock")


def _pid_dead(content: str) -> bool:
    """Vrai si le verrou nomme un pid POSIX qui n'existe plus (contenu illisible : faux)."""
    if not _PID_LIVENESS:
        return False
    try:
        pid = int(content.split(":", 1)[0])
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except (ValueError, OSError):
        return False
    return False


def _break_if_abandoned(lock: Path) -> bool:
    """Casse *lock* s'il est abandonné ; vrai si on a cassé quelque chose.

    Jamais de ``unlink`` direct après un simple ``stat`` : le verrou est d'abord
    renommé hors du chemin partagé, puis on vérifie que c'est bien le fichier jugé
    abandonné (même inode, même mtime). Sinon on a pris le verrou neuf d'un autre :
    on le remet en place.
    """
    try:
        before = lock.stat()
        abandoned = time.time() - before.st_mtime > _STALE_AFTER_S
        if not abandoned and _PID_LIVENESS:  # sinon la lecture ne servirait à rien
            abandoned = _pid_dead(lock.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return False
    if not abandoned:
        return False
    grave = lock.with_name(f"{lock.name}.stale.{os.getpid()}.{uuid.uuid4().hex}")
    try:
        lock.rename(grave)
    except OSError:
        return False
    try:
        after = grave.stat()
        if (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns):
            return True
        # Pas celui qu'on a jugé : verrou neuf d'un autre, on le restitue (échoue si recréé entre-temps).
        with contextlib.suppress(OSError):
            os.link(grave, lock)
        return False
    finally:
        with contextlib.suppress(OSError):
            grave.unlink()


def _release(lock: Path, token: str) -> None:
    """Supprime *lock* s'il porte encore *token* (cassé puis repris par un autre : ce n'est plus le nôtre).

    Un refus transitoire (``PermissionError``, partage Windows) est retenté ; un échec
    persistant lève, plutôt que de laisser un verrou orphelin que tous attendraient.
    """
    for attempt in range(_RELEASE_TRIES):
        try:
            if lock.read_text(encoding="utf-8") != token:
                return
            lock.unlink()
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if attempt == _RELEASE_TRIES - 1:
                raise
            time.sleep(_RELEASE_DELAY_S)


@contextmanager
def locked(path: str | os.PathLike[str], *, timeout: float = 30.0) -> Iterator[None]:
    """Verrou exclusif inter-processus associé à *path* (le fichier lui-même n'est pas touché).

    Lève ``TimeoutError`` si le verrou n'est pas obtenu en *timeout* secondes,
    avec ou sans ``fcntl``.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = _lock_path(target)
    deadline = time.monotonic() + timeout
    if sys.platform != "win32" and _fcntl is not None:  # le test de plateforme sert mypy --platform win32
        with lock.open("a", encoding="utf-8") as fh:
            while True:
                try:
                    _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"verrou {lock} non obtenu en {timeout:g} s") from None
                    time.sleep(_POLL_S)
            try:
                yield
            finally:
                _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)
        return

    token = f"{os.getpid()}:{uuid.uuid4().hex}"
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except (FileExistsError, PermissionError):  # Windows : PermissionError si le verrou est en cours de suppression
            if _break_if_abandoned(lock):
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError(f"verrou {lock} non obtenu en {timeout:g} s") from None
            time.sleep(_POLL_S)
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(token)
        break
    try:
        yield
    finally:
        _release(lock, token)


def atomic_write(path: str | os.PathLike[str], data: str | bytes) -> None:
    """Remplace *path* par *data* de façon atomique (temporaire, ``fsync``, ``os.replace``).

    Le mode d'une cible existante est conservé ; un fichier neuf reçoit 0o666 filtré par
    l'umask *courant* du noyau (``O_CREAT``), sans jamais lire ni modifier l'umask du processus.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = data.encode("utf-8") if isinstance(data, str) else data
    try:
        mode: int | None = stat.S_IMODE(target.stat().st_mode)
    except OSError:
        mode = None
    tmp = target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o666)
    try:
        with os.fdopen(fd, "wb") as fh:
            if mode is not None and hasattr(os, "fchmod"):
                os.fchmod(fh.fileno(), mode)
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        tmp.replace(target)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    if os.name == "posix":  # rendre le renommage durable ; best-effort
        with contextlib.suppress(OSError):
            dfd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
