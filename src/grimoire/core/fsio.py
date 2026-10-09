"""Verrou inter-processus portable et écriture atomique — l'unique implémentation.

``locked(path)`` donne l'exclusion mutuelle sur *path* : ``flock`` quand
``fcntl`` existe, sinon création exclusive d'un fichier verrou (Windows).
``atomic_write(path, data)`` écrit un temporaire voisin, le synchronise sur
disque, puis le renomme par-dessus : un lecteur voit l'ancien contenu ou le
nouveau, jamais un fichier tronqué, et une panne avant le renommage laisse
l'ancien fichier intact.

Contrairement aux anciens helpers (``missions.ledger._ledger_file_lock``,
``tools.stigmergy._board_lock``), une impossibilité de verrouiller n'est pas
avalée : elle lève, plutôt que de laisser croire à une exclusion inexistante.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import time
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


def _lock_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.lock")


@contextmanager
def locked(path: str | os.PathLike[str], *, timeout: float = 30.0) -> Iterator[None]:
    """Verrou exclusif inter-processus associé à *path* (le fichier lui-même n'est pas touché).

    Lève ``TimeoutError`` si le verrou n'est pas obtenu en *timeout* secondes
    (repli sans ``fcntl`` uniquement : ``flock`` attend sans borne).
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = _lock_path(target)
    if _fcntl is not None:
        with lock.open("a", encoding="utf-8") as fh:
            _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX)
            try:
                yield
            finally:
                _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)
        return

    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            with contextlib.suppress(OSError):
                if time.time() - lock.stat().st_mtime > _STALE_AFTER_S:
                    lock.unlink()
                    continue
            if time.monotonic() >= deadline:
                raise TimeoutError(f"verrou {lock} non obtenu en {timeout:g} s") from None
            time.sleep(_POLL_S)
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            lock.unlink()


def atomic_write(path: str | os.PathLike[str], data: str | bytes) -> None:
    """Remplace *path* par *data* de façon atomique (temporaire, ``fsync``, ``os.replace``)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = data.encode("utf-8") if isinstance(data, str) else data
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
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
