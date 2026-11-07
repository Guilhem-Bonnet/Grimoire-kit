"""Index incrémental des traces par tâche — relire les ajouts, jamais tout le journal.

``_grimoire-output/traces/traces.jsonl`` pèse plusieurs mégaoctets : le
relire et le parser en entier à chaque lecture du board (0,5 s mesurée sur
11 Mo à l'audit du 2026-11-07) ne fait que regrouper des traces par
``task_id``. Cet index garde ce regroupement en mémoire et ne consomme que
les octets ajoutés depuis la lecture précédente.

Contrat :

- l'état est indexé par ``(mtime_ns, size)`` : sans changement de signature,
  aucune lecture ;
- une taille qui diminue, ou un inode qui change (rotation, remplacement),
  déclenche une reconstruction complète ;
- seule une ligne terminée par ``\\n`` est consommée : une écriture en cours
  n'est jamais lue à moitié, et le reliquat est repris au prochain appel ;
- l'index est protégé par un verrou : le cockpit est un ``ThreadingHTTPServer``.

Seules les traces qui portent un ``task_id`` sont indexées, comme le faisait
le regroupement à la demande qu'il remplace.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
from pathlib import Path
from typing import Any

from grimoire.traces.schemas import TraceRecord

__all__ = ["TaskTraceIndex", "index_for", "traces_by_task"]

#: Signature d'un journal déjà consommé à cette taille et ce mtime.
_Signature = tuple[int, int]
#: Identité du fichier, pour détecter une rotation qui réécrit au même chemin.
_FileId = tuple[int, int]


class TaskTraceIndex:
    """Traces d'un ``traces.jsonl`` groupées par ``task_id``, rafraîchies par delta.

    Une instance par fichier (voir :func:`index_for`) : elle garde l'offset
    du dernier octet consommé et la signature ``(mtime_ns, size)`` qui a
    produit l'index courant.
    """

    def __init__(self, traces_file: Path) -> None:
        self._path = traces_file
        self._lock = threading.Lock()
        self._by_task: dict[str, list[TraceRecord]] = {}
        self._offset = 0
        self._size = 0
        self._signature: _Signature | None = None
        self._file_id: _FileId | None = None
        # Diagnostics — comptés sous verrou, lus par :meth:`stats` (tests et
        # observabilité du cockpit) ; ils ne participent à aucune décision.
        self._full_loads = 0
        self._incremental_loads = 0
        self._parsed_records = 0

    # ── Lecture publique ───────────────────────────────────────────────────

    def traces_by_task(self) -> dict[str, list[TraceRecord]]:
        """Les traces groupées par tâche — l'index à jour, sans relecture inutile.

        Rend une copie du dictionnaire et de ses listes : un appelant ne peut
        pas modifier l'index du processus. Les ``TraceRecord`` eux-mêmes sont
        partagés entre appels (ils sont lus, jamais édités par les vues).
        """
        with self._lock:
            self._refresh_locked()
            return {task_id: list(records) for task_id, records in self._by_task.items()}

    def stats(self) -> dict[str, int]:
        """Compteurs de travail : reconstructions, lectures incrémentales, traces parsées.

        ``parsed_records`` est cumulatif : après un ajout d'une ligne il ne
        progresse que de un, ce qui distingue un parse incrémental d'un parse
        complet — c'est la mesure que les tests utilisent.
        """
        with self._lock:
            return {
                "full_loads": self._full_loads,
                "incremental_loads": self._incremental_loads,
                "parsed_records": self._parsed_records,
            }

    # ── Rafraîchissement ───────────────────────────────────────────────────

    def _refresh_locked(self) -> None:
        """Met l'index à jour ; l'appelant tient :attr:`_lock`."""
        try:
            # fstat sur le descripteur effectivement lu : un remplacement entre
            # l'ouverture et la lecture ne fait pas indexer deux fichiers mêlés.
            with open(self._path, "rb") as fh:
                st = os.fstat(fh.fileno())
                if self._up_to_date_locked(st):
                    return
                self._consume_locked(fh, st)
        except FileNotFoundError:
            self._reset_locked()

    def _up_to_date_locked(self, st: os.stat_result) -> bool:
        return self._signature == (st.st_mtime_ns, st.st_size) and self._file_id == (st.st_dev, st.st_ino)

    def _reset_locked(self) -> None:
        """Le journal a disparu : oublier l'index, sans toucher aux compteurs."""
        self._by_task.clear()
        self._offset = 0
        self._size = 0
        self._signature = None
        self._file_id = None

    def _consume_locked(self, fh: Any, st: os.stat_result) -> None:
        """Consomme les octets complets ajoutés depuis ``self._offset``."""
        file_id = (st.st_dev, st.st_ino)
        rebuild = self._signature is None or self._file_id != file_id or st.st_size < self._size
        start = 0
        if rebuild:
            self._by_task.clear()
            self._offset = 0
            self._full_loads += 1
        else:
            start = self._offset
            self._incremental_loads += 1
        fh.seek(start)
        data = fh.read()
        last_newline = data.rfind(b"\n")
        if last_newline >= 0:
            self._index_lines(data[:last_newline])
            # Ce qui suit le dernier saut de ligne est une écriture en cours :
            # on ne l'avance pas, il sera relu avec sa suite.
            self._offset = start + last_newline + 1
        self._signature = (st.st_mtime_ns, st.st_size)
        self._size = st.st_size
        self._file_id = file_id

    def _index_lines(self, block: bytes) -> None:
        for raw in block.split(b"\n"):
            if not raw.strip():
                continue
            with contextlib.suppress(json.JSONDecodeError, KeyError):
                trace = TraceRecord.from_dict(json.loads(raw))
                self._parsed_records += 1
                if trace.task_id:
                    self._by_task.setdefault(trace.task_id, []).append(trace)


_INDEXES: dict[Path, TaskTraceIndex] = {}
_INDEXES_LOCK = threading.Lock()


def index_for(traces_file: Path) -> TaskTraceIndex:
    """L'index du processus pour *traces_file* — un seul par fichier résolu."""
    key = traces_file.resolve()
    with _INDEXES_LOCK:
        index = _INDEXES.get(key)
        if index is None:
            index = TaskTraceIndex(key)
            _INDEXES[key] = index
        return index


def traces_by_task(traces_file: Path) -> dict[str, list[TraceRecord]]:
    """``task_id`` → traces, via l'index partagé du processus."""
    return index_for(traces_file).traces_by_task()
