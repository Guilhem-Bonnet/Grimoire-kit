"""Ajout au journal du ledger en O(1) : ``open('ab')``, un seul ``write``, ``fsync``.

Le format du journal ne change pas d'un octet ; seul le coût de l'ajout cesse de
dépendre de la taille du fichier (plus de relecture ni de réécriture complète).
L'appelant tient le verrou du journal (``_ledger_file_lock``).
"""

from __future__ import annotations

import os
from contextlib import suppress
from pathlib import Path

#: (taille, mtime_ns, inode) d'un fichier ; ``None`` s'il n'existe pas.
FileSig = tuple[int, int, int] | None


def file_sig(path: Path) -> FileSig:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns, st.st_ino)


def append_bytes(path: Path, payload: bytes) -> tuple[FileSig, FileSig]:
    """Ajoute *payload* (une ligne terminée par ``\\n``) à *path*.

    Rend l'empreinte du fichier juste avant puis juste après l'écriture ;
    « avant » est ``None`` si la fin tronquée a dû être refermée par un saut de
    ligne (K12 : l'état en mémoire ne suit pas cette réparation).
    """
    created = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as fh:
        st = os.fstat(fh.fileno())
        before: FileSig = (st.st_size, st.st_mtime_ns, st.st_ino)
        if st.st_size:
            with open(path, "rb") as probe:
                probe.seek(-1, os.SEEK_END)
                if probe.read(1) != b"\n":
                    payload = b"\n" + payload
                    before = None
        fh.write(payload)
        fh.flush()
        # K2 (§2.3) : ajout puis ``fsync`` du fichier ; le dossier à la création.
        os.fsync(fh.fileno())
        st = os.fstat(fh.fileno())
    if created and os.name == "posix":
        with suppress(OSError):
            dfd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
    return before, (st.st_size, st.st_mtime_ns, st.st_ino)
