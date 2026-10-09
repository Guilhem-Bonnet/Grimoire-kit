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
import tempfile
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


def _read_umask() -> int:
    old = os.umask(0)
    os.umask(old)
    return old


#: Lu une seule fois au chargement (``os.umask`` n'a pas de lecture sans écriture, donc pas de course tardive).
_UMASK = _read_umask()


def _lock_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.lock")


def _pid_dead(content: str) -> bool:
    """Vrai si le verrou nomme un pid POSIX qui n'existe plus (contenu illisible : faux)."""
    if os.name != "posix":
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
        content = lock.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    abandoned = time.time() - before.st_mtime > _STALE_AFTER_S or _pid_dead(content)
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
        # Ne supprime que son propre verrou : s'il a été cassé puis repris, ce n'est plus le nôtre.
        with contextlib.suppress(OSError):
            if lock.read_text(encoding="utf-8") == token:
                lock.unlink()


def atomic_write(path: str | os.PathLike[str], data: str | bytes) -> None:
    """Remplace *path* par *data* de façon atomique (temporaire, ``fsync``, ``os.replace``)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = data.encode("utf-8") if isinstance(data, str) else data
    try:  # le mode d'une cible existante est conservé ; un fichier neuf suit l'umask
        mode = stat.S_IMODE(target.stat().st_mode)
    except OSError:
        mode = 0o666 & ~_UMASK
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            if hasattr(os, "fchmod"):
                os.fchmod(fh.fileno(), mode)
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        Path(tmp_name).replace(target)
    except BaseException:
        with contextlib.suppress(OSError):
            Path(tmp_name).unlink()
        raise
    if os.name == "posix":  # rendre le renommage durable ; best-effort
        with contextlib.suppress(OSError):
            dfd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
