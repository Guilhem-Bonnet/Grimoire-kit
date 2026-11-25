"""Le dossier des enregistrements entrants, sous le dossier d'exécution du projet (#1343 V5).

Un enregistrement n'existe sur disque que le temps de sa transcription : écrit en 0600 dans
``_grimoire-runtime-output/voice/incoming/`` (dossier rendu ignoré par git par son propre ``.gitignore``),
supprimé juste après, y compris en cas d'échec. Un arrêt brutal peut en laisser un : :func:`purge` supprime les
fichiers au motif ``grimoire-stt-*.wav`` plus vieux que ``voice.incoming_ttl_seconds``, au démarrage du service
vocal (``voice/config``) et à chaque transcription. La purge ne suit aucun lien symbolique et ne touche à rien
d'autre que des fichiers ordinaires au motif attendu, dans ce seul dossier.
"""

from __future__ import annotations

import contextlib
import os
import re
import stat
import tempfile
import time
from collections.abc import Callable, Iterator
from pathlib import Path

__all__ = ["INCOMING_RELPATH", "PREFIX", "SUFFIX", "incoming_dir", "purge", "recording"]

INCOMING_RELPATH = Path("_grimoire-runtime-output") / "voice" / "incoming"
PREFIX = "grimoire-stt-"
SUFFIX = ".wav"
_NAME = re.compile(rf"{re.escape(PREFIX)}[A-Za-z0-9_]+{re.escape(SUFFIX)}")
_IGNORE_ALL = "# Enregistrements vocaux entrants, éphémères : jamais versionnés.\n*\n"


def _refuse_links(root: Path, path: Path) -> None:
    """Refuse un dossier vocal dont un élément (sous la racine du projet) est un lien symbolique."""
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        with contextlib.suppress(FileNotFoundError):
            if stat.S_ISLNK(os.lstat(current).st_mode):
                raise ValueError(f"dossier vocal refusé : {current.relative_to(root)} est un lien symbolique")


def incoming_dir(root: Path) -> Path:
    """Le dossier des enregistrements entrants, créé au besoin (0700), avec son ``.gitignore`` qui ignore tout."""
    path = root / INCOMING_RELPATH
    _refuse_links(root, path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    _refuse_links(root, path)  # créé entre-temps par un autre
    ignore = path / ".gitignore"
    if not ignore.exists() and not ignore.is_symlink():
        with contextlib.suppress(OSError):
            ignore.write_text(_IGNORE_ALL, encoding="utf-8")
    return path


def purge(root: Path, ttl_s: float, *, clock: Callable[[], float] = time.time) -> int:
    """Supprime les enregistrements orphelins plus vieux que ``ttl_s`` ; rend leur nombre. Au mieux, jamais bloquant."""
    path = root / INCOMING_RELPATH
    try:
        _refuse_links(root, path)
        entries = list(os.scandir(path))
    except (OSError, ValueError):
        return 0
    removed = 0
    now = clock()
    for entry in entries:
        if not _NAME.fullmatch(entry.name):
            continue
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode) or now - info.st_mtime < ttl_s:
            continue  # lien symbolique, dossier, fichier récent : laissé tel quel
        with contextlib.suppress(OSError):
            Path(entry.path).unlink()  # le lien symbolique n'atteint jamais ici (S_ISREG sans le suivre)
            removed += 1
    return removed


@contextlib.contextmanager
def recording(root: Path, data: bytes) -> Iterator[Path]:
    """Écrit ``data`` dans un fichier 0600 du dossier entrant, le rend, puis le supprime quoi qu'il arrive."""
    fd, name = tempfile.mkstemp(prefix=PREFIX, suffix=SUFFIX, dir=incoming_dir(root))
    path = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        yield path
    finally:
        path.unlink(missing_ok=True)
